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
    expected_levels = math.floor(safe_div(upper_price - lower_price, step_usdt)) + 1 if step_usdt > 0 else len(sorted_levels)
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


def calculate_order_book_metrics(
    bids: list[list[float]] | list[tuple[float, float]],
    asks: list[list[float]] | list[tuple[float, float]],
) -> dict[str, float]:
    bid_rows = [(float(price), float(qty)) for price, qty in bids]
    ask_rows = [(float(price), float(qty)) for price, qty in asks]
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
    lower = _number(first_value(data, ("lower_price", "grid_lower")))
    upper = _number(first_value(data, ("upper_price", "grid_upper")))
    current = _number(first_value(data, ("current_price", "mark_price")))
    level_prices = [
        number
        for row in orders
        if (number := finite_float(first_value(row, ("grid_level_price", "price")))) is not None
    ]
    capital = _number(first_value(data, ("capital_allocated_usdt", "grid_capital_usdt")))
    grid = calculate_grid_metrics(
        lower_price=lower,
        upper_price=upper,
        current_price=current,
        level_prices=level_prices,
        capital_allocated_usdt=capital,
    )
    normalized_levels = [round(value, 12) for value in level_prices]
    duplicates = sum(count - 1 for count in Counter(normalized_levels).values() if count > 1)
    outside = sum(price < lower or price > upper for price in level_prices) if lower < upper else 0
    bids = _book_rows(first_value(data, ("bids",), []))
    asks = _book_rows(first_value(data, ("asks",), []))
    book = calculate_order_book_metrics(bids, asks)
    stale = str(first_value(data, ("status", "market_data_status"), "")).lower() == "stale"
    kill_active = bool_value(first_value(data, ("kill_switch_active", "kill_switch_enabled")), False)
    score = calculate_grid_integrity_score(
        duplicate_orders=duplicates,
        gap_count=grid["missing_levels"],
        outside_channel_count=outside,
        stale_data=stale,
        spread_bps=book["spread_bps"],
    )
    integrity_status = (
        DashboardSectionStatus.BLOCKED
        if stale or kill_active or score < 70
        else DashboardSectionStatus.WARNING
        if score < 90
        else DashboardSectionStatus.OK
    )
    dust_qty = _number(first_value(data, ("dust_qty",)))
    mark_price = _number(first_value(data, ("mark_price", "current_price")))
    equity = _number(first_value(data, ("estimated_equity_usdt", "estimated_equity")))
    dust_value = dust_qty * mark_price

    sections = {
        "selected_grid": section(DashboardSectionStatus.OK, symbol=first_value(data, ("symbol", "pair"))),
        "grid_channel": section(DashboardSectionStatus.BLOCKED if grid["price_outside_grid"] else DashboardSectionStatus.OK, **grid),
        "grid_density": section(DashboardSectionStatus.OK, expected_levels=grid["expected_levels"], active_levels=grid["active_levels"], missing_levels=grid["missing_levels"]),
        "dust": section(DashboardSectionStatus.OK, dust_qty=dust_qty, dust_value_usdt=dust_value, dust_portfolio_pct=safe_div(dust_value, equity) * 100.0),
        "order_book": section(DashboardSectionStatus.OK if bids and asks else DashboardSectionStatus.UNKNOWN, **book),
        "heatmap": section(DashboardSectionStatus.UNKNOWN, levels=level_prices),
        "last_executions": section(DashboardSectionStatus.OK, executions=records(first_payload(sources, "financial_event_log"))[-20:]),
        "grid_summary": section(integrity_status, mode="BLOCKED" if integrity_status is DashboardSectionStatus.BLOCKED else "ACTIVE" if integrity_status is DashboardSectionStatus.OK else "DEGRADED"),
        "integrity": section(integrity_status, grid_integrity_score=score, duplicate_orders=duplicates, gap_count=grid["missing_levels"], outside_channel_count=outside, stale_data=stale, kill_switch_active=kill_active),
        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True),
    }
    return build_snapshot_envelope(
        context=context,
        page_id=DashboardPageId.grid_monitor,
        schema_version=DASHBOARD_GRID_MONITOR_SCHEMA_VERSION,
        sections=sections,
        source_state=sources,
    )


def _number(value: Any, default: float = 0.0) -> float:
    return finite_float(value, default) or 0.0


def _book_rows(value: Any) -> list[tuple[float, float]]:
    output: list[tuple[float, float]] = []
    if not isinstance(value, list):
        return output
    for row in value:
        if isinstance(row, list | tuple) and len(row) >= 2:
            price, qty = finite_float(row[0]), finite_float(row[1])
            if price is not None and qty is not None:
                output.append((price, qty))
    return output
