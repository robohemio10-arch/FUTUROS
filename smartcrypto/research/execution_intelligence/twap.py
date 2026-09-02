"""Time-weighted average price slicing simulation for W8 research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .contracts import ExecutionScenario, FillRecord, LiquidityRole, Side
from .fill_model import aggressive_fill_estimate, aggressive_limit_price, market_slice_available_at
from .market_impact import fee_bps_for_role
from .policies import submit_time


@dataclass(frozen=True)
class TwapResult:
    fills: tuple[FillRecord, ...]
    fill_probability: float
    child_order_count: int
    used_market_slice_ids: tuple[str, ...]
    latest_used_available_at_utc: datetime | None
    arrival_mid_price: float | None
    spread_bps: float | None
    latency_ms: float
    timed_out: bool


def simulate_twap(scenario: ExecutionScenario) -> TwapResult:
    first_time = submit_time(scenario)
    arrival = market_slice_available_at(scenario.market_path, first_time)
    if arrival is None:
        return TwapResult(
            fills=(),
            fill_probability=0.0,
            child_order_count=scenario.twap_slices,
            used_market_slice_ids=(),
            latest_used_available_at_utc=None,
            arrival_mid_price=None,
            spread_bps=None,
            latency_ms=float(scenario.submit_latency_ms * scenario.twap_slices),
            timed_out=True,
        )

    total = scenario.quantity
    base_child = total / scenario.twap_slices
    remaining = total
    fills: list[FillRecord] = []
    used: list[str] = []
    consumed_by_slice: dict[str, float] = {}
    probability_weighted = 0.0
    latest_available: datetime | None = None

    for child_index in range(scenario.twap_slices):
        if remaining <= 1e-12:
            break
        action_time = first_time + timedelta(
            seconds=scenario.twap_interval_seconds * child_index
        )
        market = market_slice_available_at(scenario.market_path, action_time)
        child_quantity = (
            remaining
            if child_index == scenario.twap_slices - 1
            else min(base_child, remaining)
        )
        if market is None:
            continue
        if market.slice_id not in used:
            used.append(market.slice_id)
        latest_available = (
            market.available_at_utc
            if latest_available is None
            else max(latest_available, market.available_at_utc)
        )

        limit_price = aggressive_limit_price(
            market,
            scenario.side,
            scenario.aggressive_limit_bps,
        )
        estimate = aggressive_fill_estimate(
            market,
            scenario.side,
            child_quantity,
            scenario.participation_cap,
            limit_price,
            scenario.cost_model,
        )
        raw_capacity = (
            (market.ask_quantity if scenario.side == Side.BUY else market.bid_quantity)
            + market.traded_volume * scenario.participation_cap
        )
        already_consumed = consumed_by_slice.get(market.slice_id, 0.0)
        residual_capacity = max(raw_capacity - already_consumed, 0.0)
        fill_quantity = min(estimate.quantity, residual_capacity, child_quantity)
        probability_weighted += estimate.fill_probability * (child_quantity / total)
        if fill_quantity <= 0:
            continue

        consumed_by_slice[market.slice_id] = already_consumed + fill_quantity
        fills.append(
            FillRecord(
                fill_id=f"{scenario.scenario_id}/twap-fill/{child_index + 1}",
                source_slice_id=market.slice_id,
                fill_time_utc=action_time,
                quantity=fill_quantity,
                price=estimate.price,
                liquidity_role=LiquidityRole.TAKER,
                fee_bps=fee_bps_for_role(LiquidityRole.TAKER, scenario.cost_model),
                impact_bps=estimate.impact_bps,
                child_index=child_index,
            )
        )
        remaining = max(remaining - fill_quantity, 0.0)

    filled = sum(item.quantity for item in fills)
    return TwapResult(
        fills=tuple(fills),
        fill_probability=min(max(probability_weighted, 0.0), 1.0),
        child_order_count=scenario.twap_slices,
        used_market_slice_ids=tuple(used),
        latest_used_available_at_utc=latest_available,
        arrival_mid_price=arrival.mid_price,
        spread_bps=arrival.spread_bps,
        latency_ms=float(scenario.submit_latency_ms * scenario.twap_slices),
        timed_out=filled + 1e-12 < scenario.quantity,
    )
