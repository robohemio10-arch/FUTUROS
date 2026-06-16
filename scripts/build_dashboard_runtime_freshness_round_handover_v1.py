from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict


SCHEMA_VERSION = "dashboard_runtime_freshness_round_handover_v1"
REPORT_PATH = Path("data/reports/dashboard_runtime_freshness_round_handover_v1.json")

ROUND_BRANCHES: tuple[str, ...] = (
    "dashboard-runtime-evidence-integration-v1",
    "dashboard-runtime-blockers-remediation-runbook-v1",
    "dashboard-runtime-blockers-operator-pack-v1",
    "runtime-blockers-closeout-evidence-audit-v1",
    "runtime-evidence-freshness-remediation-producers-audit-v1",
    "runtime-freshness-producer-contracts-manual-closeout-v1",
    "runtime-freshness-post-refresh-evidence-gate-v1",
    "runtime-freshness-producer-entrypoint-static-safety-audit-v1",
    "dashboard-runtime-freshness-governance-closeout-index-v1",
    "dashboard-runtime-freshness-round-handover-v1",
)

MODULE_KEYS: tuple[str, ...] = (
    "runtime_evidence_view",
    "runtime_blockers_remediation",
    "runtime_blockers_operator_pack",
    "runtime_blockers_closeout_evidence",
    "runtime_evidence_freshness_remediation_producers",
    "runtime_freshness_producer_contracts",
    "runtime_freshness_producer_entrypoint_static_safety",
    "runtime_freshness_post_refresh_evidence_gate",
    "runtime_freshness_governance_closeout_index",
)

RUNTIME_REPORTS: tuple[str, ...] = (
    "data/reports/dashboard_snapshot_build_summary.json",
    "data/reports/dashboard_global_status_snapshot.json",
    "data/reports/dashboard_runtime_blockers_remediation_runbook_v1.json",
    "data/reports/dashboard_runtime_blockers_operator_pack_v1.json",
    "data/reports/runtime_blockers_closeout_evidence_audit_v1.json",
    "data/reports/runtime_evidence_freshness_remediation_producers_audit_v1.json",
    "data/reports/runtime_freshness_producer_contracts_audit_v1.json",
    "data/reports/runtime_freshness_producer_entrypoint_static_safety_audit_v1.json",
    "data/reports/runtime_freshness_post_refresh_evidence_gate_v1.json",
    "data/reports/runtime_freshness_governance_closeout_index_v1.json",
)

UNSAFE_TRUE_FLAGS: frozenset[str] = frozenset(
    {
        "live_trading_enabled",
        "live_release_allowed",
        "canary_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "changes_model",
    }
)


class ModuleRow(TypedDict):
    module_key: str
    schema_version: str | None
    status: str
    reason: str
    execution_allowed: bool
    safe_to_execute_from_dashboard: bool
    sends_orders: bool
    changes_risk: bool
    changes_model: bool


class RuntimeReportRow(TypedDict):
    path: str
    exists: bool
    state: str
    schema_version: str | None
    status: str
    reason: str
    versioned: bool


