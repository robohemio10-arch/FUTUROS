from __future__ import annotations

from typing import Any

from smartcrypto.ops.dashboard_snapshots.aibot_parity_integration import (
    build_aibot_parity_dashboard_section,
)
from smartcrypto.ops.dashboard_snapshots.build_context import DashboardBuildContext
from smartcrypto.ops.dashboard_snapshots.builder_common import (
    build_snapshot_envelope,
    first_payload,
    load_page_sources,
    records,
    section,
)
from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_OPPORTUNITY_SCANNER_SCHEMA_VERSION,
    DashboardPageId,
    DashboardSectionStatus,
    HardBlockStatus,
)
from smartcrypto.ops.dashboard_snapshots.safe_math import safe_div


REQUIRED_SECTIONS = (
    "status",
    "spread_scanner",
    "triangular_arbitrage",
    "order_flow_imbalance",
    "launch_radar",
    "opportunity_ranking",
    "events",
    "governance",
    "aibot_parity",
    "audit",
)


def calculate_spread_opportunity(
    *,
    price_a: float,
    price_b: float,
    notional_usdt: float,
    fee_rate_exchange_a: float = 0.0,
    fee_rate_exchange_b: float = 0.0,
    slippage_pct_total: float = 0.0,
    latency_penalty_pct: float = 0.0,
) -> dict[str, float]:
    spread_gross_pct = safe_div(price_b - price_a, price_a) * 100.0
    fee_pct_total = (fee_rate_exchange_a + fee_rate_exchange_b) * 100.0
    fee_cost = notional_usdt * (fee_rate_exchange_a + fee_rate_exchange_b)
    spread_net_pct = spread_gross_pct - fee_pct_total - slippage_pct_total - latency_penalty_pct
    return {
        "spread_gross_pct": spread_gross_pct,
        "gross_edge_usdt": notional_usdt * safe_div(spread_gross_pct, 100.0),
        "fee_cost_usdt": fee_cost,
        "spread_net_pct": spread_net_pct,
        "projected_net_profit_usdt": notional_usdt * safe_div(spread_net_pct, 100.0),
    }


def calculate_triangular_opportunity(
    capital: float,
    rates: tuple[float, float, float],
    fees: tuple[float, float, float] = (0.0, 0.0, 0.0),
    slippages: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict[str, float]:
    value = capital
    for rate, fee, slippage in zip(rates, fees, slippages):
        value *= rate * (1.0 - fee) * (1.0 - slippage)
    return {
        "final_capital": value,
        "triangular_return_pct": (safe_div(value, capital) - 1.0) * 100.0,
        "triangular_net_profit_usdt": value - capital,
    }


def calculate_order_flow(bid_depth_usdt: float, ask_depth_usdt: float) -> dict[str, float]:
    total = bid_depth_usdt + ask_depth_usdt
    return {
        "bid_depth_usdt": bid_depth_usdt,
        "ask_depth_usdt": ask_depth_usdt,
        "buy_pressure_pct": safe_div(bid_depth_usdt, total) * 100.0,
        "sell_pressure_pct": safe_div(ask_depth_usdt, total) * 100.0,
        "ofi_score": safe_div(bid_depth_usdt - ask_depth_usdt, total),
    }


def calculate_opportunity_score(
    normalized_expected_value: float,
    liquidity_score: float,
    regime_score: float,
    shadow_quality_score: float,
    risk_score: float,
    latency_score: float,
) -> float:
    return (
        normalized_expected_value * 0.35
        + liquidity_score * 0.20
        + regime_score * 0.15
        + shadow_quality_score * 0.15
        - risk_score * 0.10
        - latency_score * 0.05
    )


def build_opportunity_scanner_snapshot(context: DashboardBuildContext) -> dict[str, Any]:
    sources = load_page_sources(context, DashboardPageId.opportunity_scanner)
    spread_rows = records(first_payload(sources, "opportunity_spread_scanner_snapshot"))
    triangular_rows = records(first_payload(sources, "triangular_arbitrage_snapshot"))
    flow_rows = records(first_payload(sources, "order_flow_imbalance_snapshot"))
    launch_rows = records(first_payload(sources, "launch_radar_snapshot"))
    candidates = [*spread_rows, *triangular_rows, *flow_rows, *launch_rows]
    ranking = sorted(candidates, key=lambda item: float(item.get("opportunity_score", item.get("score", 0.0)) or 0.0), reverse=True)
    governance = {
        "opportunity_scanner": HardBlockStatus.READ_ONLY.value,
        "real_arbitrage": HardBlockStatus.HARD_BLOCKED.value,
        "sniper_real": HardBlockStatus.HARD_BLOCKED.value,
        "multi_exchange_live": HardBlockStatus.HARD_BLOCKED.value,
        "dashboard_can_send_order": False,
        "dashboard_can_arm_sniper": False,
    }
    sections = {
        "status": section(DashboardSectionStatus.OK if candidates else DashboardSectionStatus.UNKNOWN, opportunity_count=len(candidates)),
        "spread_scanner": section(DashboardSectionStatus.OK if spread_rows else DashboardSectionStatus.UNKNOWN, opportunities=spread_rows),
        "triangular_arbitrage": section(DashboardSectionStatus.OK if triangular_rows else DashboardSectionStatus.UNKNOWN, opportunities=triangular_rows, real_execution=HardBlockStatus.HARD_BLOCKED.value),
        "order_flow_imbalance": section(DashboardSectionStatus.OK if flow_rows else DashboardSectionStatus.UNKNOWN, observations=flow_rows),
        "launch_radar": section(DashboardSectionStatus.OK if launch_rows else DashboardSectionStatus.UNKNOWN, observations=launch_rows, sniper_real=HardBlockStatus.HARD_BLOCKED.value),
        "opportunity_ranking": section(DashboardSectionStatus.OK if ranking else DashboardSectionStatus.UNKNOWN, ranking=ranking),
        "events": section(DashboardSectionStatus.OK, events=records(first_payload(sources, "financial_event_log"))[-50:]),
        "governance": section(DashboardSectionStatus.OK, **governance),
        "aibot_parity": build_aibot_parity_dashboard_section(
            sources, "opportunity_scanner"
        ),
        "audit": section(DashboardSectionStatus.OK, dashboard_reads_only=True),
    }
    return build_snapshot_envelope(
        context=context,
        page_id=DashboardPageId.opportunity_scanner,
        schema_version=DASHBOARD_OPPORTUNITY_SCANNER_SCHEMA_VERSION,
        sections=sections,
        source_state=sources,
    )
