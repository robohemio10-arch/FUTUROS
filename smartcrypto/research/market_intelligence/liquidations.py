"""Causal public liquidation event aggregation."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Iterable

from .contracts import FeatureVector, MarketEvent


def build_liquidation_features(
    events: Iterable[MarketEvent],
    *,
    decision_time_utc: datetime,
    window_seconds: int,
) -> FeatureVector:
    cutoff = decision_time_utc - timedelta(seconds=window_seconds)
    rows = sorted(
        (
            item
            for item in events
            if item.event_type == "liquidation"
            and cutoff < item.event_time_utc <= decision_time_utc
        ),
        key=lambda item: (item.event_time_utc, item.event_id),
    )
    long_notional = 0.0
    short_notional = 0.0
    valid_count = 0
    for item in rows:
        side = str(item.payload.get("side") or "").strip().upper()
        if side not in {"LONG", "SHORT"}:
            continue
        notional = _notional(item.payload)
        if notional is None:
            continue
        valid_count += 1
        if side == "LONG":
            long_notional += notional
        else:
            short_notional += notional
    total = long_notional + short_notional
    return {
        "long_liquidation_notional": long_notional,
        "short_liquidation_notional": short_notional,
        "net_liquidation_pressure": short_notional - long_notional,
        "liquidation_count": valid_count,
        "liquidation_intensity": valid_count / float(window_seconds),
        "liquidation_imbalance": (
            (short_notional - long_notional) / total if total > 0 else None
        ),
        "window_seconds": window_seconds,
    }


def _notional(payload: dict[str, object]) -> float | None:
    direct = _positive(payload.get("notional"))
    if direct is not None:
        return direct
    price = _positive(payload.get("price"))
    quantity = _positive(payload.get("quantity", payload.get("qty")))
    if price is None or quantity is None:
        return None
    return price * quantity


def _positive(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed
