from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.dashboard.components.runtime_blockers_operator_pack import (
    closeout_criteria_rows,
    external_sequence_rows,
    operator_checklist_rows,
    operator_group_rows,
    runtime_blockers_operator_pack_view,
)
from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.builder_registry import build_all_dashboard_snapshots
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_operator_pack import (
    build_runtime_blockers_operator_pack,
)
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import (
    SAFETY_FLAGS,
    build_runtime_blockers_remediation,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_dashboard_runtime_blockers_operator_pack_v1.py"
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
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


def remediation_payload() -> dict[str, object]:
    source_rows = [
        {
            "source_id": reason.split(":", 1)[0],
            "display_name": reason.split(":", 1)[0],
            "owner_domain": domain,
            "canonical_path": path,
            "status": "STALE",
            "freshness_status": "CRITICAL_STALE",
            "health_status": "BLOCKED",
            "required_level": "REQUIRED",
            "severity": "CRITICAL",
            "producer_hint": f"Documented producer for {path}",
            "runbook_hint": f"Consult the {domain} runbook.",
            "remediation_action": f"Refresh {path} manually.",
            "blocks_dashboard_readiness": True,
            "blocks_page_operational_view": True,
        }
        for reason, domain, path in zip(
            GLOBAL_REASONS,
            ("market_data", "portfolio_risk", "active_controls"),
            (
                "data/reports/market_data_health_audit_report.json",
                "data/runtime/kill_switch.json",
                "data/runtime/runtime_safety_audit_config.json",
            ),
            strict=True,
        )
    ]
    return build_runtime_blockers_remediation(
        now_utc=NOW,
        dashboard_status="BLOCKED",
        global_source_health_status="BLOCKED",
        runtime_evidence_integration_status="BLOCKED",
        global_blocking_reasons=GLOBAL_REASONS,
        runtime_evidence_blocking_reasons=RUNTIME_REASONS,
        source_health_matrix=source_rows,
    )


def operator_pack() -> dict[str, object]:
    return build_runtime_blockers_operator_pack(
        remediation=remediation_payload(),
        now_utc=NOW,
    )


def test_operator_pack_is_generated_from_existing_runbook_payload() -> None:
    pack = operator_pack()

    assert pack["schema_version"] == "dashboard_runtime_blockers_operator_pack_v1"
    assert pack["status"] == "warning"
    assert pack["operator_pack_status"] == "warning"
    assert pack["blockers_total"] == 7
    assert pack["critical_blockers_total"] == 7
    assert pack["domains_total"] == 7
    assert len(pack["operator_groups"]) == 7
    assert len(pack["operator_checklist"]) == 7
    assert [item["sequence"] for item in pack["external_execution_sequence"]] == list(
        range(1, 8)
    )


def test_every_critical_blocker_has_checklist_and_closeout_condition() -> None:
    remediation = remediation_payload()
    pack = build_runtime_blockers_operator_pack(remediation=remediation, now_utc=NOW)
    critical_ids = {
        f"check_{row['blocker_id']}"
        for row in remediation["blocker_rows"]
        if row["severity"] == "CRITICAL"
    }
    checklist = {item["check_id"]: item for item in pack["operator_checklist"]}

    assert critical_ids <= set(checklist)
    assert pack["critical_blockers_without_checklist"] == []
    assert pack["checklist_items_without_closeout"] == []
    for check_id in critical_ids:
        item = checklist[check_id]
        assert item["closeout_condition"]
        assert item["execution_location"] == "manual_outside_dashboard"
        assert item["requires_manual_operator"] is True
        assert item["execution_allowed"] is False
        assert item["safe_to_execute_from_dashboard"] is False
        assert item["changes_runtime"] is False
        assert item["changes_risk"] is False
        assert item["changes_model"] is False
        assert item["sends_orders"] is False
        assert item["sends_notifications"] is False


def test_operator_pack_fails_closed_when_runbook_or_safety_is_unsafe() -> None:
    remediation = remediation_payload()
    remediation["safety_flags"] = {**SAFETY_FLAGS, "uses_network": True}

    pack = build_runtime_blockers_operator_pack(remediation=remediation, now_utc=NOW)

    assert pack["status"] == "blocked"
    assert pack["unsafe_safety_flags"] == ["uses_network"]


def test_cli_no_write_and_write_report_are_restricted(tmp_path: Path) -> None:
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    (reports / "dashboard_snapshot_build_summary.json").write_text(
        json.dumps({"runtime_blockers_remediation": remediation_payload()}),
        encoding="utf-8",
    )
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    no_write = _run_cli(tmp_path)
    assert no_write.returncode == 0
    assert json.loads(no_write.stdout)["status"] == "warning"
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before

    write = _run_cli(tmp_path, "--write-report")
    assert write.returncode == 0
    output = reports / "dashboard_runtime_blockers_operator_pack_v1.json"
    assert output.is_file()
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    assert after - before == {
        Path("data/reports/dashboard_runtime_blockers_operator_pack_v1.json")
    }
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "warning"


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
    pack = summary["runtime_blockers_operator_pack"]
    assert global_snapshot["runtime_blockers_operator_pack"] == pack
    assert infrastructure["runtime_blockers_operator_pack"] == pack
    assert active_controls["runtime_blockers_operator_pack"] == pack
    assert "runtime_blockers_operator_pack" in infrastructure["sections"]
    assert "runtime_blockers_operator_pack" in active_controls["sections"]


def test_component_reads_only_materialized_operator_pack() -> None:
    pack = operator_pack()
    snapshot = {"sections": {"runtime_blockers_operator_pack": {"data": pack}}}

    assert runtime_blockers_operator_pack_view(snapshot) == pack
    assert len(operator_group_rows(pack)) == 7
    assert len(operator_checklist_rows(pack)) == 7
    assert len(external_sequence_rows(pack)) == 7
    assert len(closeout_criteria_rows(pack)) == 9


def test_safety_flags_preserve_all_required_invariants() -> None:
    flags = operator_pack()["safety_flags"]
    assert flags["paper_only"] is True
    assert flags["shadow_only"] is True
    assert flags["dashboard_readonly"] is True
    for key in (
        "live_trading_enabled",
        "live_release_allowed",
        "canary_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_runtime",
        "changes_risk",
        "changes_model",
        "changes_active_signals",
        "sends_notifications",
        "uses_ccxt",
        "uses_network",
        "uses_private_exchange",
    ):
        assert flags[key] is False


def test_static_safety_has_no_forbidden_imports_or_calls() -> None:
    paths = (
        ROOT / "smartcrypto/ops/dashboard_snapshots/runtime_blockers_operator_pack.py",
        ROOT / "smartcrypto/dashboard/components/runtime_blockers_operator_pack.py",
        ROOT / "scripts/build_dashboard_runtime_blockers_operator_pack_v1.py",
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
                assert _call_name(node.func) not in forbidden_calls


def _run_cli(project_root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
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


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return ""
