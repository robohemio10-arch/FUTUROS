from __future__ import annotations

import pytest

from smartcrypto.ops.dashboard_snapshots.contracts import DASHBOARD_OPPORTUNITY_SCANNER_SCHEMA_VERSION
from smartcrypto.ops.dashboard_snapshots.opportunity_scanner_snapshot_builder import (
    REQUIRED_SECTIONS,
    build_opportunity_scanner_snapshot,
    calculate_opportunity_score,
    calculate_order_flow,
    calculate_spread_opportunity,
    calculate_triangular_opportunity,
)
from tests.dashboard_builder_test_support import assert_safe_snapshot, context, write_json


def test_opportunity_builder_is_readonly_and_hard_blocks_real_execution(tmp_path) -> None:
    write_json(tmp_path, "data/reports/market_data_health_audit_report.json", {"status": "ok"})
    snapshot = build_opportunity_scanner_snapshot(context(tmp_path))
    assert_safe_snapshot(snapshot, DASHBOARD_OPPORTUNITY_SCANNER_SCHEMA_VERSION, REQUIRED_SECTIONS)
    governance = snapshot["sections"]["governance"]
    assert governance["real_arbitrage"] == "HARD_BLOCKED"
    assert governance["sniper_real"] == "HARD_BLOCKED"
    assert governance["dashboard_can_send_order"] is False


def test_opportunity_formulas() -> None:
    spread = calculate_spread_opportunity(price_a=100, price_b=102, notional_usdt=1000, fee_rate_exchange_a=0.001, fee_rate_exchange_b=0.001, slippage_pct_total=0.2, latency_penalty_pct=0.1)
    assert spread["spread_gross_pct"] == 2
    assert spread["spread_net_pct"] == pytest.approx(1.5)
    assert spread["projected_net_profit_usdt"] == pytest.approx(15)
    triangular = calculate_triangular_opportunity(100, (2, 0.5, 1.1))
    assert triangular["triangular_return_pct"] == pytest.approx(10)
    flow = calculate_order_flow(600, 400)
    assert flow["buy_pressure_pct"] == 60
    assert flow["ofi_score"] == 0.2
    assert calculate_opportunity_score(100, 80, 60, 70, 30, 20) == pytest.approx(66.5)
