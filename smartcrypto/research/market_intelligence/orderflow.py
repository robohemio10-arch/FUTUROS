"""Causal public agg-trade feature extraction."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Iterable

from .contracts import FeatureVector, MarketEvent


def build_orderflow_features(
    events: Iterable[MarketEvent],
    *,
    decision_time_utc: datetime,
    windows_seconds: tuple[int, ...],
    large_trade_quantile: float,
) -> FeatureVector:
    trades = sorted(
        (item for item in events if item.event_type == "agg_trade"),
        key=lambda item: (item.event_time_utc, item.event_id),
    )
    output: FeatureVector = {}
    for window in windows_seconds:
        cutoff = decision_time_utc - timedelta(seconds=window)
        selected = [item for item in trades if cutoff < item.event_time_utc <= decision_time_utc]
        metrics = _window_metrics(selected, window, large_trade_quantile)
        for name, value in metrics.items():
            output[f"{name}_{window}s"] = value
    return output


def _window_metrics(
    trades: list[MarketEvent],
    window_seconds: int,
    large_trade_quantile: float,
) -> FeatureVector:
    parsed: list[tuple[float, float, bool]] = []
    for item in trades:
        price = _positive_number(item.payload.get("price"))
        quantity = _positive_number(item.payload.get("quantity", item.payload.get("qty")))
        buyer_maker = item.payload.get(
            "buyer_maker",
            item.payload.get("is_buyer_maker", item.payload.get("buyer_is_maker")),
        )
        if price is None or quantity is None or not isinstance(buyer_maker, bool):
            continue
        parsed.append((price, quantity, buyer_maker))

    trade_count = len(parsed)
    if trade_count == 0:
        return {
            "trade_count": 0,
            "buy_trade_count": 0,
            "sell_trade_count": 0,
            "buy_notional": 0.0,
            "sell_notional": 0.0,
            "net_taker_notional": 0.0,
            "taker_buy_ratio": None,
            "signed_volume": 0.0,
            "trade_intensity": 0.0,
            "average_trade_size": None,
            "large_trade_share": None,
            "flow_imbalance": None,
            "flow_acceleration": None,
        }

    buy_notionals: list[float] = []
    sell_notionals: list[float] = []
    quantities: list[float] = []
    signed_quantities: list[float] = []
    notionals: list[float] = []
    signed_notionals: list[float] = []
    for price, quantity, buyer_maker in parsed:
        notional = price * quantity
        is_taker_buy = not buyer_maker
        quantities.append(quantity)
        notionals.append(notional)
        signed_quantities.append(quantity if is_taker_buy else -quantity)
        signed_notionals.append(notional if is_taker_buy else -notional)
        if is_taker_buy:
            buy_notionals.append(notional)
        else:
            sell_notionals.append(notional)

    buy_notional = sum(buy_notionals)
    sell_notional = sum(sell_notionals)
    total_notional = buy_notional + sell_notional
    threshold = _quantile(notionals, large_trade_quantile)
    large_notional = sum(value for value in notionals if value >= threshold)
    split = max(1, len(signed_notionals) // 2)
    earlier = signed_notionals[:split]
    recent = signed_notionals[split:]
    earlier_total = sum(abs(value) for value in earlier)
    recent_total = sum(abs(value) for value in recent)
    earlier_imbalance = sum(earlier) / earlier_total if earlier_total else 0.0
    recent_imbalance = sum(recent) / recent_total if recent_total else earlier_imbalance

    return {
        "trade_count": trade_count,
        "buy_trade_count": len(buy_notionals),
        "sell_trade_count": len(sell_notionals),
        "buy_notional": buy_notional,
        "sell_notional": sell_notional,
        "net_taker_notional": buy_notional - sell_notional,
        "taker_buy_ratio": buy_notional / total_notional if total_notional else None,
        "signed_volume": sum(signed_quantities),
        "trade_intensity": trade_count / float(window_seconds),
        "average_trade_size": sum(quantities) / trade_count,
        "large_trade_share": large_notional / total_notional if total_notional else None,
        "flow_imbalance": (
            (buy_notional - sell_notional) / total_notional if total_notional else None
        ),
        "flow_acceleration": recent_imbalance - earlier_imbalance,
    }


def _positive_number(value: object) -> float | None:
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


def _quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("quantile_requires_values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight
