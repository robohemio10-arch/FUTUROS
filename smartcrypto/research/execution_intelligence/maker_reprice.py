"""Deterministic maker-reprice simulation for W8 research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .contracts import ExecutionScenario, FillRecord, LiquidityRole
from .fill_model import (
    market_slice_available_at,
    passive_fill_capacity,
    passive_limit_price,
    touched_passive_limit,
)
from .market_impact import fee_bps_for_role
from .policies import submit_time, timeout_time


@dataclass(frozen=True)
class MakerRepriceResult:
    fills: tuple[FillRecord, ...]
    fill_probability: float
    reprice_count: int
    child_order_count: int
    used_market_slice_ids: tuple[str, ...]
    latest_used_available_at_utc: datetime | None
    arrival_mid_price: float | None
    spread_bps: float | None
    latency_ms: float
    timed_out: bool


def simulate_maker_reprice(scenario: ExecutionScenario) -> MakerRepriceResult:
    live_time = submit_time(scenario)
    deadline = timeout_time(scenario)
    arrival = market_slice_available_at(scenario.market_path, live_time)
    if arrival is None:
        return MakerRepriceResult(
            fills=(),
            fill_probability=0.0,
            reprice_count=0,
            child_order_count=1,
            used_market_slice_ids=(),
            latest_used_available_at_utc=None,
            arrival_mid_price=None,
            spread_bps=None,
            latency_ms=float(scenario.submit_latency_ms),
            timed_out=True,
        )

    limit_price = passive_limit_price(arrival, scenario.side, scenario.limit_offset_bps)
    next_reprice = live_time + timedelta(seconds=scenario.reprice_interval_seconds)
    remaining = scenario.quantity
    fills: list[FillRecord] = []
    used: list[str] = [arrival.slice_id]
    probability_not_filled = 1.0
    reprice_count = 0
    latest_available = arrival.available_at_utc

    for market in scenario.market_path:
        if market.available_at_utc < live_time or market.available_at_utc > deadline:
            continue
        if market.slice_id not in used:
            used.append(market.slice_id)
        latest_available = max(latest_available, market.available_at_utc)

        if remaining > 0 and touched_passive_limit(market, scenario.side, limit_price):
            capacity, probability = passive_fill_capacity(
                market,
                scenario.side,
                remaining,
                scenario.participation_cap,
            )
            probability_not_filled *= 1.0 - probability
            if capacity > 0:
                fill = FillRecord(
                    fill_id=f"{scenario.scenario_id}/maker-fill/{len(fills) + 1}",
                    source_slice_id=market.slice_id,
                    fill_time_utc=market.available_at_utc,
                    quantity=capacity,
                    price=limit_price,
                    liquidity_role=LiquidityRole.MAKER,
                    fee_bps=fee_bps_for_role(LiquidityRole.MAKER, scenario.cost_model),
                    impact_bps=0.0,
                    child_index=0,
                )
                fills.append(fill)
                remaining = max(remaining - capacity, 0.0)
                if remaining <= 1e-12:
                    break

        while (
            remaining > 0
            and market.available_at_utc >= next_reprice
            and reprice_count < scenario.max_reprices
            and next_reprice <= deadline
        ):
            visible = market_slice_available_at(scenario.market_path, next_reprice)
            if visible is None:
                break
            limit_price = passive_limit_price(
                visible,
                scenario.side,
                scenario.limit_offset_bps,
            )
            if visible.slice_id not in used:
                used.append(visible.slice_id)
            latest_available = max(latest_available, visible.available_at_utc)
            reprice_count += 1
            next_reprice += timedelta(seconds=scenario.reprice_interval_seconds)

    filled = sum(item.quantity for item in fills)
    timed_out = filled + 1e-12 < scenario.quantity
    latency_ms = float(scenario.submit_latency_ms * (1 + reprice_count))
    return MakerRepriceResult(
        fills=tuple(fills),
        fill_probability=min(max(1.0 - probability_not_filled, 0.0), 1.0),
        reprice_count=reprice_count,
        child_order_count=1 + reprice_count,
        used_market_slice_ids=tuple(used),
        latest_used_available_at_utc=latest_available,
        arrival_mid_price=arrival.mid_price,
        spread_bps=arrival.spread_bps,
        latency_ms=latency_ms,
        timed_out=timed_out,
    )
