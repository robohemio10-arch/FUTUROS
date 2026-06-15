from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.runtime_freshness_post_refresh_evidence_gate import (
    post_refresh_gate_rows,
    runtime_freshness_post_refresh_evidence_gate_view,
)
from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.builder_registry import build_all_dashboard_snapshots
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import SAFETY_FLAGS
from smartcrypto.ops.dashboard_snapshots.runtime_evidence_freshness_remediation_producers import (
    audit_runtime_evidence_freshness_remediation_producers,
)
from smartcrypto.ops.dashboard_snapshots.runtime_freshness_post_refresh_evidence_gate import (
    audit_runtime_freshness_post_refresh_evidence_gate,
)
from smartcrypto.ops.dashboard_snapshots.runtime_freshness_producer_contracts import (
    audit_runtime_freshness_producer_contracts,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_runtime_freshness_post_refresh_evidence_gate_v1.py"
NOW = datetime(2026, 6, 15, 21, 0, tzinfo=timezone.utc)


def test_gate_warns_when_artifacts_are_stale_and_blockers_remain(tmp_path: Path) -> None:
    write_artifacts(tmp_path, timestamp="2026-06-15T20:00:00Z")
    payload = audit_gate(tmp_path, now_utc=datetime(2026, 6, 15, 21, 30, tzinfo=timezone.utc))

    assert payload["status"] == "warning"
    assert payload["gate_allowed"] is False
    assert payload["gate_warning_total"] == 3
    assert payload["remaining_freshness_blockers"] == [
        "src_data_reports_market_data_health_audit_report_json:STALE",
        "src_data_runtime_kill_switch_json:STALE",
        "src_data_runtime_runtime_safety_audit_config_json:STALE",
    ]


def test_gate_blocks_missing_artifact(tmp_path: Path) -> None:
    write_artifacts(tmp_path, timestamp="2026-06-15T20:59:00Z")
    (tmp_path / "data/runtime/kill_switch.json").unlink()

    payload = audit_gate(tmp_path)

    assert payload["status"] == "blocked"
    assert "data/runtime/kill_switch.json" in payload["stale_or_invalid_artifacts"]


def test_gate_blocks_invalid_critical_timestamp(tmp_path: Path) -> None:
    write_artifacts(tmp_path, timestamp="not-a-timestamp")

    payload = audit_gate(tmp_path)

    assert payload["status"] == "blocked"
    assert payload["gate_blocked_total"] == 3


def test_gate_blocks_when_blocker_absent_but_artifact_stale(tmp_path: Path) -> None:
    write_artifacts(tmp_path, timestamp="2026-06-15T20:00:00Z")

    payload = audit_gate(
        tmp_path,
        global_blocking_reasons=[],
        global_source_health_status="HEALTHY",
        now_utc=datetime(2026, 6, 15, 21, 30, tzinfo=timezone.utc),
    )

    assert payload["status"] == "blocked"
    assert any(
        indicator.startswith("blocker_absent_with_invalid_artifact:")
        for indicator in payload["bypass_indicators"]
    )


def test_gate_blocks_when_kill_switch_is_disabled(tmp_path: Path) -> None:
    write_artifacts(tmp_path, timestamp="2026-06-15T20:59:00Z", kill_enabled=False)

    payload = audit_gate(tmp_path)

    assert payload["status"] == "blocked"
    assert "kill_switch_enabled_false_or_unsafe" in payload["bypass_indicators"]


def test_gate_blocks_when_any_unsafe_flag_is_true(tmp_path: Path) -> None:
    write_artifacts(tmp_path, timestamp="2026-06-15T20:59:00Z")

    payload = audit_gate(tmp_path, safety_overrides={"order_submission_enabled": True})

    assert payload["status"] == "blocked"
    assert "unsafe_flag_true:order_submission_enabled" in payload["bypass_indicators"]


def test_cli_no_write_and_write_report_are_restricted(tmp_path: Path) -> None:
    write_artifacts(tmp_path, timestamp="2026-06-15T20:00:00Z")
    write_snapshots(tmp_path)
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    no_write = run_cli(tmp_path)
    assert no_write.returncode == 0
    assert json.loads(no_write.stdout)["status"] == "warning"
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before

    write = run_cli(tmp_path, "--write-report")
    assert write.returncode == 0
    output = tmp_path / "data/reports/runtime_freshness_post_refresh_evidence_gate_v1.json"
    assert output.is_file()
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    assert after - before == {
        Path("data/reports/runtime_freshness_post_refresh_evidence_gate_v1.json")
    }


def test_snapshot_integration_preserves_authoritative_blocker_lists() -> None:
    context = create_dashboard_build_context(
        ROOT,
        output_dir=ROOT / "data/reports",
        now_utc=NOW,
        runtime_mode="paper",
        strict=False,
        allow_writes_to_output_dir=False,
    )
    result = build_all_dashboard_snapshots(context)
    summary = result["summary"]
    global_snapshot = result["snapshots"]["dashboard_global_status_snapshot.json"]
    infrastructure = result["snapshots"]["dashboard_infrastructure_snapshot.json"]
    active_controls = result["snapshots"]["dashboard_active_controls_snapshot.json"]

    assert global_snapshot["global_blocking_reasons"] == summary["global_blocking_reasons"]
    assert global_snapshot["runtime_evidence_blocking_reasons"] == summary[
        "runtime_evidence_blocking_reasons"
    ]
    assert global_snapshot["combined_blocking_reasons"] == summary[
        "combined_blocking_reasons"
    ]
    gate = summary["runtime_freshness_post_refresh_evidence_gate"]
    assert gate["gate_allowed"] is False
    assert global_snapshot["runtime_freshness_post_refresh_evidence_gate"] == gate
    assert infrastructure["runtime_freshness_post_refresh_evidence_gate"] == gate
    assert active_controls["runtime_freshness_post_refresh_evidence_gate"] == gate


def test_component_reads_only_materialized_snapshot(tmp_path: Path) -> None:
    write_artifacts(tmp_path, timestamp="2026-06-15T20:00:00Z")
    payload = audit_gate(tmp_path)
    snapshot = {"sections": {"runtime_freshness_post_refresh_evidence_gate": {"data": payload}}}

    assert runtime_freshness_post_refresh_evidence_gate_view(snapshot) == payload
    assert len(post_refresh_gate_rows(payload)) == 3


def test_static_safety_has_no_forbidden_imports_or_calls() -> None:
    paths = (
        ROOT / "smartcrypto/ops/dashboard_snapshots/runtime_freshness_post_refresh_evidence_gate.py",
        ROOT / "smartcrypto/dashboard/components/runtime_freshness_post_refresh_evidence_gate.py",
        SCRIPT,
    )
    forbidden_imports = {"ccxt", "requests", "httpx", "aiohttp", "subprocess", "yaml"}
    forbidden_calls = {
        "create_order",
        "cancel_order",
        "fetch_balance",
        "fetch_open_orders",
        "send_message",
        "post",
        "run",
        "popen",
    }

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".")[0] for alias in node.names}
                assert not (imported & forbidden_imports)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden_imports
            elif isinstance(node, ast.Call):
                assert call_name(node.func) not in forbidden_calls


