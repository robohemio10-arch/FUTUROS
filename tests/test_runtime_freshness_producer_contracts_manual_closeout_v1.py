from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.runtime_freshness_producer_contracts import (
    producer_contract_artifact_rows,
    producer_contract_closeout_rows,
    producer_contract_rows,
    runtime_freshness_producer_contracts_view,
)
from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.builder_registry import build_all_dashboard_snapshots
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import SAFETY_FLAGS
from smartcrypto.ops.dashboard_snapshots.runtime_evidence_freshness_remediation_producers import (
    audit_runtime_evidence_freshness_remediation_producers,
)
from smartcrypto.ops.dashboard_snapshots.runtime_freshness_producer_contracts import (
    CONTRACT_DEFINITIONS,
    audit_runtime_freshness_producer_contracts,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_runtime_freshness_producer_contracts_v1.py"
NOW = datetime(2026, 6, 15, 19, 0, tzinfo=timezone.utc)


def test_all_three_required_producer_contracts_exist() -> None:
    payload = audit_contracts()

    assert payload["status"] == "warning"
    assert payload["contracts_total"] == 3
    assert payload["contracts_ready_total"] == 3
    assert payload["contracts_blocked_total"] == 0
    assert payload["manual_closeout_allowed"] is False
    assert [row["producer_id"] for row in payload["producer_contracts"]] == [
        "market_data_health_audit",
        "kill_switch_state_refresh",
        "runtime_safety_config_validation",
    ]


def test_each_contract_has_manual_execution_and_closeout_evidence() -> None:
    required = {
        "contract_id",
        "producer_id",
        "domain",
        "target_source_id",
        "target_canonical_path",
        "current_status",
        "current_freshness_status",
        "current_health_status",
        "entry_criteria",
        "manual_execution_hint",
        "expected_artifact_path",
        "expected_schema_version",
        "expected_timestamp_field",
        "max_acceptable_age_seconds_after_refresh",
        "verification_commands",
        "post_refresh_success_criteria",
        "manual_closeout_condition",
        "rollback_or_abort_condition",
        "operator_notes",
        "execution_location",
        "requires_manual_operator",
        "execution_allowed",
        "safe_to_execute_from_dashboard",
        "changes_runtime",
        "changes_risk",
        "changes_model",
        "sends_orders",
        "sends_notifications",
    }

    for contract in audit_contracts()["producer_contracts"]:
        assert required <= set(contract)
        assert contract["entry_criteria"]
        assert contract["expected_artifact_path"]
        assert contract["verification_commands"]
        assert contract["manual_closeout_condition"]
        assert contract["execution_location"] == "manual_outside_dashboard"
        assert contract["requires_manual_operator"] is True
        assert contract["execution_allowed"] is False
        assert contract["safe_to_execute_from_dashboard"] is False
        assert contract["changes_runtime"] is False
        assert contract["changes_risk"] is False
        assert contract["changes_model"] is False
        assert contract["sends_orders"] is False
        assert contract["sends_notifications"] is False


def test_auditor_blocks_missing_required_contract() -> None:
    definitions = dict(CONTRACT_DEFINITIONS)
    definitions.pop("kill_switch_state_refresh")

    payload = audit_contracts(contract_definitions=definitions)

    assert payload["status"] == "blocked"
    assert payload["missing_required_contracts"] == ["kill_switch_state_refresh"]
    assert payload["manual_closeout_allowed"] is False


def test_auditor_blocks_incomplete_contract() -> None:
    definitions = dict(CONTRACT_DEFINITIONS)
    definitions["market_data_health_audit"] = replace(
        definitions["market_data_health_audit"],
        manual_closeout_condition="",
    )

    payload = audit_contracts(contract_definitions=definitions)

    assert payload["status"] == "blocked"
    assert payload["incomplete_contracts"] == [
        "market_data_health_manual_refresh_v1"
    ]


def test_auditor_preserves_and_enforces_safety_flags() -> None:
    unsafe = {**SAFETY_FLAGS, "live_release_allowed": True}

    payload = audit_contracts(safety_payloads=[unsafe])

    assert payload["status"] == "blocked"
    assert payload["safety_violations"] == ["live_release_allowed"]
    assert payload["safety_flags"]["paper_only"] is True
    assert payload["safety_flags"]["shadow_only"] is True
    assert payload["safety_flags"]["order_submission_enabled"] is False


def test_auditor_returns_ok_without_critical_freshness_blockers() -> None:
    producer_audit = audit_runtime_evidence_freshness_remediation_producers(
        now_utc=NOW,
        dashboard_status="BLOCKED",
        global_source_health_status="HEALTHY",
        source_health_matrix=[],
        safety_payloads=[dict(SAFETY_FLAGS)],
    )

    payload = audit_runtime_freshness_producer_contracts(
        now_utc=NOW,
        producer_audit=producer_audit,
        safety_payloads=[dict(SAFETY_FLAGS)],
    )

    assert payload["status"] == "ok"
    assert payload["contracts_ready_total"] == 3
    assert payload["manual_closeout_allowed"] is True


def test_cli_no_write_and_write_report_are_restricted(tmp_path: Path) -> None:
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    producer_audit = build_producer_audit()
    snapshot = {
        "dashboard_status": "BLOCKED",
        "runtime_evidence_freshness_remediation_producers": producer_audit,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    for name in (
        "dashboard_snapshot_build_summary.json",
        "dashboard_global_status_snapshot.json",
    ):
        (reports / name).write_text(json.dumps(snapshot), encoding="utf-8")
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    no_write = run_cli(tmp_path)
    assert no_write.returncode == 0
    assert json.loads(no_write.stdout)["status"] == "warning"
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before

    write = run_cli(tmp_path, "--write-report")
    assert write.returncode == 0
    output = reports / "runtime_freshness_producer_contracts_audit_v1.json"
    assert output.is_file()
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    assert after - before == {
        Path("data/reports/runtime_freshness_producer_contracts_audit_v1.json")
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
    contracts = summary["runtime_freshness_producer_contracts"]
    if contracts["status"] != "ok":
        assert contracts["manual_closeout_allowed"] is False
    assert global_snapshot["runtime_freshness_producer_contracts"] == contracts
    assert infrastructure["runtime_freshness_producer_contracts"] == contracts
    assert active_controls["runtime_freshness_producer_contracts"] == contracts
    assert "runtime_freshness_producer_contracts" in infrastructure["sections"]
    assert "runtime_freshness_producer_contracts" in active_controls["sections"]


def test_component_reads_only_materialized_snapshot() -> None:
    payload = audit_contracts()
    snapshot = {"sections": {"runtime_freshness_producer_contracts": {"data": payload}}}

    assert runtime_freshness_producer_contracts_view(snapshot) == payload
    assert len(producer_contract_rows(payload)) == 3
    assert len(producer_contract_artifact_rows(payload)) == 3
    assert len(producer_contract_closeout_rows(payload)) == 3


def test_static_safety_has_no_forbidden_imports_or_calls() -> None:
    paths = (
        ROOT / "smartcrypto/ops/dashboard_snapshots/runtime_freshness_producer_contracts.py",
        ROOT / "smartcrypto/dashboard/components/runtime_freshness_producer_contracts.py",
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


def build_producer_audit() -> dict[str, Any]:
    return audit_runtime_evidence_freshness_remediation_producers(
        now_utc=NOW,
        dashboard_status="BLOCKED",
        global_source_health_status="BLOCKED",
        source_health_matrix=source_rows(),
        safety_payloads=[dict(SAFETY_FLAGS)],
    )


def audit_contracts(
    *,
    safety_payloads: list[dict[str, Any]] | None = None,
    contract_definitions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return audit_runtime_freshness_producer_contracts(
        now_utc=NOW,
        producer_audit=build_producer_audit(),
        safety_payloads=safety_payloads or [dict(SAFETY_FLAGS)],
        contract_definitions=contract_definitions,
    )


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
            "effective_timestamp_utc": "2026-06-15T18:30:00Z",
            "expected_schema_version": None,
            "severity": "CRITICAL",
            "blocks_dashboard_readiness": True,
        }
        for source_id, domain, path, threshold in definitions
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
