from __future__ import annotations

from smartcrypto.ops.dashboard_snapshots.active_controls_snapshot_builder import (
    LEVEL4_COMMANDS,
    REQUIRED_SECTIONS,
    build_active_controls_snapshot,
    calculate_grid_parameter_diff,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DASHBOARD_ACTIVE_CONTROLS_SCHEMA_VERSION
from tests.dashboard_builder_test_support import assert_safe_snapshot, context, write_json


def test_active_controls_builder_hard_blocks_level4(tmp_path) -> None:
    write_json(tmp_path, "data/runtime/runtime_safety_audit_config.json", {"riskmanager_approval": True})
    write_json(tmp_path, "data/runtime/kill_switch.json", {"active": False})
    write_json(tmp_path, "data/reports/risk_recovery_mode_audit_report.json", {"status": "ok"})
    write_json(tmp_path, "data/reports/state_reconciliation_audit_report.json", {"status": "ok"})
    snapshot = build_active_controls_snapshot(context(tmp_path))
    assert_safe_snapshot(snapshot, DASHBOARD_ACTIVE_CONTROLS_SCHEMA_VERSION, REQUIRED_SECTIONS)
    commands = snapshot["sections"]["level4_hard_blocks"]["commands"]
    assert {item["command"] for item in commands} == set(LEVEL4_COMMANDS)
    assert all(item["status"] == "HARD_BLOCKED" and item["allowed"] is False for item in commands)
    assert snapshot["sections"]["active_layer_status"]["command_execution_enabled"] is False


def test_active_controls_reconciliation_and_grid_diff(tmp_path) -> None:
    write_json(tmp_path, "data/runtime/runtime_safety_audit_config.json", {"riskmanager_approval": True})
    write_json(tmp_path, "data/runtime/kill_switch.json", {"active": False})
    write_json(tmp_path, "data/reports/risk_recovery_mode_audit_report.json", {"status": "ok"})
    write_json(tmp_path, "data/reports/state_reconciliation_audit_report.json", {"status": "diverged"})
    snapshot = build_active_controls_snapshot(context(tmp_path))
    assert snapshot["sections"]["security_state"]["reconciliation_lock_active"] is True
    assert snapshot["sections"]["active_layer_status"]["paper_entry_allowed"] is False
    assert calculate_grid_parameter_diff(100, 125) == {"diff_abs": 25, "diff_pct": 25.0}
