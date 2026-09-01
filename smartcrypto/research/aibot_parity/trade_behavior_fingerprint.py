"""Behavioral fingerprint and rolling diagnostics for closed AIBOT trades."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.analysis.paper_financial_performance import max_consecutive
from smartcrypto.research.quant_validation_strategy_factory_v2.metrics import (
    drawdown_metrics,
)

from .contracts import safety_flags


ROLLING_WINDOWS = ("7D", "30D", "90D")


def compute_behavior_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    ordered = _ordered_metric_frame(frame)
    pnl = ordered["pnl_net"] if not ordered.empty else pd.Series(dtype=float)
    durations = pd.to_numeric(ordered.get("duration_seconds"), errors="coerce").dropna()
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    breakeven = pnl[pnl == 0]
    gross_profit = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = float(-losses.sum()) if not losses.empty else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    average_win = float(wins.mean()) if not wins.empty else None
    average_loss = float(losses.mean()) if not losses.empty else None
    payoff = (
        average_win / abs(average_loss)
        if average_win is not None and average_loss is not None and average_loss != 0.0
        else None
    )
    drawdown = drawdown_metrics(pnl.to_numpy(dtype=float))
    trade_count = int(len(pnl))
    return {
        "source_row_count": int(len(frame)),
        "trade_count": trade_count,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "breakeven": int(len(breakeven)),
        "win_rate": float(len(wins) / trade_count) if trade_count else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_pnl": float(pnl.sum()) if trade_count else 0.0,
        "profit_factor": _finite_or_none(profit_factor),
        "expectancy": float(pnl.mean()) if trade_count else None,
        "payoff": _finite_or_none(payoff),
        "avg_pnl": float(pnl.mean()) if trade_count else None,
        "median_pnl": float(pnl.median()) if trade_count else None,
        "std_pnl": float(pnl.std(ddof=1)) if trade_count > 1 else 0.0 if trade_count else None,
        "max_drawdown": float(drawdown["maximum_drawdown"]),
        "max_losing_streak": max_consecutive((pnl < 0).to_numpy()),
        "max_winning_streak": max_consecutive((pnl > 0).to_numpy()),
        "duration_unit": "seconds",
        "avg_duration": float(durations.mean()) if not durations.empty else None,
        "median_duration": float(durations.median()) if not durations.empty else None,
        "p90_duration": float(durations.quantile(0.90)) if not durations.empty else None,
        "p95_duration": float(durations.quantile(0.95)) if not durations.empty else None,
    }


def build_behavior_fingerprint(
    frame: pd.DataFrame,
    *,
    source_investment_id: str,
    source_batch_id: str,
) -> dict[str, Any]:
    global_metrics = compute_behavior_metrics(frame)
    segmentations = build_segmentations(frame)
    rolling = build_rolling_behavior(frame)
    shift_diagnostics = detect_distribution_shift_candidates(frame)
    return {
        "status": "ok" if global_metrics["trade_count"] > 0 else "blocked",
        "reason": (
            "behavior_fingerprint_generated"
            if global_metrics["trade_count"] > 0
            else "no_valid_pnl_rows"
        ),
        "source_investment_id": source_investment_id,
        "source_batch_id": source_batch_id,
        "global": global_metrics,
        "segmentations": segmentations,
        "rolling_summary": rolling["summary"],
        "distribution_shift_diagnostics": shift_diagnostics,
        "outcomes_used_for_benchmark_only": True,
        "training_performed": False,
        "safety_flags": safety_flags(),
    }


def build_segmentations(frame: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    prepared = frame.copy()
    open_time = pd.to_datetime(prepared["open_time_utc"], errors="coerce", utc=True)
    prepared["entry_hour_utc"] = open_time.dt.hour.astype("Int64")
    prepared["entry_day_of_week"] = open_time.dt.day_name()
    prepared["entry_month"] = open_time.dt.strftime("%Y-%m")
    prepared["duration_bucket"] = pd.cut(
        pd.to_numeric(prepared["duration_seconds"], errors="coerce"),
        bins=[-np.inf, 300, 900, 3600, 14400, np.inf],
        labels=["<=5m", "5m-15m", "15m-1h", "1h-4h", ">4h"],
        right=True,
    ).astype("string")
    dimensions: dict[str, Sequence[str]] = {
        "symbol": ("symbol",),
        "side": ("side",),
        "symbol_side": ("symbol", "side"),
        "hour_utc": ("entry_hour_utc",),
        "day_of_week": ("entry_day_of_week",),
        "month": ("entry_month",),
        "duration_bucket": ("duration_bucket",),
    }
    if prepared["exit_reason"].notna().any():
        dimensions["exit_reason"] = ("exit_reason",)
    return {
        name: _segment_records(prepared, columns)
        for name, columns in dimensions.items()
    }


def build_rolling_behavior(frame: pd.DataFrame) -> dict[str, Any]:
    ordered = _ordered_metric_frame(frame)
    if ordered.empty or ordered["close_time_utc"].isna().all():
        return {
            "status": "blocked",
            "reason": "no_valid_close_time_and_pnl_rows",
            "summary": {window: None for window in ROLLING_WINDOWS},
            "time_series": {window: [] for window in ROLLING_WINDOWS},
        }
    timed = ordered.dropna(subset=["close_time_utc"]).copy()
    timed = timed.sort_values(["close_time_utc", "source_row_number"], kind="mergesort")
    timed = timed.set_index("close_time_utc", drop=False)
    pnl = timed["pnl_net"].astype(float)
    cumulative = pnl.cumsum()
    peaks = np.maximum.accumulate(np.concatenate(([0.0], cumulative.to_numpy(dtype=float))))[1:]
    point_drawdown = pd.Series(peaks - cumulative.to_numpy(dtype=float), index=timed.index)
    time_series: dict[str, list[dict[str, Any]]] = {}
    summary: dict[str, dict[str, Any] | None] = {}
    for window in ROLLING_WINDOWS:
        rolling = pnl.rolling(window, min_periods=1)
        positive = pnl.clip(lower=0).rolling(window, min_periods=1).sum()
        negative = (-pnl.clip(upper=0)).rolling(window, min_periods=1).sum()
        profit_factor = positive.div(negative.where(negative.gt(0)))
        records_frame = pd.DataFrame(
            {
                "source_row_number": timed["source_row_number"].astype(int),
                "cumulative_pnl": cumulative,
                "rolling_expectancy": rolling.mean(),
                "rolling_profit_factor": profit_factor,
                "rolling_win_rate": pnl.gt(0).astype(float).rolling(window, min_periods=1).mean(),
                "rolling_trade_count": rolling.count().astype(int),
                "rolling_drawdown": point_drawdown.rolling(window, min_periods=1).max(),
            },
            index=timed.index,
        )
        records = [
            {
                "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
                "source_row_number": int(row.source_row_number),
                "cumulative_pnl": float(row.cumulative_pnl),
                "rolling_expectancy": float(row.rolling_expectancy),
                "rolling_profit_factor": _finite_or_none(row.rolling_profit_factor),
                "rolling_win_rate": float(row.rolling_win_rate),
                "rolling_trade_count": int(row.rolling_trade_count),
                "rolling_drawdown": float(row.rolling_drawdown),
            }
            for timestamp, row in records_frame.iterrows()
        ]
        time_series[window] = records
        summary[window] = records[-1] if records else None
    return {
        "status": "ok",
        "reason": "rolling_behavior_generated",
        "rolling_drawdown_semantics": "maximum_global_equity_drawdown_observed_inside_window",
        "summary": summary,
        "time_series": time_series,
    }


def detect_distribution_shift_candidates(frame: pd.DataFrame) -> dict[str, Any]:
    ordered = _ordered_metric_frame(frame)
    if len(ordered) < 40:
        return {
            "status": "insufficient_sample",
            "candidate_labels": [],
            "first_half_trade_count": int(len(ordered) // 2),
            "second_half_trade_count": int(len(ordered) - len(ordered) // 2),
        }
    midpoint = len(ordered) // 2
    first = ordered.iloc[:midpoint]
    second = ordered.iloc[midpoint:]
    first_metrics = compute_behavior_metrics(first)
    second_metrics = compute_behavior_metrics(second)
    pooled_std = float(ordered["pnl_net"].std(ddof=1)) if len(ordered) > 1 else 0.0
    expectancy_delta = float(second["pnl_net"].mean() - first["pnl_net"].mean())
    first_win_rate = float(first["pnl_net"].gt(0).mean())
    second_win_rate = float(second["pnl_net"].gt(0).mean())
    win_rate_delta = second_win_rate - first_win_rate
    material = abs(expectancy_delta) > max(1e-12, pooled_std * 0.5) or abs(win_rate_delta) >= 0.10
    return {
        "status": "candidate_detected" if material else "no_material_candidate_detected",
        "candidate_labels": (
            ["distribution_shift_candidate", "behavioral_break_candidate"] if material else []
        ),
        "automatic_strategy_change_claimed": False,
        "split_method": "chronological_equal_count_halves",
        "first_half": first_metrics,
        "second_half": second_metrics,
        "expectancy_delta": expectancy_delta,
        "win_rate_delta": win_rate_delta,
        "materiality_policy": {
            "absolute_expectancy_delta_gt_half_pooled_std": True,
            "absolute_win_rate_delta_gte": 0.10,
        },
    }


def _ordered_metric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "pnl_net" not in frame.columns:
        return pd.DataFrame(columns=frame.columns)
    prepared = frame.copy()
    prepared["pnl_net"] = pd.to_numeric(prepared["pnl_net"], errors="coerce")
    prepared = prepared.loc[np.isfinite(prepared["pnl_net"].to_numpy(dtype=float, na_value=np.nan))]
    if "close_time_utc" in prepared.columns:
        prepared["close_time_utc"] = pd.to_datetime(
            prepared["close_time_utc"], errors="coerce", utc=True
        )
        prepared = prepared.sort_values(
            ["close_time_utc", "source_row_number"],
            kind="mergesort",
            na_position="last",
        )
    return prepared.reset_index(drop=True)


def _segment_records(frame: pd.DataFrame, columns: Sequence[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouper: str | list[str] = columns[0] if len(columns) == 1 else list(columns)
    for key, group in frame.groupby(grouper, dropna=False, sort=True, observed=False):
        values = key if isinstance(key, tuple) else (key,)
        label = " | ".join(
            f"{column}={'UNKNOWN' if pd.isna(value) else value}"
            for column, value in zip(columns, values, strict=True)
        )
        rows.append({"segment": label, **compute_behavior_metrics(group)})
    return rows


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
