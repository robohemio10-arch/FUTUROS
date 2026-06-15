from __future__ import annotations

import ast
import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.runtime_evidence_freshness_remediation_producers import (
    freshness_manual_plan_rows,
    freshness_producer_rows,
    freshness_verification_rows,
    runtime_evidence_freshness_remediation_producers_view,
)
from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.builder_registry import build_all_dashboard_snapshots
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import SAFETY_FLAGS
from smartcrypto.ops.dashboard_snapshots.runtime_evidence_freshness_remediation_producers import (
    audit_runtime_evidence_freshness_remediation_producers,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "scripts"
    / "audit_runtime_evidence_freshness_remediation_producers_v1.py"
)
NOW = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)


def test_maps_all_three_current_freshness_blockers() -> None:
    payload = audit(source_rows())

    assert payload["status"] == "warning"
    assert payload["freshness_blockers_total"] == 3
    assert payload["critical_freshness_blockers_total"] == 3
    assert payload["unmapped_critical_freshness_blockers"] == []
    assert [row["producer_id"] for row in payload["producer_rows"]] == [
        "market_data_health_audit",
        "kill_switch_state_refresh",
        "runtime_safety_config_validation",
    ]
    assert payload["blocked_until_refreshed_sources"] == sorted(
        row["canonical_path"] for row in source_rows()
    )


def test_every_producer_row_has_stable_read_only_contract() -> None:
    required = {
        "producer_id",
        "domain",
        "target_source_id",
        "target_canonical_path",
        "current_status",
        "current_health_status",
        "current_freshness_status",
        "age_seconds",
        "max_age_seconds",
        "critical_age_seconds",
        "effective_timestamp_utc",
        "timestamp_valid",
        "manual_command_hint",
        "expected_output_path",
        "expected_schema_version",
        "verification_command",
        "post_refresh_closeout_condition",
        "execution_location",
        "execution_allowed",
        "safe_to_execute_from_dashboard",
        "requires_manual_operator",
        "changes_runtime",
        "changes_risk",
        "changes_model",
        "sends_orders",
        "sends_notifications",
    }

    for row in audit(source_rows())["producer_rows"]:
        assert required <= set(row)
        assert row["execution_location"] == "manual_outside_dashboard"
        assert row["execution_allowed"] is False
        assert row["safe_to_execute_from_dashboard"] is False
        assert row["requires_manual_operator"] is True
        assert row["changes_runtime"] is False
        assert row["changes_risk"] is False
        assert row["changes_model"] is False
        assert row["sends_orders"] is False
        assert row["sends_notifications"] is False


def test_blocks_critical_freshness_source_without_mapping() -> None:
    rows = source_rows()
    rows.append(
        {
            **rows[0],
            "source_id": "src_unmapped_critical_freshness",
            "canonical_path": "data/reports/unmapped_critical_freshness.json",
        }
    )

    payload = audit(rows)

    assert payload["status"] == "blocked"
    assert payload["unmapped_critical_freshness_blockers"] == [
        "src_unmapped_critical_freshness"
    ]


def test_blocks_invalid_critical_timestamp() -> None:
    rows = source_rows()
    rows[1]["effective_timestamp_utc"] = "invalid-timestamp"
    rows[1]["status"] = "INVALID_TIMESTAMP"
    rows[1]["freshness_status"] = "INVALID_TIMESTAMP"

    payload = audit(rows)

    assert payload["status"] == "blocked"
    assert payload["invalid_critical_timestamp_sources"] == [
        "src_data_runtime_kill_switch_json"
    ]


def test_blocks_when_any_safety_payload_is_unsafe() -> None:
    unsafe = {**SAFETY_FLAGS, "order_submission_enabled": True}

    payload = audit(source_rows(), safety_payloads=[SAFETY_FLAGS, unsafe])

    assert payload["status"] == "blocked"
    assert payload["safety_violations"] == ["order_submission_enabled"]


