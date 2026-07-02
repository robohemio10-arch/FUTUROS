"""Deterministic no-training financial baselines for walk-forward evidence."""

from __future__ import annotations

import hashlib
from typing import Any

import pandas as pd

from .split_schema import DEFAULT_BASELINE_SEED


def build_baseline_summary(frame: pd.DataFrame, *, seed: int = DEFAULT_BASELINE_SEED) -> dict[str, Any]:
    """Build deterministic financial baselines without fitting a model."""

    if frame.empty:
        return {
            "baseline_status": "blocked",
            "no_trade_expected_value": 0.0,
            "random_deterministic_expected_value": 0.0,
            "always_long_expected_value": 0.0,
            "always_short_expected_value": 0.0,
            "always_allow_expected_value": 0.0,
            "always_block_expected_value": 0.0,
            "baseline_seed": int(seed),
            "baseline_row_count": 0,
        }

    ev = expected_value_series(frame)
    side = frame["side"].astype("string").str.lower() if "side" in frame.columns else pd.Series("", index=frame.index)
    random_mask = pd.Series([deterministic_coin(row, seed=seed) for row in frame.to_dict(orient="records")], index=frame.index)
    return {
        "baseline_status": "ok",
        "no_trade_expected_value": 0.0,
        "random_deterministic_expected_value": rounded_sum(ev[random_mask]),
        "always_long_expected_value": rounded_sum(ev[side == "long"]),
        "always_short_expected_value": rounded_sum(ev[side == "short"]),
        "always_allow_expected_value": rounded_sum(ev),
        "always_block_expected_value": 0.0,
        "baseline_seed": int(seed),
        "baseline_row_count": int(len(frame)),
    }


def expected_value_series(frame: pd.DataFrame) -> pd.Series:
    for column in ("target_expected_value_component", "net_pnl", "target_net_pnl"):
        if column in frame.columns:
            return pd.to_numeric(frame[column], errors="coerce").fillna(0.0).astype(float)
    return pd.Series(0.0, index=frame.index)


def deterministic_coin(row: dict[str, Any], *, seed: int) -> bool:
    key = "|".join(
        str(row.get(column, ""))
        for column in ("order_id", "event_id", "trade_id", "open_time_utc", "symbol_norm", "side")
    )
    digest = hashlib.sha256(f"{seed}|{key}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 2 == 0


def rounded_sum(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    return round(float(pd.to_numeric(series, errors="coerce").fillna(0.0).sum()), 10)
