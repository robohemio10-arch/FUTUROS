from __future__ import annotations

from smartcrypto.ops.dashboard_snapshots.contracts import DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION
from smartcrypto.ops.dashboard_snapshots.infrastructure_snapshot_builder import (
    REQUIRED_SECTIONS,
    build_infrastructure_snapshot,
    calculate_latency_metrics,
    calculate_market_microstructure,
    classify_rate_limit,
)
from tests.dashboard_builder_test_support import assert_safe_snapshot, context, write_json


def test_infrastructure_builder_contract_and_metrics(tmp_path) -> None:
    write_json(tmp_path, "data/reports/system_healthcheck_report.json", {"status": "ok", "cpu_pct": 20, "ram_pct": 30, "disk_pct": 40})
    write_json(tmp_path, "data/reports/market_data_health_audit_report.json", {"status": "ok"})
    write_json(tmp_path, "data/reports/market_data_health_runtime_sources_report.json", {"best_bid": 100, "best_ask": 101, "best_bid_size": 2, "best_ask_size": 3, "latency_ms": 25, "last_candle_timestamp_utc": "2026-06-11T11:59:30Z"})

    snapshot = build_infrastructure_snapshot(context(tmp_path))

    assert_safe_snapshot(snapshot, DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION, REQUIRED_SECTIONS)
    market = snapshot["sections"]["market_data_health"]
    assert market["spread_bps"] > 0
    assert market["data_age_seconds"] == 30


def test_infrastructure_formulas_and_missing_sources(tmp_path) -> None:
    assert calculate_market_microstructure(100, 102, 2, 3) == {"mid_price": 101.0, "spread_bps": 198.01980198019803, "top_of_book_depth_usdt": 506}
    metrics = calculate_latency_metrics([10, 20, 30, 40])
    assert metrics["latency_p50_ms"] == 25
    assert metrics["latency_p90_ms"] == 37
    assert classify_rate_limit(50, 100) == "OK"
    assert classify_rate_limit(70, 100) == "WARNING"
    assert classify_rate_limit(90, 100) == "BLOCKED"
    snapshot = build_infrastructure_snapshot(context(tmp_path))
    assert snapshot["missing_required_sources"]
