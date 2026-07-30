"""Canonical net-of-cost trade metrics and segmented OOS diagnostics."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .contracts import AcceptanceGates


def compute_trade_metrics(frame: pd.DataFrame, *, annualization_factor: int = 365) -> dict[str, Any]:
    if frame.empty or "net_pnl" not in frame.columns:
        return empty_metrics()
    pnl = pd.to_numeric(frame["net_pnl"], errors="coerce").dropna().astype(float)
    if pnl.empty:
        return empty_metrics()
    gross = pd.to_numeric(frame.get("gross_pnl", pnl), errors="coerce").fillna(0.0).astype(float)
    costs = pd.to_numeric(frame.get("total_cost", 0.0), errors="coerce")
    if not isinstance(costs, pd.Series):
        costs = pd.Series(float(costs), index=frame.index, dtype=float)
    costs = costs.fillna(0.0).astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    mean = float(pnl.mean())
    std = float(pnl.std(ddof=1)) if len(pnl) > 1 else 0.0
    downside = pnl[pnl < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sharpe = mean / std * math.sqrt(annualization_factor) if std > 0 else None
    sortino = mean / downside_std * math.sqrt(annualization_factor) if downside_std > 0 else None
    drawdown = drawdown_metrics(pnl.to_numpy(dtype=float))
    calmar = float(pnl.sum()) / drawdown["maximum_drawdown"] if drawdown["maximum_drawdown"] > 0 else None
    average_win = float(wins.mean()) if not wins.empty else 0.0
    average_loss = float(losses.mean()) if not losses.empty else 0.0
    payoff_ratio = average_win / abs(average_loss) if average_loss < 0 else None

    maker_count = int((frame.get("liquidity_role", pd.Series(dtype=str)).astype(str).str.lower() == "maker").sum()) if "liquidity_role" in frame.columns else 0
    taker_count = int((frame.get("liquidity_role", pd.Series(dtype=str)).astype(str).str.lower() == "taker").sum()) if "liquidity_role" in frame.columns else 0
    executed_count = max(1, maker_count + taker_count)

    return {
        "trade_count": int(len(pnl)),
        "win_count": int((pnl > 0).sum()),
        "loss_count": int((pnl < 0).sum()),
        "hit_rate": float((pnl > 0).mean()),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "gross_pnl": float(gross.sum()),
        "net_pnl": float(pnl.sum()),
        "expectancy": mean,
        "profit_factor": finite_or_none(profit_factor),
        "sharpe": finite_or_none(sharpe),
        "sortino": finite_or_none(sortino),
        "calmar": finite_or_none(calmar),
        "maximum_drawdown": drawdown["maximum_drawdown"],
        "average_drawdown": drawdown["average_drawdown"],
        "drawdown_duration": drawdown["maximum_drawdown_duration"],
        "payoff_ratio": finite_or_none(payoff_ratio),
        "average_win": average_win,
        "average_loss": average_loss,
        "turnover": _column_sum(frame, "turnover"),
        "maker_ratio": maker_count / executed_count,
        "taker_ratio": taker_count / executed_count,
        "total_fees": _column_sum(frame, "trading_fee"),
        "total_funding": _column_sum(frame, "funding_fee"),
        "slippage": _column_sum(frame, "slippage_cost"),
        "market_impact": _column_sum(frame, "market_impact_cost"),
        "total_costs": float(costs.sum()),
        "liquidation_count": int(_column_sum(frame, "liquidation_count")),
        "exposure": _column_mean(frame, "exposure"),
        "concentration": _column_max(frame, "concentration"),
        "cost_drag_ratio": cost_drag_ratio(float(costs.sum()), float(gross.abs().sum())),
    }


def drawdown_metrics(values: Sequence[float]) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return {"maximum_drawdown": 0.0, "average_drawdown": 0.0, "maximum_drawdown_duration": 0}
    equity = np.cumsum(array)
    peaks = np.maximum.accumulate(np.concatenate(([0.0], equity)))[:-1]
    drawdowns = np.maximum(peaks - equity, 0.0)
    max_duration = 0
    current = 0
    for value in drawdowns:
        if value > 0:
            current += 1
            max_duration = max(max_duration, current)
        else:
            current = 0
    positive = drawdowns[drawdowns > 0]
    return {
        "maximum_drawdown": float(drawdowns.max(initial=0.0)),
        "average_drawdown": float(positive.mean()) if positive.size else 0.0,
        "maximum_drawdown_duration": int(max_duration),
    }


def segment_metrics(
    frame: pd.DataFrame,
    *,
    gates: AcceptanceGates,
    dimensions: Sequence[str] = (
        "symbol",
        "side",
        "regime",
        "volatility_bucket",
        "liquidity_bucket",
        "funding_bucket",
        "holding_period_bucket",
        "entry_score_bucket",
        "cost_bucket",
        "market_impact_bucket",
        "leverage_bucket",
    ),
) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    material_negative: list[str] = []
    worst_by_dimension: dict[str, dict[str, Any] | None] = {}

    for dimension in dimensions:
        if dimension not in frame.columns:
            worst_by_dimension[dimension] = None
            continue
        dimension_rows: list[dict[str, Any]] = []
        for value, group in frame.groupby(dimension, dropna=False, sort=True):
            metrics = compute_trade_metrics(group)
            sufficient = metrics["trade_count"] >= gates.minimum_trades_per_segment
            status = "PASS" if sufficient else "INSUFFICIENT_SAMPLE"
            negative = bool(
                sufficient
                and metrics["expectancy"] < gates.material_negative_segment_expectancy
            )
            segment_id = f"{dimension}={value}"
            record = {
                "segment_id": segment_id,
                "dimension": dimension,
                "value": str(value),
                "sample_size": metrics["trade_count"],
                "sample_status": status,
                "material_negative": negative,
                "metrics": metrics,
            }
            dimension_rows.append(record)
            segments.append(record)
            if negative:
                material_negative.append(segment_id)
        eligible = [row for row in dimension_rows if row["sample_status"] == "PASS"]
        worst_by_dimension[dimension] = (
            min(eligible, key=lambda row: float(row["metrics"]["expectancy"])) if eligible else None
        )

    return {
        "segments": segments,
        "material_negative_segments": sorted(material_negative),
        "material_negative_segment_count": len(material_negative),
        "worst_by_dimension": worst_by_dimension,
        "segment_gate_status": "blocked" if material_negative else "ok",
    }


def aggregate_fold_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    records = [dict(row) for row in rows]
    if not records:
        return {
            "fold_count": 0,
            "worst_fold": None,
            "median_fold": None,
            "weighted_aggregate": empty_metrics(),
            "unweighted_aggregate": {},
            "fold_dispersion": {},
        }
    valid = [row for row in records if row.get("metrics", {}).get("trade_count", 0) > 0]
    if not valid:
        return {
            "fold_count": len(records),
            "worst_fold": None,
            "median_fold": None,
            "weighted_aggregate": empty_metrics(),
            "unweighted_aggregate": {},
            "fold_dispersion": {},
        }
    weights = np.asarray([row["metrics"]["trade_count"] for row in valid], dtype=float)
    expectancies = np.asarray([row["metrics"]["expectancy"] for row in valid], dtype=float)
    net_pnls = np.asarray([row["metrics"]["net_pnl"] for row in valid], dtype=float)
    profit_factors = np.asarray([
        row["metrics"]["profit_factor"] if row["metrics"]["profit_factor"] is not None else 0.0
        for row in valid
    ], dtype=float)
    weighted_expectancy = float(np.average(expectancies, weights=weights))
    worst = min(valid, key=lambda row: float(row["metrics"]["expectancy"]))
    median = sorted(valid, key=lambda row: float(row["metrics"]["expectancy"]))[len(valid) // 2]
    return {
        "fold_count": len(records),
        "valid_fold_count": len(valid),
        "blocked_fold_count": len(records) - len(valid),
        "worst_fold": worst,
        "median_fold": median,
        "weighted_aggregate": {
            "trade_count": int(weights.sum()),
            "net_pnl": float(net_pnls.sum()),
            "expectancy": weighted_expectancy,
            "profit_factor": float(np.average(profit_factors, weights=weights)),
        },
        "unweighted_aggregate": {
            "net_pnl_mean": float(net_pnls.mean()),
            "expectancy_mean": float(expectancies.mean()),
            "profit_factor_mean": float(profit_factors.mean()),
        },
        "fold_dispersion": {
            "net_pnl_std": float(net_pnls.std(ddof=1)) if len(net_pnls) > 1 else 0.0,
            "expectancy_std": float(expectancies.std(ddof=1)) if len(expectancies) > 1 else 0.0,
            "profit_factor_std": float(profit_factors.std(ddof=1)) if len(profit_factors) > 1 else 0.0,
        },
    }


def cost_drag_ratio(total_cost: float, gross_absolute_pnl: float) -> float | None:
    if gross_absolute_pnl <= 0:
        return None
    return float(total_cost / gross_absolute_pnl)


def empty_metrics() -> dict[str, Any]:
    return {
        "trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "hit_rate": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "gross_pnl": 0.0,
        "net_pnl": 0.0,
        "expectancy": 0.0,
        "profit_factor": 0.0,
        "sharpe": None,
        "sortino": None,
        "calmar": None,
        "maximum_drawdown": 0.0,
        "average_drawdown": 0.0,
        "drawdown_duration": 0,
        "payoff_ratio": None,
        "average_win": 0.0,
        "average_loss": 0.0,
        "turnover": 0.0,
        "maker_ratio": 0.0,
        "taker_ratio": 0.0,
        "total_fees": 0.0,
        "total_funding": 0.0,
        "slippage": 0.0,
        "market_impact": 0.0,
        "total_costs": 0.0,
        "liquidation_count": 0,
        "exposure": 0.0,
        "concentration": 0.0,
        "cost_drag_ratio": None,
    }


def finite_or_none(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _column_sum(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0.0).sum())


def _column_mean(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns:
        return 0.0
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else 0.0


def _column_max(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns:
        return 0.0
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.max()) if not values.empty else 0.0
