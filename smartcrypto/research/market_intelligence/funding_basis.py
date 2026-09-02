"""Causal mark/index basis and funding research features."""

from __future__ import annotations

import math
from datetime import datetime
from statistics import fmean, pstdev
from typing import Iterable

from .contracts import FeatureVector, MarketEvent


def build_basis_funding_features(
    events: Iterable[MarketEvent],
    *,
    decision_time_utc: datetime,
    extremeness_min_observations: int,
) -> FeatureVector:
    marks = sorted(
        (item for item in events if item.event_type == "mark_price"),
        key=lambda item: (item.event_time_utc, item.event_id),
    )
    if not marks:
        return {}
    latest = marks[-1]
    mark_price = _positive(latest.payload.get("mark_price"))
    index_price = _positive(latest.payload.get("index_price"))
    premium_index = _finite(latest.payload.get("premium_index"))
    funding_rate = _finite(latest.payload.get("funding_rate"))
    funding_kind = str(latest.payload.get("funding_rate_kind") or "").strip().lower()
    funding_interval_hours = _positive(latest.payload.get("funding_interval_hours"))
    next_funding = _parse_utc(latest.payload.get("next_funding_time_utc"))

    basis_abs: float | None = None
    basis_bps: float | None = None
    if mark_price is not None and index_price is not None:
        basis_abs = mark_price - index_price
        basis_bps = (basis_abs / index_price) * 10_000.0

    predicted: float | None = None
    realized: float | None = None
    if funding_rate is not None:
        if funding_kind == "predicted":
            predicted = funding_rate
        elif funding_kind == "realized":
            realized = funding_rate
        elif funding_kind:
            raise ValueError("funding_rate_kind_invalid")
        else:
            raise ValueError("funding_rate_kind_required")

    annualized: float | None = None
    active_rate = predicted if predicted is not None else realized
    if active_rate is not None and funding_interval_hours is not None:
        annualized = active_rate * (24.0 / funding_interval_hours) * 365.0

    time_to_funding: int | None = None
    if next_funding is not None:
        time_to_funding = max(0, int((next_funding - decision_time_utc).total_seconds()))

    extremeness = _funding_extremeness(
        marks, active_rate, funding_kind, extremeness_min_observations
    )
    direction: int | None = None
    if active_rate is not None:
        direction = 1 if active_rate > 0 else -1 if active_rate < 0 else 0

    return {
        "mark_price": mark_price,
        "index_price": index_price,
        "mark_index_basis_abs": basis_abs,
        "mark_index_basis_bps": basis_bps,
        "perp_index_basis_bps": basis_bps,
        "premium_bps": premium_index * 10_000.0 if premium_index is not None else None,
        "funding_rate_predicted": predicted,
        "funding_rate_realized": realized,
        "funding_annualized_research": annualized,
        "funding_direction": direction,
        "funding_extremeness_causal": extremeness,
        "time_to_next_funding_seconds": time_to_funding,
    }


def _funding_extremeness(
    events: list[MarketEvent],
    active_rate: float | None,
    active_kind: str,
    min_observations: int,
) -> float | None:
    if active_rate is None:
        return None
    rates: list[float] = []
    for event in events:
        value = _finite(event.payload.get("funding_rate"))
        kind = str(event.payload.get("funding_rate_kind") or "").strip().lower()
        if value is not None and kind == active_kind:
            rates.append(value)
    if len(rates) < min_observations:
        return None
    std = pstdev(rates)
    if std == 0:
        return 0.0
    return (active_rate - fmean(rates)) / std


def _parse_utc(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("next_funding_time_invalid") from exc
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None:
        raise ValueError("next_funding_time_must_be_utc")
    if offset.total_seconds() != 0:
        raise ValueError("next_funding_time_must_be_utc")
    return parsed


def _finite(value: object) -> float | None:
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
    return parsed if math.isfinite(parsed) else None


def _positive(value: object) -> float | None:
    parsed = _finite(value)
    return parsed if parsed is not None and parsed > 0 else None
