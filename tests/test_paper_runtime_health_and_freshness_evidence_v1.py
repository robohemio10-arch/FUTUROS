from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.active_controls_snapshot_builder import build_active_controls_snapshot
from smartcrypto.ops.dashboard_snapshots.infrastructure_snapshot_builder import build_infrastructure_snapshot
from smartcrypto.ops.paper_runtime_health_and_freshness import audit_paper_runtime_health_and_freshness
from smartcrypto.ops.runtime_evidence_pack import build_runtime_evidence_pack_and_readiness_snapshot_v2
from tests.dashboard_builder_test_support import context, write_json as write_dashboard_json
from tests.test_runtime_evidence_pack_and_readiness_snapshot_v2 import ROOT

FIXED_NOW = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
PAPER_RUNTIME_REPORT = Path("data/reports/paper_runtime_health_and_freshness_report.json")
HEALTH_CLI = ROOT / "scripts" / "audit_paper_runtime_health_and_freshness.py"
RUNTIME_CLI = ROOT / "scripts" / "build_runtime_evidence_pack_and_readiness_snapshot_v2.py"


def write_runtime_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def safe_report(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ok",
        "created_at": "2026-06-11T11:58:00Z",
        "generated_at": "2026-06-11T11:58:00Z",
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_training_dataset": False,
        "writes_trades_master": False,
    }
    payload.update(extra)
    return payload


def write_runtime_health_sources(root: Path) -> None:
    reports = root / "data" / "reports"
    names = (
        "phase14_runtime_feedback_sync_report",
        "phase14_summary",
        "phase14_output_summary",
        "phase14_closed_feedback_report",
        "phase14_open_positions_report",
        "qlib_paper_refresh_supervisor_report",
        "qlib_market_features_refresh_report",
        "qlib_fresh_prediction_runner_report",
        "dashboard_snapshot_build_summary",
        "runtime_evidence_pack_v2",
        "readiness_snapshot_v2",
        "paper_shadow_soak_gap_accounting_report",
        "trade_event_notifications_report",
    )
    for name in names:
        write_runtime_json(reports / f"{name}.json", safe_report())
    write_runtime_json(
        reports / "qlib_paper_refresh_supervisor_report.json",
        safe_report(input_data_status="input_data_fresh", phase13_status="ok", signals_after={"active_signal_count": 2}),
    )
    write_runtime_json(
        reports / "phase14_runtime_feedback_sync_report.json",
        safe_report(runtime_mode="paper", raw_rows=316, closed_rows=314, open_rows=2),
    )


def write_compose(root: Path) -> None:
    services = [
        "freqtrade-paper",
        "phase14-feedback-sync-paper",
        "qlib-refresh-supervisor-paper",
        "smartcrypto-bot-paper",
        "smartcrypto-dashboard-paper",
        "trade-event-notifications-paper",
    ]
    root.joinpath("docker-compose.paper.yml").write_text(
        "services:\n" + "".join(f"  {service}:\n    image: test\n" for service in services),
        encoding="utf-8",
    )


def complete_health_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    reports = root / "data" / "reports"
    gap_payload = safe_report(
        generated_at_utc="2026-06-11T11:58:00Z",
        observed_soak_days=31,
        observed_calendar_days=31,
        continuous_valid_soak_days=31,
        critical_gap_count=0,
        warning_gap_count=0,
        max_gap_minutes=0,
        readiness_gap_free=True,
        seven_day_diagnostic_status="reached",
        thirty_day_readiness_status="ready",
    )
    for name in (
        "paper_soak_report",
        "paper_shadow_soak_report",
        "paper_shadow_soak_continuity_audit",
        "paper_shadow_soak_anchor_continuity_pack",
        "runtime_evidence_pack_v2",
        "readiness_snapshot_v2",
        "paper_shadow_soak_gap_accounting_report",
    ):
        write_runtime_json(reports / f"{name}.json", gap_payload)
    write_runtime_json(reports / "freqtrade_paper_db_authority_report.json", safe_report(selected_db="data/snapshots/freqtrade_paper.sqlite"))
    write_runtime_json(reports / "readiness_gate_report.json", safe_report(readiness_approved=True))
    write_runtime_json(reports / "monte_carlo_risk_simulation_report.json", safe_report(policy_action="allow_paper"))
    write_runtime_health_sources(root)
    write_compose(root)
    return root


def test_health_auditor_default_is_read_only_and_fresh(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)
    report_path = root / PAPER_RUNTIME_REPORT

    report = audit_paper_runtime_health_and_freshness(project_root=root, write=False, now=FIXED_NOW)

    assert report["status"] == "ok"
    assert report["paper_runtime_alive"] is True
    assert report["paper_runtime_fresh"] is True
    assert report["critical_stale_count"] == 0
    assert report["write_performed"] is False
    assert report["report_materialized"] is False
    assert report["sends_orders"] is False
    assert report["live_release_allowed"] is False
    assert not report_path.exists()


