from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from smartcrypto.ops.dashboard_snapshots.builder_common import iso_utc, json_safe


SCHEMA_VERSION = "dashboard_runtime_blockers_remediation_runbook_v1"

SAFETY_FLAGS = {
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
    "changes_runtime": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_active_signals": False,
    "sends_notifications": False,
    "uses_ccxt": False,
    "uses_private_exchange": False,
    "uses_network": False,
}

FORBIDDEN_ACTIONS = [
    "Do not execute evidence producers from the dashboard.",
    "Do not call private exchange APIs or submit, cancel, or amend orders.",
    "Do not change risk, models, active signals, datasets, or YAML configuration.",
    "Do not send Telegram, NTFY, or any other notification.",
    "Do not enable live trading or canary release from this runbook.",
]

RUNTIME_BLOCKER_DEFINITIONS: dict[str, dict[str, Any]] = {
    "source_health": {
        "domain": "source_health",
        "source_id": "dashboard_source_health_closeout",
        "canonical_path": "data/reports/dashboard_snapshot_build_summary.json",
        "operator_summary": "One or more required dashboard sources are unhealthy or stale.",
        "remediation_action": "Review each source-health blocker below and refresh it manually outside Streamlit.",
        "producer_hint": "Use only the documented producer for each blocked source.",
        "runbook_hint": "Resolve source rows individually, then rebuild dashboard snapshots.",
    },
    "runtime_evidence_pack": {
        "domain": "runtime_evidence",
        "source_id": "runtime_evidence_pack_v2",
        "canonical_path": "data/reports/runtime_evidence_pack_v2.json",
        "operator_summary": "The runtime evidence pack does not currently satisfy the dashboard contract.",
        "remediation_action": "Build the runtime evidence pack manually outside the dashboard and review its result.",
        "producer_hint": "scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py",
        "runbook_hint": "Rebuild runtime evidence first; do not infer readiness from the dashboard alone.",
    },
    "readiness": {
        "domain": "readiness",
        "source_id": "readiness_snapshot_v2",
        "canonical_path": "data/reports/readiness_snapshot_v2.json",
        "operator_summary": "Readiness remains blocked by the materialized evidence chain.",
        "remediation_action": "Refresh readiness evidence manually and inspect every blocking reason before rebuilding snapshots.",
        "producer_hint": "scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py",
        "runbook_hint": "Thirty continuous valid days are the minimum readiness requirement; seven days are diagnostic only.",
    },
    "paper_shadow_soak_gap_accounting": {
        "domain": "paper_shadow_soak",
        "source_id": "paper_shadow_soak_gap_accounting_report",
        "canonical_path": "data/reports/paper_shadow_soak_gap_accounting_report.json",
        "operator_summary": "Paper/shadow continuity or gap accounting does not meet readiness requirements.",
        "remediation_action": "Audit soak continuity manually outside the dashboard and preserve all detected gaps.",
        "producer_hint": "scripts/audit_paper_shadow_soak_continuity_and_gap_accounting.py",
        "runbook_hint": "Seven days provide diagnosis; thirty continuous valid days are required and never auto-release live.",
    },
}


