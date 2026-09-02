"""Feature-family builders and canonical research feature metadata."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import fmean, pstdev
from typing import Iterable

from .contracts import FeatureDefinition, FeatureVector, MarketEvent, MarketIntelligenceConfig
from .funding_basis import build_basis_funding_features
from .liquidations import build_liquidation_features
from .open_interest import build_open_interest_features
from .orderflow import build_orderflow_features


def build_feature_families(
    events: Iterable[MarketEvent],
    *,
    decision_time_utc: datetime,
    config: MarketIntelligenceConfig,
) -> dict[str, FeatureVector]:
    materialized = tuple(events)
    flow = build_orderflow_features(
        materialized,
        decision_time_utc=decision_time_utc,
        windows_seconds=config.feature_windows_seconds,
        large_trade_quantile=config.large_trade_quantile,
    )
    spread = build_spread_features(
        materialized,
        decision_time_utc=decision_time_utc,
        zscore_window_seconds=config.spread_zscore_window_seconds,
        zscore_min_observations=config.spread_zscore_min_observations,
    )
    basis_funding = build_basis_funding_features(
        materialized,
        decision_time_utc=decision_time_utc,
        extremeness_min_observations=config.funding_extremeness_min_observations,
    )
    preferred_window = (
        15 if 15 in config.feature_windows_seconds else min(config.feature_windows_seconds)
    )
    flow_imbalance = _float_or_none(flow.get(f"flow_imbalance_{preferred_window}s"))
    open_interest = build_open_interest_features(materialized, flow_imbalance=flow_imbalance)
    liquidations = build_liquidation_features(
        materialized,
        decision_time_utc=decision_time_utc,
        window_seconds=max(config.feature_windows_seconds),
    )
    return {
        "flow": flow,
        "spread": spread,
        "basis_funding": basis_funding,
        "open_interest": open_interest,
        "liquidations": liquidations,
    }


def build_spread_features(
    events: Iterable[MarketEvent],
    *,
    decision_time_utc: datetime,
    zscore_window_seconds: int,
    zscore_min_observations: int,
) -> FeatureVector:
    books = sorted(
        (item for item in events if item.event_type == "book_ticker"),
        key=lambda item: (item.event_time_utc, item.event_id),
    )
    parsed: list[tuple[MarketEvent, float, float, float, float]] = []
    for item in books:
        bid = _positive(item.payload.get("best_bid", item.payload.get("bid")))
        ask = _positive(item.payload.get("best_ask", item.payload.get("ask")))
        bid_qty = _nonnegative(item.payload.get("bid_qty", item.payload.get("bid_size")))
        ask_qty = _nonnegative(item.payload.get("ask_qty", item.payload.get("ask_size")))
        if bid is None or ask is None or bid_qty is None or ask_qty is None:
            continue
        if bid >= ask:
            raise ValueError("book_ticker_crossed_or_locked")
        parsed.append((item, bid, ask, bid_qty, ask_qty))
    if not parsed:
        return {}
    _, bid, ask, bid_qty, ask_qty = parsed[-1]
    mid = (bid + ask) / 2.0
    spread_abs = ask - bid
    spread_bps = spread_abs / mid * 10_000.0
    total_qty = bid_qty + ask_qty
    microprice = ((ask * bid_qty) + (bid * ask_qty)) / total_qty if total_qty > 0 else None
    zscore = _spread_zscore(
        parsed,
        decision_time_utc=decision_time_utc,
        window_seconds=zscore_window_seconds,
        min_observations=zscore_min_observations,
    )
    return {
        "mid_price": mid,
        "spread_abs": spread_abs,
        "spread_bps": spread_bps,
        "top_book_imbalance": (bid_qty - ask_qty) / total_qty if total_qty > 0 else None,
        "microprice": microprice,
        "bid_depth_top": bid_qty,
        "ask_depth_top": ask_qty,
        "depth_ratio": bid_qty / ask_qty if ask_qty > 0 else None,
        "spread_zscore_causal": zscore,
    }


def feature_definitions(config: MarketIntelligenceConfig) -> tuple[FeatureDefinition, ...]:
    definitions: list[FeatureDefinition] = []
    for window in config.feature_windows_seconds:
        for name, dtype, unit in (
            ("trade_count", "int", "count"),
            ("buy_trade_count", "int", "count"),
            ("sell_trade_count", "int", "count"),
            ("buy_notional", "float", "quote_notional"),
            ("sell_notional", "float", "quote_notional"),
            ("net_taker_notional", "float", "quote_notional"),
            ("taker_buy_ratio", "float", "ratio_0_1"),
            ("signed_volume", "float", "base_quantity"),
            ("trade_intensity", "float", "trades_per_second"),
            ("average_trade_size", "float", "base_quantity"),
            ("large_trade_share", "float", "ratio_0_1"),
            ("flow_imbalance", "float", "ratio_-1_1"),
            ("flow_acceleration", "float", "ratio_delta"),
        ):
            definitions.append(
                FeatureDefinition(
                    feature_name=f"{name}_{window}s",
                    feature_family="flow",
                    dtype=dtype,
                    unit=unit,
                    window_seconds=window,
                    source_type="public_agg_trade",
                    calculation_version="market_intelligence_flow_v1",
                    availability_rule="event.available_at_utc <= decision_time_utc",
                    nan_policy="null_when_denominator_or_history_is_insufficient",
                    range_policy="finite_values_only; ratios bounded by semantics",
                )
            )
    for name, unit in (
        ("mid_price", "quote_price"),
        ("spread_abs", "quote_price"),
        ("spread_bps", "basis_points"),
        ("top_book_imbalance", "ratio_-1_1"),
        ("microprice", "quote_price"),
        ("bid_depth_top", "base_quantity"),
        ("ask_depth_top", "base_quantity"),
        ("depth_ratio", "ratio"),
        ("spread_zscore_causal", "zscore"),
    ):
        definitions.append(
            FeatureDefinition(
                feature_name=name,
                feature_family="spread",
                dtype="float",
                unit=unit,
                source_type="public_book_ticker",
                calculation_version="market_intelligence_spread_v1",
                availability_rule="latest causal top-of-book at cutoff",
                nan_policy="null_when_depth_or_history_is_insufficient",
                range_policy="finite values; best_bid < best_ask",
            )
        )
    for name, unit in (
        ("mark_price", "quote_price"),
        ("index_price", "quote_price"),
        ("mark_index_basis_abs", "quote_price"),
        ("mark_index_basis_bps", "basis_points"),
        ("perp_index_basis_bps", "basis_points"),
        ("premium_bps", "basis_points"),
        ("funding_rate_predicted", "fraction"),
        ("funding_rate_realized", "fraction"),
        ("funding_annualized_research", "fraction_per_year"),
        ("funding_direction", "signed_class"),
        ("funding_extremeness_causal", "zscore"),
        ("time_to_next_funding_seconds", "seconds"),
    ):
        definitions.append(
            FeatureDefinition(
                feature_name=name,
                feature_family="basis_funding",
                dtype="float" if name != "funding_direction" else "int",
                unit=unit,
                source_type="public_mark_index_funding",
                calculation_version="market_intelligence_basis_funding_v1",
                availability_rule="latest causal mark/index/funding observation",
                nan_policy="null_when_source_field_or causal history is unavailable",
                range_policy="finite values; positive prices; funding semantics explicit",
            )
        )
    for name, unit in (
        ("oi", "contracts_or_base_units"),
        ("oi_delta", "contracts_or_base_units"),
        ("oi_pct_change", "ratio"),
        ("oi_velocity", "units_per_second"),
        ("price_oi_interaction", "ratio_product"),
        ("flow_oi_interaction", "ratio_product"),
    ):
        definitions.append(
            FeatureDefinition(
                feature_name=name,
                feature_family="open_interest",
                dtype="float",
                unit=unit,
                source_type="public_open_interest",
                calculation_version="market_intelligence_open_interest_v1",
                availability_rule="two latest causal OI observations when delta required",
                nan_policy="null_when prior observation or denominator is unavailable",
                range_policy="finite values; OI strictly positive",
            )
        )
    for name, dtype, unit in (
        ("long_liquidation_notional", "float", "quote_notional"),
        ("short_liquidation_notional", "float", "quote_notional"),
        ("net_liquidation_pressure", "float", "quote_notional"),
        ("liquidation_count", "int", "count"),
        ("liquidation_intensity", "float", "events_per_second"),
        ("liquidation_imbalance", "float", "ratio_-1_1"),
        ("window_seconds", "int", "seconds"),
    ):
        definitions.append(
            FeatureDefinition(
                feature_name=name,
                feature_family="liquidations",
                dtype=dtype,
                unit=unit,
                window_seconds=max(config.feature_windows_seconds),
                source_type="public_liquidation_event",
                calculation_version="market_intelligence_liquidations_v1",
                availability_rule="causal public liquidation events inside configured window",
                nan_policy="null_only_for undefined imbalance; no synthetic source values",
                range_policy="finite nonnegative notionals/counts; imbalance bounded [-1,1]",
            )
        )
    return tuple(definitions)


def _spread_zscore(
    parsed: list[tuple[MarketEvent, float, float, float, float]],
    *,
    decision_time_utc: datetime,
    window_seconds: int,
    min_observations: int,
) -> float | None:
    cutoff = decision_time_utc - timedelta(seconds=window_seconds)
    spreads: list[float] = []
    for event, bid, ask, _, _ in parsed:
        if cutoff < event.event_time_utc <= decision_time_utc:
            mid = (bid + ask) / 2.0
            spreads.append((ask - bid) / mid * 10_000.0)
    if len(spreads) < min_observations:
        return None
    std = pstdev(spreads)
    if std == 0:
        return 0.0
    latest_spread = spreads[-1]
    return (latest_spread - fmean(spreads)) / std


def _positive(value: object) -> float | None:
    parsed = _float_or_none(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative(value: object) -> float | None:
    parsed = _float_or_none(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _float_or_none(value: object) -> float | None:
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