def test_health_auditor_write_materializes_runtime_report(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)
    report_path = root / PAPER_RUNTIME_REPORT

    report = audit_paper_runtime_health_and_freshness(project_root=root, write=True, now=FIXED_NOW)

    assert report["write_performed"] is True
    assert report["report_materialized"] is True
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "paper_runtime_health_and_freshness_v1"
    assert payload["paper_runtime_fresh"] is True
    assert payload["order_submission_enabled"] is False


def test_health_auditor_blocks_on_required_stale_report(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)
    write_runtime_json(root / "data" / "reports" / "phase14_runtime_feedback_sync_report.json", safe_report(created_at="2026-06-11T11:00:00Z"))

    report = audit_paper_runtime_health_and_freshness(project_root=root, write=False, now=FIXED_NOW)

    assert report["status"] == "blocked"
    assert report["paper_runtime_fresh"] is False
    assert "phase14_runtime_feedback_sync_report" in report["stale_required_sources"]
    assert report["canary_release_allowed"] is False
    assert report["live_release_allowed"] is False


def test_runtime_evidence_materializes_and_embeds_health_report(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)
    report_path = root / PAPER_RUNTIME_REPORT

    result = build_runtime_evidence_pack_and_readiness_snapshot_v2(
        project_root=root,
        output_dir=root / "data" / "reports",
        no_write=False,
        now=FIXED_NOW,
    )

    summary = result.readiness_snapshot["paper_runtime_health_and_freshness"]
    assert report_path.exists()
    assert summary["paper_runtime_alive"] is True
    assert summary["paper_runtime_fresh"] is True
    assert summary["report_materialized"] is True
    assert result.evidence_pack["output_paths"]["paper_runtime_health_and_freshness_report"] == str(report_path)
    assert result.readiness_snapshot["live_release_allowed"] is False
    assert result.readiness_snapshot["canary_release_allowed"] is False


def test_runtime_evidence_cli_surfaces_paper_runtime_summary(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)

    completed = subprocess.run(
        [sys.executable, str(RUNTIME_CLI), "--project-root", str(root), "--output-dir", str(root / "data" / "reports"), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["paper_runtime_health_and_freshness_report_materialized"] is True
    assert "paper_runtime_alive" in payload
    assert "paper_runtime_fresh" in payload
    assert payload["sends_orders"] is False


def test_health_cli_default_read_only(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)
    report_path = root / PAPER_RUNTIME_REPORT

    completed = subprocess.run(
        [sys.executable, str(HEALTH_CLI), "--project-root", str(root), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["write_performed"] is False
    assert "paper_runtime_fresh" in payload
    assert payload["order_submission_enabled"] is False
    assert not report_path.exists()


def test_dashboard_snapshots_surface_paper_runtime_health(tmp_path: Path) -> None:
    root = complete_health_project(tmp_path)
    audit_paper_runtime_health_and_freshness(project_root=root, write=True, now=FIXED_NOW)
    write_dashboard_json(root, "data/reports/system_healthcheck_report.json", safe_report())
    write_dashboard_json(root, "data/reports/market_data_health_audit_report.json", safe_report())
    write_dashboard_json(root, "data/reports/market_data_health_runtime_sources_report.json", safe_report(last_candle_timestamp_utc="2026-06-11T11:59:00Z"))
    write_dashboard_json(root, "data/runtime/runtime_safety_audit_config.json", {"riskmanager_approval": True})
    write_dashboard_json(root, "data/runtime/kill_switch.json", {"active": False})
    write_dashboard_json(root, "data/reports/risk_recovery_mode_audit_report.json", safe_report())
    write_dashboard_json(root, "data/reports/state_reconciliation_audit_report.json", safe_report(status="ok"))

    infrastructure = build_infrastructure_snapshot(context(root))
    active_controls = build_active_controls_snapshot(context(root))

    assert str(PAPER_RUNTIME_REPORT) not in infrastructure["missing_optional_sources"]
    assert str(PAPER_RUNTIME_REPORT) not in active_controls["missing_optional_sources"]
    assert infrastructure["sections"]["paper_runtime_health"]["status"] == "OK"
    assert active_controls["sections"]["paper_runtime_health"]["status"] == "OK"
    assert active_controls["sections"]["paper_runtime_health"]["live_release_allowed"] is False


def test_static_safety_no_orders_network_or_dispatchers() -> None:
    files = [
        Path("smartcrypto/ops/paper_runtime_health_and_freshness/auditor.py"),
        Path("scripts/audit_paper_runtime_health_and_freshness.py"),
        Path("smartcrypto/ops/runtime_evidence_pack.py"),
        Path("smartcrypto/ops/dashboard_snapshots/infrastructure_snapshot_builder.py"),
        Path("smartcrypto/ops/dashboard_snapshots/active_controls_snapshot_builder.py"),
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