def audit_gate(
    project_root: Path,
    *,
    global_blocking_reasons: list[str] | None = None,
    global_source_health_status: str = "BLOCKED",
    safety_overrides: dict[str, bool] | None = None,
    now_utc: datetime = NOW,
) -> dict[str, Any]:
    safety = {**SAFETY_FLAGS, **(safety_overrides or {})}
    source_rows = source_health_rows()
    producer_audit = audit_runtime_evidence_freshness_remediation_producers(
        now_utc=NOW,
        dashboard_status="BLOCKED",
        global_source_health_status=global_source_health_status,
        source_health_matrix=source_rows,
        safety_payloads=[safety],
    )
    contracts = audit_runtime_freshness_producer_contracts(
        now_utc=NOW,
        producer_audit=producer_audit,
        safety_payloads=[safety],
    )
    summary = {
        "dashboard_status": "BLOCKED",
        "global_source_health_status": global_source_health_status,
        "global_blocking_reasons": (
            global_blocking_reasons
            if global_blocking_reasons is not None
            else [
                "src_data_reports_market_data_health_audit_report_json:STALE",
                "src_data_runtime_kill_switch_json:STALE",
                "src_data_runtime_runtime_safety_audit_config_json:STALE",
            ]
        ),
        "source_health_matrix": source_rows,
        "safety_flags": safety,
    }
    return audit_runtime_freshness_post_refresh_evidence_gate(
        project_root=project_root,
        now_utc=now_utc,
        summary=summary,
        global_snapshot=summary,
        producer_contracts=contracts,
        producer_audit=producer_audit,
    )


