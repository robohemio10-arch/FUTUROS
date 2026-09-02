"""Policy-neutral helpers for W8 execution simulations."""

from __future__ import annotations

from datetime import timedelta

from .contracts import ExecutionScenario, MarketSlice, Side


def submit_time(scenario: ExecutionScenario):
    return scenario.decision_time_utc + timedelta(milliseconds=scenario.submit_latency_ms)


def timeout_time(scenario: ExecutionScenario):
    return submit_time(scenario) + timedelta(seconds=scenario.timeout_seconds)


def quote_for_side(market: MarketSlice, side: Side) -> float:
    return market.best_ask if side == Side.BUY else market.best_bid


def same_side_quote(market: MarketSlice, side: Side) -> float:
    return market.best_bid if side == Side.BUY else market.best_ask
