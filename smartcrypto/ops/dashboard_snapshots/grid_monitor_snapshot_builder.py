from __future__ import annotations

import math
from collections import Counter
from typing import Any

from smartcrypto.ops.dashboard_snapshots.build_context import DashboardBuildContext
from smartcrypto.ops.dashboard_snapshots.builder_common import (
    all_source_payloads,
    bool_value,
    build_snapshot_envelope,
    finite_float,
    first_payload,
    first_value,
    load_page_sources,
    records,
    section,
)
from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_GRID_MONITOR_SCHEMA_VERSION,
    DashboardPageId,
    DashboardSectionStatus,
)
from smartcrypto.ops.dashboard_snapshots.safe_math import clamp, safe_div, safe_mean


REQUIRED_SECTIONS = (
    "selected_grid",
    "grid_channel",
    "grid_density",
    "dust",
    "order_book",
    "heatmap",
    "last_executions",
    "grid_summary",
    "integrity",
    "audit",
)

MAX_MATERIALIZED_DEPTH_LEVELS = 20
MAX_MATERIALIZED_GRID_LEVELS = 200
GRID_LEVEL_DISTRIBUTION_BUCKETS = 12


def calculate_grid_metrics(
    *,
    lower_price: float,
    upper_price: float,
    current_price: float,
    level_prices: list[float],
    capital_allocated_usdt: float = 0.0,
) -> dict[str, Any]:
    center = safe_div(upper_price + lower_price, 2.0)
    sorted_levels = sorted(set(level_prices))
    differences = [right - left for left, right in zip(sorted_levels, sorted_levels[1:])]
    step_usdt = safe_mean(differences)
    expected_levels = (
        math.floor(safe_div(upper_price - lower_price, step_usdt)) + 1
        if step_usdt > 0
        else len(sorted_levels)
    )
    return {
        "grid_center": center,
        "distance_to_upper_pct": safe_div(upper_price - current_price, current_price) * 100.0,
        "distance_to_lower_pct": safe_div(current_price - lower_price, current_price) * 100.0,
        "distance_to_center_pct": safe_div(current_price - center, center) * 100.0,
        "price_outside_grid": current_price < lower_price or current_price > upper_price,
        "grid_coverage_pct": safe_div(upper_price - lower_price, current_price) * 100.0,
        "step_usdt": step_usdt,
        "step_pct": safe_div(step_usdt, current_price) * 100.0,
        "expected_levels": expected_levels,
        "active_levels": len(sorted_levels),
        "missing_levels": max(expected_levels - len(sorted_levels), 0),
        "capital_per_level": safe_div(capital_allocated_usdt, expected_levels),
    }


def build_grid_channel_payload(
    *,
    lower_price: float | None,
    upper_price: float | None,
    current_price: float | None,
    level_prices: list[float],
    capital_allocated_usdt: float = 0.0,
) -> dict[str, Any]:
    """Materialize the visual grid contract without fabricating absent prices."""

    normalized_levels = _normalize_level_prices(level_prices)
    materialized_levels = normalized_levels[:MAX_MATERIALIZED_GRID_LEVELS]
    channel_available = _valid_grid_channel(
        lower_price=lower_price,
        upper_price=upper_price,
        current_price=current_price,
    )

    contract: dict[str, Any] = {
        "lower_price": lower_price,
        "upper_price": upper_price,
        "current_price": current_price,
        "level_prices": materialized_levels,
        "level_prices_count": len(normalized_levels),
        "level_prices_truncated": len(normalized_levels) > len(materialized_levels),
        "metrics_available": channel_available,
    }

    if not channel_available:
        contract.update(_unavailable_grid_metrics(active_levels=len(normalized_levels)))
        return contract

    assert lower_price is not None
    assert upper_price is not None
    assert current_price is not None
    contract.update(
        calculate_grid_metrics(
            lower_price=lower_price,
            upper_price=upper_price,
            current_price=current_price,
            level_prices=normalized_levels,
            capital_allocated_usdt=capital_allocated_usdt,
        )
    )
    return contract