def build_runtime_blockers_remediation(
    *,
    now_utc: datetime,
    dashboard_status: str,
    global_source_health_status: str,
    runtime_evidence_integration_status: str,
    global_blocking_reasons: Sequence[Any],
    runtime_evidence_blocking_reasons: Sequence[Any],
    source_health_matrix: Sequence[Any] = (),
    runtime_evidence_sources: Sequence[Any] = (),
) -> dict[str, Any]:
    """Translate materialized blocker reasons into a non-executable operator runbook."""
    global_reasons = _unique_reasons(global_blocking_reasons)
    runtime_reasons = _unique_reasons(runtime_evidence_blocking_reasons)
    combined_reasons = sorted(set(global_reasons) | set(runtime_reasons))
    source_rows = _rows_by_source_id(source_health_matrix)
    evidence_rows = _rows_by_source_id(runtime_evidence_sources)

    blocker_rows: list[dict[str, Any]] = []
    unmapped_critical: list[str] = []
    for raw_reason in combined_reasons:
        row = _build_blocker_row(raw_reason, source_rows, evidence_rows)
        if row is None:
            unmapped_critical.append(raw_reason)
            continue
        blocker_rows.append(row)

    blockers_by_domain = dict(sorted(Counter(row["domain"] for row in blocker_rows).items()))
    unsafe_flags = [
        key
        for key, value in SAFETY_FLAGS.items()
        if key not in {"paper_only", "shadow_only", "dashboard_readonly"} and value is not False
    ]
    if unmapped_critical or unsafe_flags:
        status = "blocked"
        reason = "critical_blocker_mapping_or_safety_violation"
    elif blocker_rows:
        status = "warning"
        reason = "expected_read_only_blockers_fully_mapped"
    else:
        status = "ok"
        reason = "no_current_blockers"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "generated_at_utc": iso_utc(now_utc),
        "dashboard_status": str(dashboard_status).upper(),
        "global_source_health_status": str(global_source_health_status).upper(),
        "runtime_evidence_integration_status": str(runtime_evidence_integration_status).upper(),
        "global_blocking_reasons": global_reasons,
        "runtime_evidence_blocking_reasons": runtime_reasons,
        "combined_blocking_reasons": combined_reasons,
        "blockers_total": len(blocker_rows),
        "blockers_by_domain": blockers_by_domain,
        "blocker_rows": blocker_rows,
        "operator_runbook_steps": _operator_runbook_steps(blocker_rows),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "unmapped_critical_blockers": unmapped_critical,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    return json_safe(payload)


