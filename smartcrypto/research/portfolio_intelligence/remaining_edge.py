"""Point-in-time remaining-edge validation for open positions."""

from __future__ import annotations

from datetime import datetime

from .capital_hours import consumed_capital_hours
from .contracts import OpenPositionOpportunity, OpenPositionView


def build_open_position_view(
    position: OpenPositionOpportunity,
    decision_time_utc: datetime,
) -> OpenPositionView:
    errors = position.point_in_time_errors(decision_time_utc)
    point_in_time_valid = not errors
    if position.remaining_ev is None:
        remaining_ev_status = "SOURCE_MISSING"
        remaining_ev = None
    elif errors:
        remaining_ev_status = "INVALID_POINT_IN_TIME"
        remaining_ev = None
    else:
        remaining_ev_status = "AVAILABLE"
        remaining_ev = position.remaining_ev.value_usdt
    return OpenPositionView(
        position_id=position.position_id,
        symbol=position.symbol,
        side=position.side,
        strategy_id=position.strategy_id,
        remaining_ev_usdt=remaining_ev,
        remaining_ev_status=remaining_ev_status,
        capital_locked_usdt=position.capital_locked_usdt,
        capital_hours_consumed=consumed_capital_hours(position),
        position_age_seconds=position.position_age_seconds,
        point_in_time_valid=point_in_time_valid,
        point_in_time_errors=errors,
        source_hash=position.source_hash,
    )
