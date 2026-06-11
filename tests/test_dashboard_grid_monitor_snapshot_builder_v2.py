from __future__ import annotations

import pytest

from smartcrypto.ops.dashboard_snapshots.contracts import DASHBOARD_GRID_MONITOR_SCHEMA_VERSION
from smartcrypto.ops.dashboard_snapshots.grid_monitor_snapshot_builder import (
    REQUIRED_SECTIONS,
    build_grid_monitor_snapshot,
    calculate_grid_integrity_score,
    calculate_grid_metrics,
    calculate_order_book_metrics,
)
from tests.dashboard_builder_test_support import assert_safe_snapshot, context, write_json


def test_grid_builder_contract_and_integrity(tmp_path) -> None:
    write_json(tmp_path, "data/reports/market_data_health_audit_report.json", {"status": "ok"})
    write_json(tmp_path, "data/freqtrade_signals.json", {"signals": [{"grid_level_price": 90}, {"grid_level_price": 100}, {"grid_level_price": 100}, {"grid_level_price": 110}]})
    write_json(tmp_path, "data/reports/market_data_health_runtime_sources_report.json", {"lower_price": 90, "upper_price": 110, "current_price": 100, "bids": [[99, 2]], "asks": [[101, 3]]})

    snapshot = build_grid_monitor_snapshot(context(tmp_path))

    assert_safe_snapshot(snapshot, DASHBOARD_GRID_MONITOR_SCHEMA_VERSION, REQUIRED_SECTIONS)
    assert snapshot["sections"]["integrity"]["duplicate_orders"] == 1


def test_grid_formulas() -> None:
    metrics = calculate_grid_metrics(lower_price=90, upper_price=110, current_price=100, level_prices=[90, 100, 110], capital_allocated_usdt=300)
    assert metrics["grid_center"] == 100
    assert metrics["step_pct"] == 10
    assert metrics["expected_levels"] == 3
    book = calculate_order_book_metrics([(99, 2)], [(101, 3)])
    assert book["spread_bps"] == pytest.approx(200)
    assert book["order_book_imbalance"] < 0
    assert calculate_grid_integrity_score(gap_count=3) < 100
