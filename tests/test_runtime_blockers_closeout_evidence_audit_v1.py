from __future__ import annotations

import ast
import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.runtime_blockers_closeout_evidence import (
    closeout_evidence_rows,
    closeout_issue_rows,
    closeout_safety_rows,
    runtime_blockers_closeout_evidence_view,
)
from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.builder_registry import build_all_dashboard_snapshots
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_closeout_evidence import (
    audit_runtime_blockers_closeout_evidence,
)
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_operator_pack import (
    build_runtime_blockers_operator_pack,
)
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import (
    SAFETY_FLAGS,
    build_runtime_blockers_remediation,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_runtime_blockers_closeout_evidence_v1.py"
NOW = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
GLOBAL_REASONS = [
    "src_data_reports_market_data_health_audit_report_json:STALE",
    "src_data_runtime_kill_switch_json:STALE",
    "src_data_runtime_runtime_safety_audit_config_json:STALE",
]
RUNTIME_REASONS = [
    "source_health:BLOCKED",
    "runtime_evidence_pack:BLOCKED",
    "readiness:BLOCKED",
    "paper_shadow_soak_gap_accounting:BLOCKED",
]


def test_warning_when_blockers_remain_materialized_and_auditable(tmp_path: Path) -> None:
    inputs = audit_inputs(tmp_path)

    payload = run_audit(tmp_path, inputs)

    assert payload["status"] == "warning"
    assert payload["reason"] == "blockers_remain_materialized_and_auditable"
    assert payload["closeout_allowed"] is False
    assert payload["bypass_indicators"] == []
    assert payload["suspicious_closeouts"] == []
    assert len(payload["current_global_blocking_reasons"]) == 3
    assert len(payload["current_runtime_evidence_blocking_reasons"]) == 4


def test_blocks_when_global_reasons_are_artificially_emptied(tmp_path: Path) -> None:
    inputs = audit_inputs(tmp_path)
    inputs["global_snapshot"]["global_blocking_reasons"] = []
    inputs["global_snapshot"]["combined_blocking_reasons"] = RUNTIME_REASONS

    payload = run_audit(tmp_path, inputs)

    assert payload["status"] == "blocked"
    assert "global_blocking_reasons_empty_while_source_health_blocked" in payload[
        "bypass_indicators"
    ]


def test_blocks_when_runtime_reasons_are_artificially_emptied(tmp_path: Path) -> None:
    inputs = audit_inputs(tmp_path)
    inputs["global_snapshot"]["runtime_evidence_blocking_reasons"] = []
    inputs["global_snapshot"]["combined_blocking_reasons"] = GLOBAL_REASONS

    payload = run_audit(tmp_path, inputs)

    assert payload["status"] == "blocked"
    assert "runtime_evidence_blocking_reasons_empty_while_evidence_blocked" in payload[
        "bypass_indicators"
    ]


def test_blocks_unsafe_safety_flags(tmp_path: Path) -> None:
    inputs = audit_inputs(tmp_path)
    inputs["summary"]["live_release_allowed"] = True

    payload = run_audit(tmp_path, inputs)

    assert payload["status"] == "blocked"
    assert "unsafe_flag_true:live_release_allowed" in payload["bypass_indicators"]
    assert "live_release_allowed" in payload["safety_violations"]
    assert payload["closeout_allowed"] is False


def test_detects_invalid_timestamp_for_critical_freshness_source(tmp_path: Path) -> None:
    inputs = audit_inputs(tmp_path)
    source = next(
        row
        for row in inputs["source_health_matrix"]
        if row["source_id"] == "src_data_runtime_kill_switch_json"
    )
    source["effective_timestamp_utc"] = "not-a-timestamp"
    source["freshness_status"] = "INVALID_TIMESTAMP"
    source["status"] = "INVALID_TIMESTAMP"

    payload = run_audit(tmp_path, inputs)

    assert payload["status"] == "blocked"
    assert "data/runtime/kill_switch.json" in payload["invalid_timestamp_sources"]
    assert (
        "freshness_required_timestamp_invalid:src_data_runtime_kill_switch_json"
        in payload["bypass_indicators"]
    )


def test_cli_no_write_and_write_report_are_restricted(tmp_path: Path) -> None:
    inputs = audit_inputs(tmp_path)
    write_json(tmp_path, "data/reports/dashboard_snapshot_build_summary.json", inputs["summary"])
    write_json(tmp_path, "data/reports/dashboard_global_status_snapshot.json", inputs["global_snapshot"])
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    no_write = run_cli(tmp_path)
    assert no_write.returncode == 0
    assert json.loads(no_write.stdout)["status"] == "warning"
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before

    write = run_cli(tmp_path, "--write-report")
    assert write.returncode == 0
    output = tmp_path / "data/reports/runtime_blockers_closeout_evidence_audit_v1.json"
    assert output.is_file()
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    assert after - before == {
        Path("data/reports/runtime_blockers_closeout_evidence_audit_v1.json")
    }


def test_snapshot_integration_does_not_change_blocker_lists() -> None:
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
    audit = summary["runtime_blockers_closeout_evidence"]
    assert global_snapshot["runtime_blockers_closeout_evidence"] == audit
    assert infrastructure["runtime_blockers_closeout_evidence"] == audit
    assert active_controls["runtime_blockers_closeout_evidence"] == audit
    assert "runtime_blockers_closeout_evidence" in infrastructure["sections"]
    assert "runtime_blockers_closeout_evidence" in active_controls["sections"]


def test_component_reads_only_snapshot_payload(tmp_path: Path) -> None:
    payload = run_audit(tmp_path, audit_inputs(tmp_path))
    snapshot = {"sections": {"runtime_blockers_closeout_evidence": {"data": payload}}}

    assert runtime_blockers_closeout_evidence_view(snapshot) == payload
    assert closeout_evidence_rows(payload)
    assert closeout_safety_rows(payload)
    assert closeout_issue_rows(payload) == [
        {"category": "stale_evidence_sources", "value": path}
        for path in sorted(
            (
                "data/reports/market_data_health_audit_report.json",
                "data/runtime/kill_switch.json",
                "data/runtime/runtime_safety_audit_config.json",
            )
        )
    ]


def test_static_safety_has_no_forbidden_imports_or_calls() -> None:
    paths = (
        ROOT / "smartcrypto/ops/dashboard_snapshots/runtime_blockers_closeout_evidence.py",
        ROOT / "smartcrypto/dashboard/components/runtime_blockers_closeout_evidence.py",
        ROOT / "scripts/audit_runtime_blockers_closeout_evidence_v1.py",
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
                assert not ({alias.name.split(".")[0] for alias in node.names} & forbidden_imports)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden_imports
            elif isinstance(node, ast.Call):
                assert call_name(node.func) not in forbidden_calls


def audit_inputs(root: Path) -> dict[str, Any]:
    source_rows = source_health_rows()
    remediation = build_runtime_blockers_remediation(
        now_utc=NOW,
        dashboard_status="BLOCKED",
        global_source_health_status="BLOCKED",
        runtime_evidence_integration_status="BLOCKED",
        global_blocking_reasons=GLOBAL_REASONS,
        runtime_evidence_blocking_reasons=RUNTIME_REASONS,
        source_health_matrix=source_rows,
    )
    operator_pack = build_runtime_blockers_operator_pack(
        remediation=remediation,
        now_utc=NOW,
    )
    combined = sorted(GLOBAL_REASONS + RUNTIME_REASONS)
    base = {
        "dashboard_status": "BLOCKED",
        "global_source_health_status": "BLOCKED",
        "runtime_evidence_integration_status": "BLOCKED",
        "global_blocking_reasons": list(GLOBAL_REASONS),
        "runtime_evidence_blocking_reasons": list(RUNTIME_REASONS),
        "combined_blocking_reasons": combined,
        "source_health_matrix": source_rows,
        "runtime_blockers_remediation": remediation,
        "runtime_blockers_operator_pack": operator_pack,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    materialize_evidence(root)
    return {
        "summary": deepcopy(base),
        "global_snapshot": deepcopy(base),
        "remediation": remediation,
        "operator_pack": operator_pack,
        "source_health_matrix": source_rows,
    }


def source_health_rows() -> list[dict[str, Any]]:
    definitions = (
        (
            "src_data_reports_market_data_health_audit_report_json",
            "market_data",
            "data/reports/market_data_health_audit_report.json",
        ),
        (
            "src_data_runtime_kill_switch_json",
            "portfolio_risk",
            "data/runtime/kill_switch.json",
        ),
        (
            "src_data_runtime_runtime_safety_audit_config_json",
            "active_controls",
            "data/runtime/runtime_safety_audit_config.json",
        ),
    )
    return [
        {
            "source_id": source_id,
            "owner_domain": domain,
            "canonical_path": path,
            "status": "STALE",
            "health_status": "BLOCKED",
            "freshness_status": "CRITICAL_STALE",
            "freshness_required": True,
            "effective_timestamp_utc": "2026-06-15T17:30:00Z",
            "age_seconds": 1800.0,
            "required_level": "REQUIRED",
            "severity": "CRITICAL",
            "blocks_dashboard_readiness": True,
            "blocks_page_operational_view": True,
            "producer_hint": f"Documented producer for {path}",
            "runbook_hint": f"Consult the {domain} runbook.",
            "remediation_action": f"Refresh {path} manually.",
        }
        for source_id, domain, path in definitions
    ]


def materialize_evidence(root: Path) -> None:
    write_json(
        root,
        "data/reports/runtime_evidence_pack_v2.json",
        {"schema_version": "runtime_evidence_pack_v2", "status": "blocked"},
    )
    write_json(
        root,
        "data/reports/readiness_snapshot_v2.json",
        {"schema_version": "runtime_evidence_pack_v2", "status": "blocked"},
    )
    write_json(
        root,
        "data/reports/paper_shadow_soak_gap_accounting_report.json",
        {
            "schema_version": "paper_shadow_soak_continuity_gap_accounting_v1",
            "status": "blocked",
            "generated_at_utc": "2026-06-15T17:30:00Z",
        },
    )
    write_json(
        root,
        "data/reports/market_data_health_audit_report.json",
        {"status": "blocked", "generated_at_utc": "2026-06-15T17:30:00Z"},
    )
    write_json(
        root,
        "data/runtime/kill_switch.json",
        {"enabled": True, "updated_at": "2026-06-15T17:30:00+00:00"},
    )
    write_json(
        root,
        "data/runtime/runtime_safety_audit_config.json",
        dict(SAFETY_FLAGS),
    )


def run_audit(root: Path, inputs: dict[str, Any]) -> dict[str, Any]:
    return audit_runtime_blockers_closeout_evidence(
        project_root=root,
        now_utc=NOW,
        **inputs,
    )


def write_json(root: Path, relative_path: str, payload: object) -> None:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


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
