"""Causal capital-hours calculations for W5 research."""

from __future__ import annotations

from .contracts import CandidateOpportunity, OpenPositionOpportunity


def required_capital_hours(candidate: CandidateOpportunity) -> float:
    return candidate.capital_required_usdt * candidate.expected_holding_seconds / 3600.0


def consumed_capital_hours(position: OpenPositionOpportunity) -> float:
    return position.capital_locked_usdt * position.position_age_seconds / 3600.0
