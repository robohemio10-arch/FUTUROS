"""Small deterministic helpers shared by the financial objective modules."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd


def _trade_key_series(frame: pd.DataFrame) -> pd.Series:
    for column in (
        "order_id",
        "trade_id",
        "stable_trade_id",
        "event_id",
        "source_trade_id",
    ):
        if column in frame.columns:
            return frame[column].map(_normalize_trade_key)
    return pd.Series([""] * len(frame), index=frame.index, dtype="string")


def _row_trade_key(row: Mapping[str, Any]) -> str:
    for key in (
        "order_id",
        "trade_id",
        "stable_trade_id",
        "event_id",
        "source_trade_id",
    ):
        value = row.get(key)
        if value is not None and str(value).strip():
            return _normalize_trade_key(value)
    return ""


def _normalize_trade_key(value: Any) -> str:
    raw = str(value).strip()
    if raw.startswith("outcome_order_id_"):
        raw = raw.removeprefix("outcome_order_id_")
    if raw.startswith("freqtrade-paper-"):
        raw = raw.removeprefix("freqtrade-paper-")
    parsed = _finite(raw)
    if parsed is not None and parsed.is_integer():
        return str(int(parsed))
    return raw


def _numeric_trade_id(value: Any) -> int | None:
    parsed = _finite(_normalize_trade_key(value))
    return int(parsed) if parsed is not None and parsed.is_integer() else None


def _first_finite(*values: Any) -> float | None:
    for value in values:
        parsed = _finite(value)
        if parsed is not None:
            return parsed
    return None


def _finite(value: Any) -> float | None:
    if value is None or value is pd.NA or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _mean_or_none(values: Sequence[float]) -> float | None:
    return float(sum(values) / len(values)) if values else None


def _mean_numeric(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns or frame.empty:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else None


def _sum_pnl(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    return float(
        pd.to_numeric(frame["net_pnl"], errors="coerce").fillna(0.0).sum()
    )


def _minimum_trades(total: int) -> int:
    return min(total, max(6, int(math.ceil(total * 0.10))))


def _pf_improved(candidate: float | None, baseline: float | None) -> bool:
    if candidate is None:
        return baseline is None
    if baseline is None:
        return True
    return candidate >= baseline


def _sort_float(value: Any) -> float:
    parsed = _finite(value)
    return parsed if parsed is not None else -math.inf


def _slug(value: Any) -> str:
    text = str(value).strip().lower()
    normalized = "".join(
        character if character.isalnum() else "_" for character in text
    ).strip("_")
    return normalized or "unknown"