def write_snapshots(project_root: Path) -> None:
    reports = project_root / "data/reports"
    reports.mkdir(parents=True, exist_ok=True)
    producer_audit = audit_runtime_evidence_freshness_remediation_producers(
        now_utc=NOW,
        dashboard_status="BLOCKED",
        global_source_health_status="BLOCKED",
        source_health_matrix=source_health_rows(),
        safety_payloads=[dict(SAFETY_FLAGS)],
    )
    contracts = audit_runtime_freshness_producer_contracts(
        now_utc=NOW,
        producer_audit=producer_audit,
        safety_payloads=[dict(SAFETY_FLAGS)],
    )
    snapshot = {
        "dashboard_status": "BLOCKED",
        "global_source_health_status": "BLOCKED",
        "global_blocking_reasons": [
            "src_data_reports_market_data_health_audit_report_json:STALE",
            "src_data_runtime_kill_switch_json:STALE",
            "src_data_runtime_runtime_safety_audit_config_json:STALE",
        ],
        "source_health_matrix": source_health_rows(),
        "runtime_freshness_producer_contracts": contracts,
        "runtime_evidence_freshness_remediation_producers": producer_audit,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    for name in ("dashboard_snapshot_build_summary.json", "dashboard_global_status_snapshot.json"):
        (reports / name).write_text(json.dumps(snapshot), encoding="utf-8")


def write_artifacts(
    project_root: Path,
    *,
    timestamp: str,
    kill_enabled: bool = True,
) -> None:
    (project_root / "data/reports").mkdir(parents=True, exist_ok=True)
    (project_root / "data/runtime").mkdir(parents=True, exist_ok=True)
    (project_root / "data/reports/market_data_health_audit_report.json").write_text(
        json.dumps({"status": "ok", "generated_at_utc": timestamp}),
        encoding="utf-8",
    )
    (project_root / "data/runtime/kill_switch.json").write_text(
        json.dumps({"enabled": kill_enabled, "updated_at": timestamp}),
        encoding="utf-8",
    )
    runtime_safety = {
        "status": "ok",
        "generated_at_utc": timestamp,
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
    }
    (project_root / "data/runtime/runtime_safety_audit_config.json").write_text(
        json.dumps(runtime_safety),
        encoding="utf-8",
    )


def source_health_rows() -> list[dict[str, Any]]:
    return [
        {
            "source_id": "src_data_reports_market_data_health_audit_report_json",
            "canonical_path": "data/reports/market_data_health_audit_report.json",
            "status": "STALE",
            "health_status": "BLOCKED",
            "freshness_status": "CRITICAL_STALE",
            "age_seconds": 3600.0,
            "max_age_seconds": 300.0,
            "critical_age_seconds": 300.0,
            "effective_timestamp_utc": "2026-06-15T20:00:00Z",
            "severity": "CRITICAL",
            "blocks_dashboard_readiness": True,
        },
        {
            "source_id": "src_data_runtime_kill_switch_json",
            "canonical_path": "data/runtime/kill_switch.json",
            "status": "STALE",
            "health_status": "BLOCKED",
            "freshness_status": "CRITICAL_STALE",
            "age_seconds": 3600.0,
            "max_age_seconds": 900.0,
            "critical_age_seconds": 900.0,
            "effective_timestamp_utc": "2026-06-15T20:00:00Z",
            "severity": "CRITICAL",
            "blocks_dashboard_readiness": True,
        },
        {
            "source_id": "src_data_runtime_runtime_safety_audit_config_json",
            "canonical_path": "data/runtime/runtime_safety_audit_config.json",
            "status": "STALE",
            "health_status": "BLOCKED",
            "freshness_status": "CRITICAL_STALE",
            "age_seconds": 3600.0,
            "max_age_seconds": 900.0,
            "critical_age_seconds": 900.0,
            "effective_timestamp_utc": "2026-06-15T20:00:00Z",
            "severity": "CRITICAL",
            "blocks_dashboard_readiness": True,
        },
    ]


def run_cli(project_root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(project_root),
            "--json",
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return ""
