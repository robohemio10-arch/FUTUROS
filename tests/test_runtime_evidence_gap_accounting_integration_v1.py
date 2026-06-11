from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.active_controls_snapshot_builder import build_active_controls_snapshot
from smartcrypto.ops.dashboard_snapshots.quantitative_reports_snapshot_builder import build_quantitative_reports_snapshot
from smartcrypto.ops.runtime_evidence_pack import build_runtime_evidence_pack_and_readiness_snapshot_v2
from tests.dashboard_builder_test_support import context, write_json as write_dashboard_json
from tests.test_runtime_evidence_pack_and_readiness_snapshot_v2 import base_report, generate_manifest


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def complete_gap_project(tmp_path: Path, *, critical_gap_count: int = 0) -> Path:
    root = tmp_path / "project"
    reports = root / "data" / "reports"
    ok_soak = base_report(
        observed_soak_days=31,
        observed_calendar_days=31,
        continuous_valid_soak_days=31,
        critical_gap_count=critical_gap_count,
        warning_gap_count=0,
        max_gap_minutes=0 if critical_gap_count == 0 else 480,
        readiness_gap_free=critical_gap_count == 0,
        seven_day_diagnostic_status="reached",
        thirty_day_readiness_status="ready" if critical_gap_count == 0 else "blocked",
        generated_at_utc="2026-06-11T12:00:00Z",
    )
    for name in (
        "paper_soak_report",
        "paper_shadow_soak_report",
        "paper_shadow_soak_continuity_audit",
        "paper_shadow_soak_anchor_continuity_pack",
        "runtime_evidence_pack_v2",
        "readiness_snapshot_v2",
    ):
        write_json(reports / f"{name}.json", ok_soak)
    write_json(
        reports / "freqtrade_paper_db_authority_report.json",
        base_report(selected_db="data/snapshots/freqtrade_paper.sqlite", reason="authorized_snapshot"),
    )
    write_json(reports / "readiness_gate_report.json", base_report(readiness_approved=True))
    write_json(reports / "monte_carlo_risk_simulation_report.json", base_report(policy_action="allow_paper"))
    generate_manifest(root)
    return root


def test_runtime_evidence_pack_embeds_gap_accounting_summary(tmp_path: Path) -> None:
    root = complete_gap_project(tmp_path)

    result = build_runtime_evidence_pack_and_readiness_snapshot_v2(
        project_root=root,
        output_dir=root / "out",
        no_write=True,
    )

    pack = result.evidence_pack
    snapshot = result.readiness_snapshot

    assert "paper_shadow_soak_gap_accounting" in pack["evidence_sources"]
    assert pack["paper_shadow_soak_gap_accounting"]["status"] == "ok"
    assert snapshot["paper_shadow_soak_gap_accounting"]["continuous_valid_soak_days"] == 31
    assert snapshot["readiness_gap_free"] is True
    assert snapshot["canary_release_allowed"] is False
    assert snapshot["live_release_allowed"] is False
    assert result.write_performed is False
    assert not (root / "data" / "reports" / "paper_shadow_soak_gap_accounting_report.json").exists()


def test_runtime_evidence_blocks_when_gap_accounting_has_critical_gaps(tmp_path: Path) -> None:
    root = complete_gap_project(tmp_path, critical_gap_count=2)

    result = build_runtime_evidence_pack_and_readiness_snapshot_v2(
        project_root=root,
        output_dir=root / "out",
        no_write=True,
    )
    snapshot = result.readiness_snapshot

    assert snapshot["status"] == "blocked"
    assert snapshot["critical_gap_count"] == 2
    assert "paper_shadow_soak_critical_gaps_present" in snapshot["blocking_reasons"]
    assert snapshot["live_release_allowed"] is False
    assert snapshot["canary_release_allowed"] is False


def test_active_controls_snapshot_surfaces_gap_accounting_gate(tmp_path: Path) -> None:
    write_dashboard_json(tmp_path, "data/runtime/runtime_safety_audit_config.json", {"riskmanager_approval": True})
    write_dashboard_json(tmp_path, "data/runtime/kill_switch.json", {"active": False})
    write_dashboard_json(tmp_path, "data/reports/risk_recovery_mode_audit_report.json", {"status": "ok"})
    write_dashboard_json(tmp_path, "data/reports/state_reconciliation_audit_report.json", {"status": "ok"})
    write_dashboard_json(
        tmp_path,
        "data/reports/paper_shadow_soak_gap_accounting_report.json",
        {"status": "blocked", "critical_gap_count": 3, "continuous_valid_soak_days": 0.25, "max_gap_minutes": 8013.7},
    )

    snapshot = build_active_controls_snapshot(context(tmp_path))
    section = snapshot["sections"]["readiness_gap_accounting"]

    assert section["status"] == "BLOCKED"
    assert section["critical_gap_count"] == 3
    assert section["live_release_allowed"] is False
    assert section["canary_release_allowed"] is False


def test_quantitative_snapshot_surfaces_gap_accounting_report(tmp_path: Path) -> None:
    write_dashboard_json(tmp_path, "data/reports/paper_financial_performance_metrics_report.json", {"status": "ok", "initial_equity": 1000})
    write_dashboard_json(
        tmp_path,
        "data/reports/paper_shadow_soak_gap_accounting_report.json",
        {"status": "blocked", "critical_gap_count": 1, "warning_gap_count": 1, "max_gap_minutes": 480},
    )

    snapshot = build_quantitative_reports_snapshot(context(tmp_path))
    section = snapshot["sections"]["soak_gap_accounting"]

    assert section["status"] == "BLOCKED"
    assert section["critical_gap_count"] == 1
    assert section["max_gap_minutes"] == 480
    assert section["live_release_allowed"] is False


def test_integration_static_safety_no_orders_or_notifications() -> None:
    files = [
        Path("smartcrypto/ops/runtime_evidence_pack.py"),
        Path("smartcrypto/ops/dashboard_snapshots/active_controls_snapshot_builder.py"),
        Path("smartcrypto/ops/dashboard_snapshots/quantitative_reports_snapshot_builder.py"),
    ]
    forbidden_calls = {"ccxt", "create_order", "cancel_order", "requests", "post", "put", "patch", "delete", "send_email"}
    forbidden_names = {"CommandBus", "NotificationDispatcher"}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert calls.isdisjoint(forbidden_calls)
        assert names.isdisjoint(forbidden_names)