def _build_blocker_row(
    raw_reason: str,
    source_rows: Mapping[str, Mapping[str, Any]],
    evidence_rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    blocker_id, status = _split_reason(raw_reason)
    source_row = source_rows.get(blocker_id)
    if source_row is not None:
        return _source_health_blocker(raw_reason, blocker_id, status, source_row)
    evidence_row = evidence_rows.get(blocker_id)
    if evidence_row is not None:
        return _evidence_source_blocker(raw_reason, blocker_id, status, evidence_row)

    definition = RUNTIME_BLOCKER_DEFINITIONS.get(blocker_id)
    if definition is None:
        return None
    evidence_row = evidence_rows.get(str(definition["source_id"]), {})
    return _stable_blocker_row(
        blocker_id=blocker_id,
        raw_reason=raw_reason,
        domain=str(definition["domain"]),
        severity="CRITICAL" if status in {"BLOCKED", "ERROR", "FAILED"} else "WARNING",
        source_id=str(definition["source_id"]),
        canonical_path=str(definition["canonical_path"]),
        status=status,
        freshness_status=str(evidence_row.get("freshness_status", "UNKNOWN")),
        health_status=str(evidence_row.get("health_status", status)),
        age_seconds=evidence_row.get("age_seconds"),
        required_level="REQUIRED",
        operator_summary=str(definition["operator_summary"]),
        remediation_action=str(definition["remediation_action"]),
        producer_hint=str(definition["producer_hint"]),
        runbook_hint=str(definition["runbook_hint"]),
        blocks_dashboard_readiness=True,
        blocks_page_operational_view=True,
    )


def _evidence_source_blocker(
    raw_reason: str,
    blocker_id: str,
    status: str,
    source_row: Mapping[str, Any],
) -> dict[str, Any]:
    canonical_path = str(source_row.get("path", "UNKNOWN"))
    return _stable_blocker_row(
        blocker_id=blocker_id,
        raw_reason=raw_reason,
        domain=str(source_row.get("domain", "runtime_evidence")),
        severity="CRITICAL" if bool(source_row.get("required", True)) else "WARNING",
        source_id=str(source_row.get("source_id", blocker_id)),
        canonical_path=canonical_path,
        status=str(source_row.get("status", status)),
        freshness_status=str(source_row.get("freshness_status", "UNKNOWN")),
        health_status=str(source_row.get("health_status", "BLOCKED")),
        age_seconds=source_row.get("age_seconds"),
        required_level="REQUIRED" if bool(source_row.get("required", True)) else "OPTIONAL",
        operator_summary=f"{blocker_id} is blocking the materialized runtime evidence chain.",
        remediation_action=str(
            source_row.get(
                "remediation_action",
                "Refresh the documented evidence source manually outside Streamlit.",
            )
        ),
        producer_hint=str(
            source_row.get("operator_hint", f"Use the documented producer for {canonical_path}.")
        ),
        runbook_hint="Review the evidence producer output, then rebuild dashboard snapshots.",
        blocks_dashboard_readiness=True,
        blocks_page_operational_view=True,
    )


def _source_health_blocker(
    raw_reason: str,
    blocker_id: str,
    status: str,
    source_row: Mapping[str, Any],
) -> dict[str, Any]:
    display_name = str(source_row.get("display_name", blocker_id))
    return _stable_blocker_row(
        blocker_id=blocker_id,
        raw_reason=raw_reason,
        domain=str(source_row.get("owner_domain", "source_health")),
        severity=str(source_row.get("severity", "CRITICAL")),
        source_id=str(source_row.get("source_id", blocker_id)),
        canonical_path=str(source_row.get("canonical_path", source_row.get("path", "UNKNOWN"))),
        status=str(source_row.get("status", status)),
        freshness_status=str(source_row.get("freshness_status", "UNKNOWN")),
        health_status=str(source_row.get("health_status", "BLOCKED")),
        age_seconds=source_row.get("age_seconds"),
        required_level=str(source_row.get("required_level", "REQUIRED")),
        operator_summary=f"{display_name} blocks the current operational view.",
        remediation_action=str(source_row.get("remediation_action", source_row.get("operator_hint", "Consult the source runbook."))),
        producer_hint=str(source_row.get("producer_hint", "Use the documented source producer.")),
        runbook_hint=str(source_row.get("runbook_hint", "Refresh manually outside Streamlit, then rebuild snapshots.")),
        blocks_dashboard_readiness=bool(source_row.get("blocks_dashboard_readiness", True)),
        blocks_page_operational_view=bool(source_row.get("blocks_page_operational_view", True)),
    )


def _stable_blocker_row(**values: Any) -> dict[str, Any]:
    return {
        **values,
        "safe_to_execute_from_dashboard": False,
        "execution_allowed": False,
        "requires_manual_operator": True,
        "changes_runtime": False,
        "changes_risk": False,
        "sends_orders": False,
        "sends_notifications": False,
    }


def _operator_runbook_steps(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    steps = [
        {
            "step": index,
            "blocker_id": row["blocker_id"],
            "action": row["remediation_action"],
            "producer_hint": row["producer_hint"],
            "execution_location": "manual_outside_dashboard",
        }
        for index, row in enumerate(rows, start=1)
    ]
    steps.append(
        {
            "step": len(steps) + 1,
            "blocker_id": "rebuild_and_verify",
            "action": "Rebuild dashboard snapshots and verify that blocker reasons changed only from new materialized evidence.",
            "producer_hint": "scripts/build_dashboard_snapshots.py",
            "execution_location": "manual_outside_dashboard",
        }
    )
    return steps


def _rows_by_source_id(rows: Sequence[Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["source_id"]): row
        for row in rows
        if isinstance(row, Mapping) and row.get("source_id")
    }


def _unique_reasons(reasons: Sequence[Any]) -> list[str]:
    return sorted({str(reason) for reason in reasons if str(reason).strip()})


def _split_reason(raw_reason: str) -> tuple[str, str]:
    blocker_id, separator, status = raw_reason.partition(":")
    return blocker_id, status.upper() if separator else "BLOCKED"
