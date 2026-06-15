from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.dashboard.components.runtime_blockers_remediation import (
    runtime_blocker_rows,
    runtime_blockers_remediation_view,
)
from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.builder_registry import build_all_dashboard_snapshots
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import (
    SAFETY_FLAGS,
    build_runtime_blockers_remediation,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_dashboard_runtime_blockers_remediation_runbook_v1.py"
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
CURRENT_GLOBAL_REASONS = [
    "src_data_reports_market_data_health_audit_report_json:STALE",
    "src_data_runtime_kill_switch_json:STALE",
    "src_data_runtime_runtime_safety_audit_config_json:STALE",
]
CURRENT_RUNTIME_REASONS = [
    "source_health:BLOCKED",
    "runtime_evidence_pack:BLOCKED",
    "readiness:BLOCKED",
    "paper_shadow_soak_gap_accounting:BLOCKED",
]


def source_health_rows() -> list[dict[str, object]]:
    return [
        {
            "source_id": reason.split(":", 1)[0],
            "display_name": reason.split(":", 1)[0],
            "owner_domain": domain,
            "canonical_path": path,
            "status": "STALE",
            "freshness_status": "CRITICAL_STALE",
            "health_status": "BLOCKED",
            "age_seconds": 900000.0,
            "required_level": "REQUIRED",
            "severity": "CRITICAL",
            "producer_hint": f"Documented producer for {path}",
            "runbook_hint": f"Consult the {domain} runbook.",
            "remediation_action": f"Refresh {path} manually.",
            "blocks_dashboard_readiness": True,
            "blocks_page_operational_view": True,
        }
        for reason, domain, path in zip(
            CURRENT_GLOBAL_REASONS,
            ("market_data", "portfolio_risk", "active_controls"),
            (
                "data/reports/market_data_health_audit_report.json",
                "data/runtime/kill_switch.json",
                "data/runtime/runtime_safety_audit_config.json",
            ),
            strict=True,
        )
    ]


def current_payload() -> dict[str, object]:
    return build_runtime_blockers_remediation(
        now_utc=NOW,
        dashboard_status="BLOCKED",
        global_source_health_status="BLOCKED",
        runtime_evidence_integration_status="BLOCKED",
        global_blocking_reasons=CURRENT_GLOBAL_REASONS,
        runtime_evidence_blocking_reasons=CURRENT_RUNTIME_REASONS,
        source_health_matrix=source_health_rows(),
    )


def test_current_seven_blockers_are_fully_mapped_to_stable_rows() -> None:
    payload = current_payload()

    assert payload["status"] == "warning"
    assert payload["blockers_total"] == 7
    assert payload["combined_blocking_reasons"] == sorted(
        CURRENT_GLOBAL_REASONS + CURRENT_RUNTIME_REASONS
    )
    assert payload["unmapped_critical_blockers"] == []

    required_keys = {
        "blocker_id",
        "raw_reason",
        "domain",
        "severity",
        "source_id",
        "canonical_path",
        "status",
        "freshness_status",
        "health_status",
        "age_seconds",
        "required_level",
        "operator_summary",
        "remediation_action",
        "producer_hint",
        "runbook_hint",
        "blocks_dashboard_readiness",
        "blocks_page_operational_view",
        "safe_to_execute_from_dashboard",
        "execution_allowed",
        "requires_manual_operator",
        "changes_runtime",
        "changes_risk",
        "sends_orders",
        "sends_notifications",
    }
    for row in payload["blocker_rows"]:
        assert required_keys <= set(row)
        assert row["safe_to_execute_from_dashboard"] is False
        assert row["execution_allowed"] is False
        assert row["requires_manual_operator"] is True
        assert row["changes_runtime"] is False
        assert row["changes_risk"] is False
        assert row["sends_orders"] is False
        assert row["sends_notifications"] is False


def test_unknown_critical_blocker_fails_closed() -> None:
    payload = build_runtime_blockers_remediation(
        now_utc=NOW,
        dashboard_status="BLOCKED",
        global_source_health_status="BLOCKED",
        runtime_evidence_integration_status="BLOCKED",
        global_blocking_reasons=["unknown_critical_source:BLOCKED"],
        runtime_evidence_blocking_reasons=[],
    )

    assert payload["status"] == "blocked"
    assert payload["unmapped_critical_blockers"] == ["unknown_critical_source:BLOCKED"]


def test_safety_flags_keep_every_operational_capability_disabled() -> None:
    assert SAFETY_FLAGS["paper_only"] is True
    assert SAFETY_FLAGS["shadow_only"] is True
    assert SAFETY_FLAGS["dashboard_readonly"] is True
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
        "uses_private_exchange",
        "uses_network",
    ):
        assert SAFETY_FLAGS[key] is False


def test_builder_preserves_reason_domains_and_attaches_remediation() -> None:
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
    assert global_snapshot["combined_blocking_reasons"] == sorted(
        set(global_snapshot["global_blocking_reasons"])
        | set(global_snapshot["runtime_evidence_blocking_reasons"])
    )
    remediation = summary["runtime_blockers_remediation"]
    assert global_snapshot["runtime_blockers_remediation"] == remediation
    assert infrastructure["runtime_blockers_remediation"] == remediation
    assert active_controls["runtime_blockers_remediation"] == remediation
    assert "runtime_blockers_remediation" in infrastructure["sections"]
    assert "runtime_blockers_remediation" in active_controls["sections"]


def test_component_reads_snapshot_data_without_operational_state() -> None:
    payload = current_payload()
    snapshot = {"sections": {"runtime_blockers_remediation": {"data": payload}}}

    assert runtime_blockers_remediation_view(snapshot) == payload
    assert len(runtime_blocker_rows(payload)) == 7


def test_cli_no_write_and_explicit_write_are_path_restricted(tmp_path: Path) -> None:
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    summary = {
        "dashboard_status": "BLOCKED",
        "global_source_health_status": "BLOCKED",
        "runtime_evidence_integration_status": "BLOCKED",
        "global_blocking_reasons": CURRENT_GLOBAL_REASONS,
        "runtime_evidence_blocking_reasons": CURRENT_RUNTIME_REASONS,
        "source_health_matrix": source_health_rows(),
        "runtime_evidence_view": {"evidence_sources": []},
    }
    (reports / "dashboard_snapshot_build_summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}

    no_write = _run_cli(tmp_path)
    assert no_write.returncode == 0
    assert json.loads(no_write.stdout)["status"] == "warning"
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before

    write = _run_cli(tmp_path, "--write-report")
    assert write.returncode == 0
    output = reports / "dashboard_runtime_blockers_remediation_runbook_v1.json"
    assert output.is_file()
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    assert after - before == {
        Path("data/reports/dashboard_runtime_blockers_remediation_runbook_v1.json")
    }
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "warning"


def test_static_safety_scan_has_no_forbidden_imports_or_calls() -> None:
    paths = (
        ROOT / "smartcrypto/ops/dashboard_snapshots/runtime_blockers_remediation.py",
        ROOT / "smartcrypto/dashboard/components/runtime_blockers_remediation.py",
        ROOT / "scripts/audit_dashboard_runtime_blockers_remediation_runbook_v1.py",
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
                name = _call_name(node.func)
                assert name not in forbidden_calls


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