def calculate_order_book_metrics(
    bids: list[list[float]] | list[tuple[float, float]],
    asks: list[list[float]] | list[tuple[float, float]],
) -> dict[str, float]:
    bid_rows = _normalize_book_rows(bids)
    ask_rows = _normalize_book_rows(asks)
    best_bid = max((price for price, _ in bid_rows), default=0.0)
    best_ask = min((price for price, _ in ask_rows), default=0.0)
    best_bid_qty = next((qty for price, qty in bid_rows if price == best_bid), 0.0)
    best_ask_qty = next((qty for price, qty in ask_rows if price == best_ask), 0.0)
    bid_depth = sum(price * qty for price, qty in bid_rows)
    ask_depth = sum(price * qty for price, qty in ask_rows)
    mid = safe_div(best_bid + best_ask, 2.0)
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid_price": mid,
        "spread_bps": safe_div(best_ask - best_bid, mid) * 10000.0,
        "top_of_book_depth_usdt": best_bid_qty * best_bid + best_ask_qty * best_ask,
        "bid_depth_usdt": bid_depth,
        "ask_depth_usdt": ask_depth,
        "order_book_imbalance": safe_div(bid_depth - ask_depth, bid_depth + ask_depth),
    }


def build_order_book_payload(
    bids: list[list[float]] | list[tuple[float, float]],
    asks: list[list[float]] | list[tuple[float, float]],
    *,
    level_limit: int = MAX_MATERIALIZED_DEPTH_LEVELS,
) -> dict[str, Any]:
    """Materialize bounded public depth data for read-only visual rendering."""

    safe_limit = max(1, min(int(level_limit), MAX_MATERIALIZED_DEPTH_LEVELS))
    bid_rows = sorted(_normalize_book_rows(bids), key=lambda row: row[0], reverse=True)
    ask_rows = sorted(_normalize_book_rows(asks), key=lambda row: row[0])

    materialized_bids = _materialize_depth_side(bid_rows[:safe_limit])
    materialized_asks = _materialize_depth_side(ask_rows[:safe_limit])
    metrics = calculate_order_book_metrics(bid_rows, ask_rows)

    return {
        **metrics,
        "bids": materialized_bids,
        "asks": materialized_asks,
        "source_bid_level_count": len(bid_rows),
        "source_ask_level_count": len(ask_rows),
        "materialized_bid_level_count": len(materialized_bids),
        "materialized_ask_level_count": len(materialized_asks),
        "depth_level_limit": safe_limit,
        "depth_levels_truncated": len(bid_rows) > safe_limit or len(ask_rows) > safe_limit,
        "depth_materialized": bool(materialized_bids and materialized_asks),
    }


def build_grid_level_distribution(
    *,
    level_prices: list[float],
    lower_price: float | None,
    upper_price: float | None,
    bucket_count: int = GRID_LEVEL_DISTRIBUTION_BUCKETS,
) -> list[dict[str, Any]]:
    """Build a deterministic price-level histogram; this is not a market heatmap."""

    if (
        lower_price is None
        or upper_price is None
        or lower_price <= 0.0
        or upper_price <= lower_price
    ):
        return []

    levels = [
        value
        for value in _normalize_level_prices(level_prices)
        if lower_price <= value <= upper_price
    ]
    if not levels:
        return []

    safe_bucket_count = max(1, min(int(bucket_count), 50))
    span = upper_price - lower_price
    bucket_width = span / safe_bucket_count
    if bucket_width <= 0.0:
        return []

    counts = [0 for _ in range(safe_bucket_count)]
    for value in levels:
        index = min(int((value - lower_price) / bucket_width), safe_bucket_count - 1)
        counts[index] += 1

    total = len(levels)
    distribution: list[dict[str, Any]] = []
    for index, count in enumerate(counts):
        bucket_lower = lower_price + index * bucket_width
        bucket_upper = upper_price if index == safe_bucket_count - 1 else bucket_lower + bucket_width
        distribution.append(
            {
                "bucket_index": index,
                "lower_price": bucket_lower,
                "upper_price": bucket_upper,
                "level_count": count,
                "level_share_pct": safe_div(count, total) * 100.0,
            }
        )
    return distribution


def calculate_grid_integrity_score(
    *,
    duplicate_orders: int = 0,
    gap_count: int = 0,
    outside_channel_count: int = 0,
    stale_data: bool = False,
    spread_bps: float = 0.0,
    capital_mismatch: bool = False,
) -> float:
    score = 100.0
    score -= min(duplicate_orders * 10.0, 30.0)
    score -= min(gap_count * 5.0, 25.0)
    score -= min(outside_channel_count * 10.0, 20.0)
    score -= 25.0 if stale_data else 0.0
    score -= min(max(spread_bps - 10.0, 0.0), 15.0)
    score -= 10.0 if capital_mismatch else 0.0
    return clamp(score, 0.0, 100.0)


