"""Causal open-interest feature extraction."""

from __future__ import annotations

import math
from typing import Iterable

from .contracts import FeatureVector, MarketEvent


ParsedOpenInterest = tuple[MarketEvent, float, float | None]


def build_open_interest_features(
    events: Iterable[MarketEvent],
    *,
    flow_imbalance: float | None,
) -> FeatureVector:
    rows = sorted(
        (item for item in events if item.event_type == "open_interest"),
        key=lambda item: (item.event_time_utc, item.event_id),
    )
    parsed: list[ParsedOpenInterest] = []
    for item in rows:
        open_interest = _positive(item.payload.get("open_interest"))
        if open_interest is None:
            continue
        reference_price = _positive(item.payload.get("reference_price"))
        parsed.append((item, open_interest, reference_price))

    if not parsed:
        return {}

    latest_event, latest_oi, latest_price = parsed[-1]
    previous = parsed[-2] if len(parsed) >= 2 else None

    oi_delta: float | None = None
    oi_pct_change: float | None = None
    oi_velocity: float | None = None
    price_oi_interaction: float | None = None

    if previous is not None:
        previous_event, previous_oi, previous_price = previous
        oi_delta = latest_oi - previous_oi
        oi_pct_change = oi_delta / previous_oi
        elapsed = (latest_event.event_time_utc - previous_event.event_time_utc).total_seconds()
        oi_velocity = oi_delta / elapsed if elapsed > 0 else None
        if latest_price is not None and previous_price is not None:
            price_return = (latest_price - previous_price) / previous_price
            price_oi_interaction = price_return * oi_pct_change

    flow_oi_interaction = (
        flow_imbalance * oi_pct_change
        if flow_imbalance is not None and oi_pct_change is not None
        else None
    )
    return {
        "oi": latest_oi,
        "oi_delta": oi_delta,
        "oi_pct_change": oi_pct_change,
        "oi_velocity": oi_velocity,
        "price_oi_interaction": price_oi_interaction,
        "flow_oi_interaction": flow_oi_interaction,
    }


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
