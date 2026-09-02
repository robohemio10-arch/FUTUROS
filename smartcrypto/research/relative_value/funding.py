"""Funding carry accounting for W7 research."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Literal

from .contracts import FundingObservation, require_utc


def expected_funding_intervals(
    observation: FundingObservation | None,
    *,
    decision_time_utc: datetime,
    holding_hours: float,
    interval_hours: float,
) -> int:
    """Count scheduled predicted funding events inside the research holding horizon.

    A realized funding record is historical evidence, not a forecast of a future
    payment.  A predicted record represents the next known settlement timestamp;
    subsequent intervals use the same supplied rate only as an explicit research
    scenario assumption.
    """
    if holding_hours <= 0 or interval_hours <= 0:
        raise ValueError("funding_horizon_and_interval_must_be_positive")
    if observation is None or observation.rate_kind == "realized":
        return 0

    decision = require_utc(decision_time_utc)
    first_funding = observation.funding_time_utc
    if first_funding < decision:
        return 0

    horizon_end = decision + timedelta(hours=holding_hours)
    if first_funding > horizon_end:
        return 0

    interval_seconds = interval_hours * 3600.0
    remaining_seconds = (horizon_end - first_funding).total_seconds()
    return 1 + int(math.floor(remaining_seconds / interval_seconds))


def expected_funding_carry_bps(
    observation: FundingObservation | None,
    *,
    perp_side: Literal["long", "short"],
    decision_time_utc: datetime,
    holding_hours: float,
    interval_hours: float,
) -> float:
    """Return point-in-time scenario funding cashflow in bps.

    Positive funding means perp longs pay shorts.  Positive return means the
    modeled position receives funding.  No future funding event is synthesized
    from a realized historical funding record.
    """
    if observation is None:
        return 0.0
    intervals = expected_funding_intervals(
        observation,
        decision_time_utc=decision_time_utc,
        holding_hours=holding_hours,
        interval_hours=interval_hours,
    )
    signed = -1.0 if perp_side == "long" else 1.0
    return signed * observation.funding_rate * 10_000.0 * intervals


def funding_carry_direction(
    funding_rate: float,
) -> Literal["LONG_SPOT_SHORT_PERP", "SHORT_SPOT_LONG_PERP"] | None:
    if funding_rate > 0:
        return "LONG_SPOT_SHORT_PERP"
    if funding_rate < 0:
        return "SHORT_SPOT_LONG_PERP"
    return None
