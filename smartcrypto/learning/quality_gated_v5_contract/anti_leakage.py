"""Fail-closed feature-name and temporal anti-leakage checks."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .contracts import LEAKAGE_EXACT_COLUMNS, LEAKAGE_PREFIXES


def is_forbidden_feature_name(name: str) -> bool:
    normalized = str(name).strip().lower()
    if normalized in LEAKAGE_EXACT_COLUMNS:
        return True
    if normalized.startswith(LEAKAGE_PREFIXES):
        return True
    if "reported_pnl" in normalized:
        return True
    if normalized.endswith("_pnl") or normalized.endswith("_pnl_usdt"):
        return True
    return False


def audit_feature_names(feature_names: list[str] | tuple[str, ...]) -> dict[str, Any]:
    forbidden = sorted({name for name in feature_names if is_forbidden_feature_name(name)})
    return {
        "status": "blocked" if forbidden else "ok",
        "forbidden_feature_count": len(forbidden),
        "forbidden_features": forbidden,
        "block_reasons": ["BLOCKED_FEATURE_LEAKAGE"] if forbidden else [],
    }


def temporal_leakage_reasons(
    freshness_row: pd.Series | dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    for timeframe in ("1m", "5m"):
        prefix = f"snapshot_{timeframe}"
        if bool(freshness_row.get(f"{prefix}_is_future", False)):
            reasons.append(f"BLOCKED_FUTURE_{timeframe.upper()}_SNAPSHOT")
        if bool(freshness_row.get(f"{prefix}_is_in_progress", False)):
            reasons.append(f"BLOCKED_IN_PROGRESS_{timeframe.upper()}_SNAPSHOT")
    return sorted(set(reasons))


def audit_temporal_frame(freshness: pd.DataFrame) -> pd.DataFrame:
    records = []
    for _, row in freshness.iterrows():
        reasons = temporal_leakage_reasons(row)
        records.append(
            {
                "temporal_leakage_status": "blocked" if reasons else "ok",
                "temporal_leakage_block_reasons": reasons,
            }
        )
    return pd.DataFrame(records, index=freshness.index)
