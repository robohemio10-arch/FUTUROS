from __future__ import annotations

from smartcrypto.ops.dashboard_snapshots.alerts_messaging_snapshot_builder import (
    REQUIRED_SECTIONS,
    ROUTING_POLICY,
    build_alerts_messaging_snapshot,
    calculate_delivery_metrics,
    calculate_retry_state,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DASHBOARD_ALERTS_MESSAGING_SCHEMA_VERSION
from tests.dashboard_builder_test_support import assert_safe_snapshot, context, write_json, write_jsonl


def test_alerts_builder_contract_routing_and_heartbeat(tmp_path) -> None:
    write_json(tmp_path, "data/reports/critical_alerting_report.json", {"status": "ok"})
    write_json(tmp_path, "data/runtime/kill_switch.json", {"active": False})
    write_json(tmp_path, "data/runtime/runtime_safety_audit_config.json", {"status": "ok"})
    write_json(tmp_path, "data/reports/notification_dispatcher_report.json", {"last_heartbeat_utc": "2026-06-11T11:59:30Z", "max_heartbeat_age_seconds": 60})
    write_jsonl(tmp_path, "data/alerts/alert_outbox.jsonl", [{"severity": "WARNING", "status": "delivered"}, {"severity": "PANIC", "status": "pending", "created_at_utc": "2026-06-11T11:50:00Z"}])
    snapshot = build_alerts_messaging_snapshot(context(tmp_path))
    assert_safe_snapshot(snapshot, DASHBOARD_ALERTS_MESSAGING_SCHEMA_VERSION, REQUIRED_SECTIONS)
    assert snapshot["sections"]["dispatcher_status"]["dispatcher_status"] == "ONLINE"
    assert snapshot["sections"]["critical_events"]["critical_delivery_breach_count"] == 1
    assert ROUTING_POLICY["INFO"] == ["log"]
    assert ROUTING_POLICY["WARNING"] == ["telegram"]
    assert ROUTING_POLICY["PANIC"] == ["telegram", "ntfy", "operator_required"]


def test_alert_delivery_and_backoff_formulas() -> None:
    metrics = calculate_delivery_metrics([{"status": "delivered", "severity": "INFO"}, {"status": "failed", "severity": "CRITICAL"}, {"status": "retry", "severity": "PANIC"}])
    assert metrics["success_rate_pct"] == 50
    assert metrics["failure_rate_pct"] == 50
    assert metrics["pending_count"] == 1
    assert metrics["critical_undelivered_count"] == 2
    retry = calculate_retry_state(retry_count=3, max_retries=3, base_backoff_seconds=5, max_backoff_seconds=60, status="failed")
    assert retry["current_backoff_seconds"] == 40
    assert retry["retry_exhausted"] is True
    assert retry["dead_letter"] is True