def build_handover(project_root: Path, now_utc: datetime) -> dict[str, Any]:
    reports_root = project_root / "data/reports"
    summary = _load_json(reports_root / "dashboard_snapshot_build_summary.json")
    global_snapshot = _load_json(reports_root / "dashboard_global_status_snapshot.json")

    input_errors: list[str] = []
    if not summary:
        input_errors.append("missing_or_invalid:data/reports/dashboard_snapshot_build_summary.json")
    if not global_snapshot:
        input_errors.append("missing_or_invalid:data/reports/dashboard_global_status_snapshot.json")

    embedded_modules = {
        key: _embedded_payload(global_snapshot, summary, key) for key in MODULE_KEYS
    }
    module_rows = [_module_row(key, embedded_modules[key]) for key in MODULE_KEYS]
    runtime_report_rows = [_runtime_report_row(project_root, path) for path in RUNTIME_REPORTS]

    dashboard_status = str(
        global_snapshot.get("dashboard_status") or summary.get("dashboard_status") or "UNKNOWN"
    ).upper()
    global_source_health_status = str(
        global_snapshot.get("global_source_health_status")
        or summary.get("global_source_health_status")
        or "UNKNOWN"
    ).upper()

    global_blocking_reasons = _string_list(
        global_snapshot.get("global_blocking_reasons") or summary.get("global_blocking_reasons")
    )
    runtime_evidence_blocking_reasons = _string_list(
        global_snapshot.get("runtime_evidence_blocking_reasons")
        or summary.get("runtime_evidence_blocking_reasons")
        or embedded_modules.get("runtime_evidence_view", {}).get("blocking_evidence_sources")
    )
    combined_blocking_reasons = sorted(
        set(global_blocking_reasons)
        | set(runtime_evidence_blocking_reasons)
        | set(_string_list(global_snapshot.get("combined_blocking_reasons")))
        | set(_string_list(summary.get("combined_blocking_reasons")))
    )

    safety_flags = _safety_flags(summary, global_snapshot, embedded_modules)
    safety_violations = _safety_violations(safety_flags)

    if input_errors or safety_violations:
        status = "blocked"
        reason = "handover_input_or_safety_violation"
    elif dashboard_status == "BLOCKED" or combined_blocking_reasons:
        status = "blocked"
        reason = "handover_records_dashboard_blocked_state"
    elif any(row["status"] == "MISSING" for row in module_rows):
        status = "warning"
        reason = "handover_missing_embedded_round_modules"
    else:
        status = "ok"
        reason = "handover_round_closed_without_runtime_blockers"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "generated_at_utc": _iso_utc(now_utc),
        "project_name": "SMART FUTUROS",
        "dashboard_name": "SMART FUTUROS Command Center",
        "round_name": "dashboard_runtime_freshness_round",
        "round_branch_count": len(ROUND_BRANCHES),
        "round_branches": list(ROUND_BRANCHES),
        "dashboard_status": dashboard_status,
        "global_source_health_status": global_source_health_status,
        "global_blocking_reasons": global_blocking_reasons,
        "runtime_evidence_blocking_reasons": runtime_evidence_blocking_reasons,
        "combined_blocking_reasons": combined_blocking_reasons,
        "open_blockers_total": len(combined_blocking_reasons),
        "module_rows_total": len(module_rows),
        "module_rows": module_rows,
        "runtime_reports_total": len(runtime_report_rows),
        "runtime_report_rows": runtime_report_rows,
        "runtime_reports_missing": [
            row["path"] for row in runtime_report_rows if row["state"] == "MISSING"
        ],
        "closeout_state": {
            "dashboard_round_closed": True,
            "runtime_closeout_ready": False,
            "manual_external_refresh_required": bool(combined_blocking_reasons),
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
        },
        "manual_external_actions": [
            "Refresh market_data_health_audit_report.json manually outside Streamlit.",
            "Refresh kill_switch.json manually, preserving enabled=true.",
            "Validate runtime_safety_audit_config.json manually in paper environment.",
            "Rebuild dashboard snapshots from materialized evidence.",
            "Re-run governance/freshness auditors and inspect remaining blockers.",
            "Continue paper/shadow soak and gap accounting until institutional readiness criteria are met.",
        ],
        "acceptance_criteria": [
            "Dashboard remains read-only and snapshot-first.",
            "Runtime reports remain unversioned.",
            "Live/canary/orders/private exchange remain disabled.",
            "Dashboard may remain BLOCKED while blockers are materialized and auditable.",
            "Seven days remain diagnostic; thirty continuous valid days remain the minimum readiness threshold.",
        ],
        "forbidden_actions": [
            "Do not execute producers from the dashboard or this handover CLI.",
            "Do not version runtime artifacts under data/reports or data/runtime.",
            "Do not edit blockers, source-health rows, or snapshots to simulate closeout.",
            "Do not enable live trading, canary release, private exchange access, or order submission.",
            "Do not change risk, models, datasets, active signals, YAML configuration, or notifications.",
        ],
        "input_errors": input_errors,
        "safety_flags": safety_flags,
        "safety_violations": safety_violations,
        "operator_summary": (
            f"Dashboard/freshness round closed as read-only handover; "
            f"dashboard_status={dashboard_status}; "
            f"open_blockers={len(combined_blocking_reasons)}; "
            "no live/canary/order release authorized."
        ),
    }