def build_grid_monitor_snapshot(context: DashboardBuildContext) -> dict[str, Any]:
    sources = load_page_sources(context, DashboardPageId.grid_monitor)
    data = all_source_payloads(sources)
    signal_data = first_payload(sources, "freqtrade_signals")
    orders = records(signal_data)

    lower = _optional_positive_number(first_value(data, ("lower_price", "grid_lower")))
    upper = _optional_positive_number(first_value(data, ("upper_price", "grid_upper")))
    current = _optional_positive_number(first_value(data, ("current_price", "mark_price")))
    symbol = first_value(data, ("symbol", "pair"))

    level_prices = [
        number
        for row in orders
        if (number := _optional_positive_number(first_value(row, ("grid_level_price", "price"))))
        is not None
    ]
    capital = _number(first_value(data, ("capital_allocated_usdt", "grid_capital_usdt")))

    grid = build_grid_channel_payload(
        lower_price=lower,
        upper_price=upper,
        current_price=current,
        level_prices=level_prices,
        capital_allocated_usdt=capital,
    )

    normalized_levels = _normalize_level_prices(level_prices)
    duplicates = count_duplicate_grid_levels(level_prices)
    channel_available = bool(grid["metrics_available"])
    outside = (
        sum(price < lower or price > upper for price in level_prices)
        if channel_available and lower is not None and upper is not None
        else 0
    )

    bids = _book_rows(first_value(data, ("bids",), []))
    asks = _book_rows(first_value(data, ("asks",), []))
    book = build_order_book_payload(bids, asks)

    stale = str(first_value(data, ("status", "market_data_status"), "")).lower() == "stale"
    kill_active = bool_value(
        first_value(data, ("kill_switch_active", "kill_switch_enabled")),
        False,
    )
    missing_levels = _non_negative_int(grid.get("missing_levels"))
    score = calculate_grid_integrity_score(
        duplicate_orders=duplicates,
        gap_count=missing_levels,
        outside_channel_count=outside,
        stale_data=stale,
        spread_bps=float(book["spread_bps"]),
    )
    integrity_observable = channel_available and bool(normalized_levels)
    integrity_status = _integrity_status(
        score=score,
        stale=stale,
        kill_active=kill_active,
        observable=integrity_observable,
    )

    dust_qty = _number(first_value(data, ("dust_qty",)))
    mark_price = _number(first_value(data, ("mark_price", "current_price")))
    equity = _number(first_value(data, ("estimated_equity_usdt", "estimated_equity")))
    dust_value = dust_qty * mark_price

    grid_channel_status = (
        DashboardSectionStatus.UNKNOWN
        if not channel_available
        else DashboardSectionStatus.BLOCKED
        if bool(grid["price_outside_grid"])
        else DashboardSectionStatus.OK
    )
    grid_density_status = (
        DashboardSectionStatus.OK
        if channel_available and bool(normalized_levels)
        else DashboardSectionStatus.UNKNOWN
    )
    selected_grid_status = (
        DashboardSectionStatus.OK
        if symbol not in (None, "") and current is not None
        else DashboardSectionStatus.UNKNOWN
    )
    order_book_status = (
        DashboardSectionStatus.OK if book["depth_materialized"] else DashboardSectionStatus.UNKNOWN
    )

    distribution = build_grid_level_distribution(
        level_prices=level_prices,
        lower_price=lower,
        upper_price=upper,
    )
    heatmap_reason = (
        "time_range_heatmap_source_not_materialized"
        if distribution
        else "grid_level_distribution_unavailable"
    )

    sections = {
        "selected_grid": section(
            selected_grid_status,
            symbol=symbol,
            current_price=current,
        ),
        "grid_channel": section(grid_channel_status, **grid),
        "grid_density": section(
            grid_density_status,
            expected_levels=grid["expected_levels"],
            active_levels=grid["active_levels"],
            missing_levels=grid["missing_levels"],
        ),
        "dust": section(
            DashboardSectionStatus.OK,
            dust_qty=dust_qty,
            dust_value_usdt=dust_value,
            dust_portfolio_pct=safe_div(dust_value, equity) * 100.0,
        ),
        "order_book": section(order_book_status, **book),
        "heatmap": section(
            DashboardSectionStatus.UNKNOWN,
            levels=_normalize_level_prices(level_prices),
            heatmap_available=False,
            heatmap_reason=heatmap_reason,
            level_distribution=distribution,
            level_distribution_kind="grid_level_price_histogram",
            level_distribution_available=bool(distribution),
        ),
        "last_executions": section(
            DashboardSectionStatus.OK,
            executions=records(first_payload(sources, "financial_event_log"))[-20:],
        ),
        "grid_summary": section(
            integrity_status,
            mode=_summary_mode(integrity_status),
        ),
        "integrity": section(
            integrity_status,
            grid_integrity_score=score if integrity_observable else None,
            duplicate_orders=duplicates,
            gap_count=missing_levels if channel_available else None,
            outside_channel_count=outside if channel_available else None,
            stale_data=stale,
            kill_switch_active=kill_active,
        ),
        "audit": section(
            DashboardSectionStatus.OK,
            dashboard_reads_only=True,
            snapshot_contract_hardened=True,
            order_book_depth_materialized=bool(book["depth_materialized"]),
            grid_level_distribution_available=bool(distribution),
            heatmap_available=False,
        ),
    }
    return build_snapshot_envelope(
        context=context,
        page_id=DashboardPageId.grid_monitor,
        schema_version=DASHBOARD_GRID_MONITOR_SCHEMA_VERSION,
        sections=sections,
        source_state=sources,
    )


