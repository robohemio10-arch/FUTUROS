from __future__ import annotations

import pytest

from smartcrypto.ops.dashboard_snapshots.contracts import DASHBOARD_QUANTITATIVE_REPORTS_SCHEMA_VERSION
from smartcrypto.ops.dashboard_snapshots.quantitative_reports_snapshot_builder import (
    REQUIRED_SECTIONS,
    build_quantitative_reports_snapshot,
    calculate_equity_drawdown,
    calculate_institutional_score,
    calculate_performance_metrics,
    calculate_tca,
)
from tests.dashboard_builder_test_support import assert_safe_snapshot, context, write_json


def test_quantitative_builder_contract_and_readonly_exports(tmp_path) -> None:
    write_json(tmp_path, "data/reports/paper_financial_performance_metrics_report.json", {"status": "ok", "initial_equity": 1000, "pnl_series": [100, -50, 25], "returns": [0.1, -0.05, 0.025], "gross_pnl": 100, "fees": 5, "spread_cost": 2, "slippage_cost": 3, "latency_cost": 1})
    snapshot = build_quantitative_reports_snapshot(context(tmp_path))
    assert_safe_snapshot(snapshot, DASHBOARD_QUANTITATIVE_REPORTS_SCHEMA_VERSION, REQUIRED_SECTIONS)
    assert snapshot["sections"]["exports"]["readonly"] is True
    assert snapshot["sections"]["exports"]["writes_training_dataset"] is False


def test_quantitative_formulas() -> None:
    tca = calculate_tca(gross_pnl=100, fees=5, spread_cost=2, slippage_cost=3, latency_cost=1, gross_alpha=100)
    assert tca["net_pnl"] == 89
    assert tca["total_tca_cost"] == 11
    assert tca["cost_to_alpha_ratio"] == 0.11
    equity = calculate_equity_drawdown(1000, [100, -200, 50])
    assert equity["max_drawdown"] == pytest.approx(-200 / 1100)
    metrics = calculate_performance_metrics([0.1, -0.05, 0.02], [100, -50, 20], capital_base=1000)
    assert metrics["profit_factor"] == 2.4
    assert metrics["expectancy_net"] == pytest.approx((2 / 3 * 60) - (1 / 3 * 50))
    assert calculate_institutional_score(100, 80, 70, 60, 50, 40) == 75