def _module_row(key: str, payload: Mapping[str, Any]) -> ModuleRow:
    return {
        "module_key": key,
        "schema_version": str(payload["schema_version"]) if payload.get("schema_version") else None,
        "status": str(payload.get("status", "MISSING")).upper() if payload else "MISSING",
        "reason": str(payload.get("reason", "missing_embedded_payload")) if payload else "missing_embedded_payload",
        "execution_allowed": bool(payload.get("execution_allowed", False)) if payload else False,
        "safe_to_execute_from_dashboard": bool(payload.get("safe_to_execute_from_dashboard", False)) if payload else False,
        "sends_orders": bool(payload.get("sends_orders", False)) if payload else False,
        "changes_risk": bool(payload.get("changes_risk", False)) if payload else False,
        "changes_model": bool(payload.get("changes_model", False)) if payload else False,
    }


def _runtime_report_row(project_root: Path, relative_path: str) -> RuntimeReportRow:
    payload = _load_json(project_root / relative_path)
    return {
        "path": relative_path,
        "exists": bool(payload),
        "state": "OK" if payload else "MISSING",
        "schema_version": str(payload["schema_version"]) if payload.get("schema_version") else None,
        "status": str(payload.get("status", "UNKNOWN")).upper() if payload else "MISSING",
        "reason": str(payload.get("reason", "runtime_report_absent_or_invalid")) if payload else "runtime_report_absent_or_invalid",
        "versioned": False,
    }


def _safety_flags(
    summary: Mapping[str, Any],
    global_snapshot: Mapping[str, Any],
    modules: Mapping[str, Mapping[str, Any]],
) -> dict[str, bool]:
    flags: dict[str, bool] = {
        "paper_only": True,
        "shadow_only": True,
        "dashboard_readonly": True,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "changes_runtime": False,
    }
    for payload in [summary, global_snapshot, *modules.values()]:
        nested = payload.get("safety_flags") or payload.get("safety")
        if not isinstance(nested, Mapping):
            continue
        for key, value in nested.items():
            if not isinstance(value, bool):
                continue
            if key in UNSAFE_TRUE_FLAGS and value:
                flags[str(key)] = True
            elif key in {"paper_only", "shadow_only"} and not value:
                flags[str(key)] = False
            else:
                flags.setdefault(str(key), value)
    return flags


def _safety_violations(flags: Mapping[str, bool]) -> list[str]:
    violations = [key for key in sorted(UNSAFE_TRUE_FLAGS) if bool(flags.get(key))]
    if flags.get("paper_only") is not True:
        violations.append("paper_only_not_true")
    if flags.get("shadow_only") is not True:
        violations.append("shadow_only_not_true")
    return sorted(set(violations))


def _embedded_payload(
    global_snapshot: Mapping[str, Any],
    summary: Mapping[str, Any],
    key: str,
) -> dict[str, Any]:
    for source in (global_snapshot, summary):
        direct = source.get(key)
        if isinstance(direct, Mapping):
            return dict(direct)
        sections = source.get("sections")
        if isinstance(sections, Mapping):
            section = sections.get(key)
            if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
                return dict(section["data"])
    return {}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build read-only dashboard/freshness round handover.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    payload = build_handover(project_root, datetime.now(timezone.utc))

    if args.write_report:
        report_path = (project_root / REPORT_PATH).resolve()
        report_root = (project_root / "data/reports").resolve()
        if report_path.parent != report_root:
            raise ValueError(f"unauthorized_report_path:{report_path}")
        report_root.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{payload['status']}: {payload['reason']}")

    return 1 if payload["input_errors"] or payload["safety_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