def test_blocks_when_required_snapshot_inputs_are_unavailable() -> None:
    payload = audit_runtime_evidence_freshness_remediation_producers(
        now_utc=NOW,
        dashboard_status="UNKNOWN",
        global_source_health_status="UNKNOWN",
        source_health_matrix=[],
        safety_payloads=[dict(SAFETY_FLAGS)],
        input_errors=[
            "missing_or_invalid:data/reports/dashboard_snapshot_build_summary.json"
        ],
    )

    assert payload["status"] == "blocked"
    assert payload["input_errors"] == [
        "missing_or_invalid:data/reports/dashboard_snapshot_build_summary.json"
    ]


def test_cli_no_write_and_write_report_are_restricted(tmp_path: Path) -> None:
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    summary = {
        "dashboard_status": "BLOCKED",
        "global_source_health_status": "BLOCKED",
        "source_health_matrix": source_rows(),
        "safety_flags": dict(SAFETY_FLAGS),
    }
    (reports / "dashboard_snapshot_build_summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    (reports / "dashboard_global_status_snapshot.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    no_write = run_cli(tmp_path)
    assert no_write.returncode == 0
    assert json.loads(no_write.stdout)["status"] == "warning"
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before

    write = run_cli(tmp_path, "--write-report")
    assert write.returncode == 0
    output = (
        reports / "runtime_evidence_freshness_remediation_producers_audit_v1.json"
    )
    assert output.is_file()
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    assert after - before == {
        Path(
            "data/reports/runtime_evidence_freshness_remediation_producers_audit_v1.json"
        )
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
    producer_audit = summary["runtime_evidence_freshness_remediation_producers"]
    assert global_snapshot["runtime_evidence_freshness_remediation_producers"] == producer_audit
    assert infrastructure["runtime_evidence_freshness_remediation_producers"] == producer_audit
    assert active_controls["runtime_evidence_freshness_remediation_producers"] == producer_audit
    assert "runtime_evidence_freshness_remediation_producers" in infrastructure["sections"]
    assert "runtime_evidence_freshness_remediation_producers" in active_controls["sections"]


def test_component_reads_only_materialized_snapshot() -> None:
    payload = audit(source_rows())
    snapshot = {
        "sections": {
            "runtime_evidence_freshness_remediation_producers": {"data": payload}
        }
    }

    assert runtime_evidence_freshness_remediation_producers_view(snapshot) == payload
    assert len(freshness_producer_rows(payload)) == 3
    assert len(freshness_manual_plan_rows(payload)) == 3
    assert len(freshness_verification_rows(payload)) == 4


def test_static_safety_has_no_forbidden_imports_or_calls() -> None:
    paths = (
        ROOT
        / "smartcrypto/ops/dashboard_snapshots/runtime_evidence_freshness_remediation_producers.py",
        ROOT
        / "smartcrypto/dashboard/components/runtime_evidence_freshness_remediation_producers.py",
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
                assert not ({alias.name.split(".")[0] for alias in node.names} & forbidden_imports)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden_imports
            elif isinstance(node, ast.Call):
                assert call_name(node.func) not in forbidden_calls


def source_rows() -> list[dict[str, Any]]:
    definitions = (
        (
            "src_data_reports_market_data_health_audit_report_json",
            "market_data",
            "data/reports/market_data_health_audit_report.json",
            300.0,
        ),
        (
            "src_data_runtime_kill_switch_json",
            "portfolio_risk",
            "data/runtime/kill_switch.json",
            900.0,
        ),
        (
            "src_data_runtime_runtime_safety_audit_config_json",
            "active_controls",
            "data/runtime/runtime_safety_audit_config.json",
            900.0,
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
            "age_seconds": threshold + 1200.0,
            "max_age_seconds": threshold,
            "critical_age_seconds": threshold,
            "effective_timestamp_utc": "2026-06-15T17:30:00Z",
            "expected_schema_version": None,
            "severity": "CRITICAL",
            "blocks_dashboard_readiness": True,
        }
        for source_id, domain, path, threshold in definitions
    ]


def audit(
    rows: list[dict[str, Any]],
    *,
    safety_payloads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return audit_runtime_evidence_freshness_remediation_producers(
        now_utc=NOW,
        dashboard_status="BLOCKED",
        global_source_health_status="BLOCKED",
        source_health_matrix=deepcopy(rows),
        safety_payloads=safety_payloads or [dict(SAFETY_FLAGS)],
    )


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
