"""Deterministic fill and cost primitives for W8 Execution Intelligence."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from math import sqrt

from .contracts import ExecutionCostModel, MarketSlice, Side

BPS = 10_000.0


@dataclass(frozen=True)
class FillEstimate:
    quantity: float
    price: float
    fill_probability: float
    impact_bps: float
    slippage_bps: float
    spread_bps: float


def market_slice_available_at(
    market_path: tuple[MarketSlice, ...],
    at_utc: datetime,
) -> MarketSlice | None:
    """Return the latest slice that was available at ``at_utc``.

    The market path may contain future replay rows.  They are deliberately not
    visible before their ``available_at_utc`` timestamp.
    """

    index = bisect_right(
        market_path,
        at_utc,
        key=lambda item: item.available_at_utc,
    )
    if index == 0:
        return None
    return market_path[index - 1]


def passive_limit_price(market: MarketSlice, side: Side, offset_bps: float) -> float:
    """Build a passive/near-touch limit without silently crossing the spread."""

    fraction = offset_bps / BPS
    if side == Side.BUY:
        candidate = market.best_bid * (1.0 + fraction)
        return min(candidate, market.best_ask * (1.0 - 1e-12))
    candidate = market.best_ask * (1.0 - fraction)
    return max(candidate, market.best_bid * (1.0 + 1e-12))


def aggressive_limit_price(market: MarketSlice, side: Side, max_slippage_bps: float) -> float:
    fraction = max_slippage_bps / BPS
    if side == Side.BUY:
        return market.best_ask * (1.0 + fraction)
    return market.best_bid * (1.0 - fraction)


def touched_passive_limit(market: MarketSlice, side: Side, limit_price: float) -> bool:
    if side == Side.BUY:
        return market.last_price <= limit_price
    return market.last_price >= limit_price


def passive_fill_capacity(
    market: MarketSlice,
    side: Side,
    requested_quantity: float,
    participation_cap: float,
) -> tuple[float, float]:
    """Conservative passive fill proxy.

    Only a capped fraction of observed traded volume is considered accessible,
    and queue ahead is proxied by same-side top quantity.  This avoids inventing
    hidden L2 depth.
    """

    queue_ahead = market.bid_quantity if side == Side.BUY else market.ask_quantity
    participable = market.traded_volume * participation_cap
    effective = max(participable - queue_ahead, 0.0)
    capacity = min(requested_quantity, effective)
    denominator = max(requested_quantity + queue_ahead, 1e-12)
    probability = min(max(participable / denominator, 0.0), 1.0)
    return capacity, probability


def aggressive_fill_estimate(
    market: MarketSlice,
    side: Side,
    requested_quantity: float,
    participation_cap: float,
    limit_price: float,
    cost_model: ExecutionCostModel,
) -> FillEstimate:
    """Estimate a causal aggressive-limit fill from observed top liquidity.

    Liquidity beyond top-of-book is never fabricated.  A capped share of
    observed traded volume can supplement visible top quantity, and a square
    root participation impact model produces a deterministic impact estimate.
    """

    top_quantity = market.ask_quantity if side == Side.BUY else market.bid_quantity
    participable = market.traded_volume * participation_cap
    capacity = max(top_quantity + participable, 0.0)
    quantity = min(requested_quantity, capacity)
    if quantity <= 0:
        return FillEstimate(
            quantity=0.0,
            price=market.best_ask if side == Side.BUY else market.best_bid,
            fill_probability=0.0,
            impact_bps=0.0,
            slippage_bps=0.0,
            spread_bps=market.spread_bps,
        )

    liquidity_reference = max(top_quantity + market.traded_volume, requested_quantity, 1e-12)
    participation = min(max(quantity / liquidity_reference, 0.0), 1.0)
    impact_bps = cost_model.impact_coefficient_bps * sqrt(participation)
    impact_bps += market.volatility_bps * sqrt(participation) * 0.10
    impact_bps = max(impact_bps, 0.0)

    touch = market.best_ask if side == Side.BUY else market.best_bid
    signed_impact = impact_bps / BPS
    modeled_price = touch * (1.0 + signed_impact if side == Side.BUY else 1.0 - signed_impact)

    if side == Side.BUY:
        executable = modeled_price <= limit_price
        price = min(modeled_price, limit_price)
    else:
        executable = modeled_price >= limit_price
        price = max(modeled_price, limit_price)

    if not executable:
        return FillEstimate(
            quantity=0.0,
            price=touch,
            fill_probability=min(capacity / requested_quantity, 1.0),
            impact_bps=impact_bps,
            slippage_bps=0.0,
            spread_bps=market.spread_bps,
        )

    mid = market.mid_price
    slippage_bps = (
        (price - mid) / mid * BPS if side == Side.BUY else (mid - price) / mid * BPS
    )
    slippage_bps = max(slippage_bps, 0.0) + cost_model.base_slippage_bps
    return FillEstimate(
        quantity=quantity,
        price=price,
        fill_probability=min(capacity / requested_quantity, 1.0),
        impact_bps=impact_bps,
        slippage_bps=slippage_bps,
        spread_bps=market.spread_bps,
    )


def weighted_average_price(quantity_price_pairs: list[tuple[float, float]]) -> float | None:
    quantity = sum(item[0] for item in quantity_price_pairs)
    if quantity <= 0:
        return None
    return sum(qty * price for qty, price in quantity_price_pairs) / quantity


def signed_slippage_bps(side: Side, arrival_mid: float, fill_price: float) -> float:
    if arrival_mid <= 0:
        raise ValueError("arrival_mid_must_be_positive")
    value = (
        (fill_price - arrival_mid) / arrival_mid * BPS
        if side == Side.BUY
        else (arrival_mid - fill_price) / arrival_mid * BPS
    )
    return max(value, 0.0)
