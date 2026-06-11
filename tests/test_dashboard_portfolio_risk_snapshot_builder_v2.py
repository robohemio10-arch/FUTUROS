from __future__ import annotations

import pytest

from smartcrypto.ops.dashboard_snapshots.contracts import DASHBOARD_PORTFOLIO_RISK_SCHEMA_VERSION
from smartcrypto.ops.dashboard_snapshots.portfolio_risk_snapshot_builder import (
    REQUIRED_SECTIONS,
    build_portfolio_risk_snapshot,
    calculate_capital_summary,
    calculate_drawdown_series,
    calculate_tail_risk,
)
from tests.dashboard_builder_test_support import assert_safe_snapshot, context, write_json


def test_portfolio_builder_contract_reconciliation_and_capital(tmp_path) -> None:
    write_json(tmp_path, "data/reports/state_reconciliation_audit_report.json", {"status": "diverged"})
    write_json(tmp_path, "data/reports/order_intent_capital_ledger_audit_report.json", {"capital_reserved": 100})
    write_json(tmp_path, "data/reports/risk_recovery_mode_audit_report.json", {"status": "ok"})
    write_json(tmp_path, "data/runtime/runtime_safety_audit_config.json", {"max_capital_global": 1000, "cash_available": 600, "capital_deployed": 200})
    write_json(tmp_path, "data/runtime/kill_switch.json", {"active": False})

    snapshot = build_portfolio_risk_snapshot(context(tmp_path))

    assert_safe_snapshot(snapshot, DASHBOARD_PORTFOLIO_RISK_SCHEMA_VERSION, REQUIRED_SECTIONS)
    assert snapshot["sections"]["capital_summary"]["free_capital_for_entries"] == 700
    assert snapshot["sections"]["financial_truth"]["new_entries_blocked"] is True


def test_portfolio_formulas() -> None:
    capital = calculate_capital_summary(cash_available=500, cash_locked=100, inventory_value=200, unrealized_pnl=20, max_capital_global=1000, capital_reserved=100, capital_deployed=250)
    assert capital["estimated_equity"] == 820
    assert capital["free_capital_for_entries"] == 650
    drawdown = calculate_drawdown_series(1000, [100, -200, 50])
    assert drawdown["max_drawdown_pct"] == pytest.approx(-18.1818181818)
    tail = calculate_tail_risk([-0.1, -0.05, 0.02, 0.03], 1000)
    assert tail["historical_var_95"] > 0
    assert tail["cvar_95"] == 100
