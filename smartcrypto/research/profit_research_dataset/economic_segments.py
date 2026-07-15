"""Economic segmentation and non-operational BTC block hypothesis."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any, Final

import numpy as np
import pandas as pd


BOOTSTRAP_MIN_SAMPLE: Final = 20
BOOTSTRAP_ITERATIONS: Final = 500


def financial_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty or "net_pnl" not in frame.columns:
        return _empty_metrics()
    ordered = frame.sort_values(["close_time_utc", "stable_trade_id"])
    pnl = pd.to_numeric(ordered["net_pnl"], errors="coerce").dropna()
    if pnl.empty:
        return _empty_metrics()
    winners = pnl[pnl > 0]
    losers = pnl[pnl < 0]
    gross_profit = float(winners.sum())
    gross_loss_abs = float(-losers.sum())
    cumulative = pnl.cumsum()
    peak = cumulative.cummax().clip(lower=0.0)
    drawdown = peak - cumulative
    fees = pd.to_numeric(ordered.get("fees"), errors="coerce")
    total_fees = float(fees.sum()) if fees.notna().any() else None
    confidence = bootstrap_expectancy_interval(pnl, seed_key="global")
    return {
        "trade_count": int(len(pnl)),
        "net_pnl": float(pnl.sum()),
        "average_pnl": float(pnl.mean()),
        "median_pnl": float(pnl.median()),
        "win_rate": float((pnl > 0).mean()),
        "profit_factor": gross_profit / gross_loss_abs if gross_loss_abs > 0 else None,
        "gross_profit": gross_profit,
        "gross_loss": -gross_loss_abs,
        "max_drawdown": float(drawdown.max()),
        "fee_total": total_fees,
        "fee_to_gross_loss_ratio": (
            total_fees / gross_loss_abs
            if total_fees is not None and gross_loss_abs > 0
            else None
        ),
        "expectancy_per_trade": float(pnl.mean()),
        "bootstrap_expectancy_ci_low": confidence[0],
        "bootstrap_expectancy_ci_high": confidence[1],
        "sample_sufficiency_status": (
            "sufficient" if len(pnl) >= BOOTSTRAP_MIN_SAMPLE else "insufficient"
        ),
    }


def build_economic_segments(frame: pd.DataFrame) -> tuple[dict[str, Any], ...]:
    prepared = add_segmentation_buckets(frame)
    dimensions: Sequence[tuple[str, ...]] = (
        ("symbol",),
        ("side",),
        ("symbol", "side"),
        ("exit_reason",),
        ("duration_bucket",),
        ("entry_volatility_regime",),
        ("entry_trend_regime",),
        ("entry_momentum_bucket",),
        ("mfe_bucket",),
        ("mae_bucket",),
        ("retracement_bucket",),
        ("entry_hour_utc",),
        ("entry_day_of_week",),
        ("winner_to_loser_conversion",),
        ("fee_burden_bucket",),
    )
    result: list[dict[str, Any]] = []
    for keys in dimensions:
        if any(key not in prepared.columns for key in keys):
            continue
        grouped = prepared.groupby(list(keys), dropna=False, sort=True)
        for values, subset in grouped:
            value_tuple = values if isinstance(values, tuple) else (values,)
            metrics = financial_metrics(subset)
            seed_key = "|".join(f"{key}={value}" for key, value in zip(keys, value_tuple, strict=True))
            confidence = bootstrap_expectancy_interval(
                pd.to_numeric(subset["net_pnl"], errors="coerce").dropna(),
                seed_key=seed_key,
            )
            result.append(
                {
                    "segment_dimension": "x".join(keys),
                    "segment_value": "|".join(str(value) for value in value_tuple),
                    **metrics,
                    "bootstrap_expectancy_ci_low": confidence[0],
                    "bootstrap_expectancy_ci_high": confidence[1],
                }
            )
    return tuple(
        sorted(
            result,
            key=lambda item: (str(item["segment_dimension"]), str(item["segment_value"])),
        )
    )


def add_segmentation_buckets(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["entry_momentum_bucket"] = _fixed_bucket(
        output.get("entry_momentum_6"),
        boundaries=(-0.002, 0.002),
        labels=("negative", "neutral", "positive"),
    )
    output["mfe_bucket"] = _fixed_bucket(
        output.get("mfe_pct"),
        boundaries=(0.002, 0.01),
        labels=("low", "medium", "high"),
    )
    output["mae_bucket"] = _fixed_bucket(
        output.get("mae_pct"),
        boundaries=(-0.01, -0.002),
        labels=("severe", "moderate", "low"),
    )
    output["retracement_bucket"] = _fixed_bucket(
        output.get("retracement_pct_of_mfe"),
        boundaries=(0.25, 0.75),
        labels=("low", "medium", "high"),
    )
    output["fee_burden_bucket"] = _fixed_bucket(
        output.get("fee_burden"),
        boundaries=(0.05, 0.20),
        labels=("low", "medium", "high"),
    )
    return output


def evaluate_btc_block_hypothesis(frame: pd.DataFrame) -> dict[str, Any]:
    ordered = frame.sort_values(["close_time_utc", "stable_trade_id"]).reset_index(drop=True)
    baseline = financial_metrics(ordered)
    btc_mask = ordered["symbol"].eq("BTCUSDT")
    candidate = financial_metrics(ordered.loc[~btc_mask])
    split = max(1, int(len(ordered) * 0.70))
    in_sample = ordered.iloc[:split]
    out_of_sample = ordered.iloc[split:]
    in_btc = in_sample.loc[in_sample["symbol"].eq("BTCUSDT")]
    out_btc = out_of_sample.loc[out_of_sample["symbol"].eq("BTCUSDT")]
    in_removed = float(pd.to_numeric(in_btc["net_pnl"], errors="coerce").sum())
    out_removed = float(pd.to_numeric(out_btc["net_pnl"], errors="coerce").sum())
    removed = ordered.loc[btc_mask]
    removed_pnl = float(pd.to_numeric(removed["net_pnl"], errors="coerce").sum())
    opportunity_cost = float(
        pd.to_numeric(removed.loc[removed["net_pnl"].gt(0), "net_pnl"], errors="coerce").sum()
    )
    pnl_recovered = -removed_pnl
    stability = _subperiod_stability(ordered)
    classification = _btc_conclusion(
        total_removed_pnl=removed_pnl,
        in_sample_removed_pnl=in_removed,
        out_of_sample_removed_pnl=out_removed,
        blocked_count=int(btc_mask.sum()),
        expectancy_improved=(
            float(candidate["expectancy_per_trade"]) > float(baseline["expectancy_per_trade"])
        ),
        drawdown_not_worse=(
            float(candidate["max_drawdown"]) <= float(baseline["max_drawdown"])
        ),
    )
    return {
        "hypothesis": "block_btcusdt_entry_research_only",
        "operational_rule_created": False,
        "historical_total": baseline,
        "candidate_without_btc": candidate,
        "in_sample_removed_btc_net_pnl": in_removed,
        "out_of_sample_removed_btc_net_pnl": out_removed,
        "trades_removed": int(btc_mask.sum()),
        "pnl_recovered": pnl_recovered,
        "drawdown_reduced": float(baseline["max_drawdown"] - candidate["max_drawdown"]),
        "incremental_expectancy": float(
            candidate["expectancy_per_trade"] - baseline["expectancy_per_trade"]
        ),
        "benefit_per_trade_blocked": pnl_recovered / int(btc_mask.sum()) if btc_mask.any() else 0.0,
        "stability_by_subperiod": stability,
        "regime_dependency": _btc_regime_dependency(removed),
        "opportunity_cost": opportunity_cost,
        "conclusion": classification,
    }


def bootstrap_expectancy_interval(
    pnl: pd.Series,
    *,
    seed_key: str,
) -> tuple[float | None, float | None]:
    values = pnl.to_numpy(dtype=float)
    if len(values) < BOOTSTRAP_MIN_SAMPLE:
        return None, None
    seed = int(hashlib.sha256(seed_key.encode("utf-8")).hexdigest()[:8], 16)
    generator = np.random.default_rng(seed)
    means: np.ndarray = np.empty(BOOTSTRAP_ITERATIONS, dtype=float)
    for index in range(BOOTSTRAP_ITERATIONS):
        means[index] = float(generator.choice(values, size=len(values), replace=True).mean())
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def _btc_conclusion(
    *,
    total_removed_pnl: float,
    in_sample_removed_pnl: float,
    out_of_sample_removed_pnl: float,
    blocked_count: int,
    expectancy_improved: bool,
    drawdown_not_worse: bool,
) -> str:
    if blocked_count == 0 or total_removed_pnl >= 0:
        return "rejected"
    if (
        in_sample_removed_pnl < 0
        and out_of_sample_removed_pnl < 0
        and expectancy_improved
        and drawdown_not_worse
    ):
        return "supported"
    if in_sample_removed_pnl * out_of_sample_removed_pnl < 0:
        return "unstable"
    return "weak"


def _subperiod_stability(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    period = frame["close_time_utc"].dt.strftime("%Y-%m")
    rows = []
    for value in sorted(period.unique()):
        subset = frame.loc[period.eq(value)]
        btc = subset.loc[subset["symbol"].eq("BTCUSDT")]
        rows.append(
            {
                "subperiod": value,
                "btc_trade_count": int(len(btc)),
                "btc_net_pnl": float(pd.to_numeric(btc["net_pnl"], errors="coerce").sum()),
            }
        )
    return rows


def _btc_regime_dependency(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    rows = []
    for dimension in ("entry_trend_regime", "entry_volatility_regime"):
        if dimension not in frame.columns:
            continue
        for value, subset in frame.groupby(dimension, dropna=False, sort=True):
            rows.append(
                {
                    "dimension": dimension,
                    "value": str(value),
                    "trade_count": int(len(subset)),
                    "net_pnl": float(pd.to_numeric(subset["net_pnl"], errors="coerce").sum()),
                }
            )
    return rows


def _fixed_bucket(
    values: pd.Series | None,
    *,
    boundaries: tuple[float, float],
    labels: tuple[str, str, str],
) -> pd.Series:
    if values is None:
        return pd.Series(dtype="string")
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series("unknown", index=numeric.index, dtype="string")
    result = result.mask(numeric.le(boundaries[0]), labels[0])
    result = result.mask(numeric.gt(boundaries[0]) & numeric.le(boundaries[1]), labels[1])
    return result.mask(numeric.gt(boundaries[1]), labels[2])


def _empty_metrics() -> dict[str, Any]:
    return {
        "trade_count": 0,
        "net_pnl": 0.0,
        "average_pnl": 0.0,
        "median_pnl": 0.0,
        "win_rate": 0.0,
        "profit_factor": None,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "max_drawdown": 0.0,
        "fee_total": None,
        "fee_to_gross_loss_ratio": None,
        "expectancy_per_trade": 0.0,
        "bootstrap_expectancy_ci_low": None,
        "bootstrap_expectancy_ci_high": None,
        "sample_sufficiency_status": "insufficient",
    }