def count_duplicate_grid_levels(level_prices: list[float]) -> int:
    """Count duplicate valid price levels using the original 12-decimal tolerance."""

    normalized = [
        round(candidate, 12)
        for value in level_prices
        if (candidate := _optional_positive_number(value)) is not None
    ]
    return sum(count - 1 for count in Counter(normalized).values() if count > 1)


def _unavailable_grid_metrics(*, active_levels: int) -> dict[str, Any]:
    return {
        "grid_center": None,
        "distance_to_upper_pct": None,
        "distance_to_lower_pct": None,
        "distance_to_center_pct": None,
        "price_outside_grid": None,
        "grid_coverage_pct": None,
        "step_usdt": None,
        "step_pct": None,
        "expected_levels": None,
        "active_levels": active_levels,
        "missing_levels": None,
        "capital_per_level": None,
    }


def _valid_grid_channel(
    *,
    lower_price: float | None,
    upper_price: float | None,
    current_price: float | None,
) -> bool:
    return (
        lower_price is not None
        and upper_price is not None
        and current_price is not None
        and lower_price > 0.0
        and upper_price > lower_price
        and current_price > 0.0
    )


def _normalize_level_prices(values: list[float]) -> list[float]:
    normalized = {
        candidate
        for value in values
        if (candidate := _optional_positive_number(value)) is not None
    }
    return sorted(normalized)


def _normalize_book_rows(
    value: list[list[float]] | list[tuple[float, float]],
) -> list[tuple[float, float]]:
    output: list[tuple[float, float]] = []
    for row in value:
        if not isinstance(row, list | tuple) or len(row) < 2:
            continue
        price = _optional_positive_number(row[0])
        qty = _optional_positive_number(row[1])
        if price is not None and qty is not None:
            output.append((price, qty))
    return output


def _materialize_depth_side(rows: list[tuple[float, float]]) -> list[dict[str, float]]:
    cumulative_notional = 0.0
    materialized: list[dict[str, float]] = []
    for price, quantity in rows:
        notional = price * quantity
        cumulative_notional += notional
        materialized.append(
            {
                "price": price,
                "quantity": quantity,
                "notional_usdt": notional,
                "cumulative_notional_usdt": cumulative_notional,
            }
        )
    return materialized


def _integrity_status(
    *,
    score: float,
    stale: bool,
    kill_active: bool,
    observable: bool,
) -> DashboardSectionStatus:
    if stale or kill_active:
        return DashboardSectionStatus.BLOCKED
    if not observable:
        return DashboardSectionStatus.UNKNOWN
    if score < 70:
        return DashboardSectionStatus.BLOCKED
    if score < 90:
        return DashboardSectionStatus.WARNING
    return DashboardSectionStatus.OK


def _summary_mode(status: DashboardSectionStatus) -> str:
    if status is DashboardSectionStatus.BLOCKED:
        return "BLOCKED"
    if status is DashboardSectionStatus.OK:
        return "ACTIVE"
    if status is DashboardSectionStatus.WARNING:
        return "DEGRADED"
    return "UNKNOWN"


def _optional_positive_number(value: Any) -> float | None:
    number = finite_float(value)
    if number is None or number <= 0.0:
        return None
    return number


def _non_negative_int(value: Any) -> int:
    number = finite_float(value)
    if number is None:
        return 0
    return max(int(number), 0)


def _number(value: Any, default: float = 0.0) -> float:
    return finite_float(value, default) or 0.0


def _book_rows(value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        return []
    return _normalize_book_rows(value)
