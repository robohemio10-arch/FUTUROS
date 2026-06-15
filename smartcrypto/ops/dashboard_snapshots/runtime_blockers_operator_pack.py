from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from smartcrypto.ops.dashboard_snapshots.builder_common import iso_utc, json_safe
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import (
    FORBIDDEN_ACTIONS,
    SAFETY_FLAGS,
)


SCHEMA_VERSION = "dashboard_runtime_blockers_operator_pack_v1"

SEQUENCE_DEFINITIONS = (
    {
        "sequence": 1,
        "sequence_id": "critical_source_health",
        "title": "Refresh critical or stale source-health evidence",
        "domains": ("market_data", "source_health"),
    },
    {
        "sequence": 2,
        "sequence_id": "runtime_safety_and_kill_switch",
        "title": "Refresh runtime safety and kill-switch evidence",
        "domains": ("active_controls", "portfolio_risk"),
    },
    {
        "sequence": 3,
        "sequence_id": "runtime_evidence_pack",
        "title": "Rebuild the runtime evidence pack externally",
        "blocker_ids": ("runtime_evidence_pack",),
    },
    {
        "sequence": 4,
        "sequence_id": "readiness_snapshot",
        "title": "Rebuild and review the readiness snapshot externally",
        "blocker_ids": ("readiness",),
    },
    {
        "sequence": 5,
        "sequence_id": "paper_shadow_soak_gap_accounting",
        "title": "Re-audit paper/shadow soak gap accounting externally",
        "blocker_ids": ("paper_shadow_soak_gap_accounting",),
    },
    {
        "sequence": 6,
        "sequence_id": "rebuild_dashboard_snapshots",
        "title": "Rebuild dashboard snapshots from materialized evidence",
        "command_hint": "python scripts/build_dashboard_snapshots.py --project-root . --output-dir data/reports --strict false --once --json",
    },
    {
        "sequence": 7,
        "sequence_id": "rerun_audits_without_release",
        "title": "Re-run audits and readiness checks without releasing live, canary, or orders",
        "command_hint": "Run the documented read-only auditors and inspect all remaining blockers.",
    },
)


def build_runtime_blockers_operator_pack(
    *,
    remediation: Mapping[str, Any],
    now_utc: datetime,
) -> dict[str, Any]:
    """Build a read-only operator pack from the remediation runbook payload."""
    blocker_rows = [
        dict(row)
        for row in remediation.get("blocker_rows", [])
        if isinstance(row, Mapping)
    ]
    operator_groups = _build_operator_groups(blocker_rows)
    operator_checklist = _build_operator_checklist(blocker_rows)
    critical_blockers = [
        row for row in blocker_rows if str(row.get("severity", "")).upper() == "CRITICAL"
    ]
    checklist_ids = {str(item["check_id"]) for item in operator_checklist}
    critical_without_checklist = [
        str(row.get("blocker_id", "UNKNOWN"))
        for row in critical_blockers
        if _check_id(row) not in checklist_ids
    ]
    checklist_without_closeout = [
        str(item["check_id"])
        for item in operator_checklist
        if not str(item.get("closeout_condition", "")).strip()
    ]
    safety_flags = _normalized_safety_flags(remediation.get("safety_flags"))
    unsafe_flags = _unsafe_safety_flags(safety_flags)
    runbook_blocked = str(remediation.get("status", "blocked")).lower() == "blocked"

    if runbook_blocked or critical_without_checklist or checklist_without_closeout or unsafe_flags:
        status = "blocked"
        reason = "operator_pack_incomplete_or_unsafe"
    elif blocker_rows:
        status = "warning"
        reason = "mapped_blockers_require_manual_external_closeout"
    else:
        status = "ok"
        reason = "no_critical_runtime_blockers"

    expected_evidence = _expected_post_remediation_evidence(operator_checklist)
    closeout_criteria = _closeout_criteria(operator_checklist)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "generated_at_utc": iso_utc(now_utc),
        "operator_pack_status": status,
        "dashboard_status": str(remediation.get("dashboard_status", "UNKNOWN")).upper(),
        "runtime_evidence_integration_status": str(
            remediation.get("runtime_evidence_integration_status", "UNKNOWN")
        ).upper(),
        "global_source_health_status": str(
            remediation.get("global_source_health_status", "UNKNOWN")
        ).upper(),
        "blockers_total": len(blocker_rows),
        "critical_blockers_total": len(critical_blockers),
        "domains_total": len({str(row.get("domain", "unknown")) for row in blocker_rows}),
        "operator_groups": operator_groups,
        "operator_checklist": operator_checklist,
        "external_execution_sequence": _external_execution_sequence(operator_checklist),
        "expected_post_remediation_evidence": expected_evidence,
        "closeout_criteria": closeout_criteria,
        "forbidden_actions": list(remediation.get("forbidden_actions", FORBIDDEN_ACTIONS)),
        "critical_blockers_without_checklist": critical_without_checklist,
        "checklist_items_without_closeout": checklist_without_closeout,
        "unsafe_safety_flags": unsafe_flags,
        "safety_flags": safety_flags,
    }
    return json_safe(payload)


def _build_operator_groups(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("domain", "unknown")),
            str(row.get("severity", "UNKNOWN")).upper(),
        )
        grouped[key].append(row)

    output: list[dict[str, Any]] = []
    for (domain, severity), group_rows in sorted(grouped.items()):
        blocker_ids = sorted({str(row.get("blocker_id", "UNKNOWN")) for row in group_rows})
        paths = sorted({str(row.get("canonical_path", "UNKNOWN")) for row in group_rows})
        actions = [str(row.get("remediation_action", "Manual review required.")) for row in group_rows]
        evidence = [_expected_artifact(row) for row in group_rows]
        closeout = [_blocker_closeout_condition(row) for row in group_rows]
        output.append(
            {
                "domain": domain,
                "severity": severity,
                "blockers_count": len(group_rows),
                "blocker_ids": blocker_ids,
                "canonical_paths": paths,
                "operator_summary": f"{len(group_rows)} {severity.lower()} blocker(s) require manual external review in {domain}.",
                "manual_actions": actions,
                "expected_evidence": evidence,
                "closeout_condition": " All conditions must hold: ".join(closeout),
                "execution_allowed": False,
                "safe_to_execute_from_dashboard": False,
            }
        )
    return output


def _build_operator_checklist(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=_row_order_key)
    return [
        {
            "step": index,
            "check_id": _check_id(row),
            "domain": str(row.get("domain", "unknown")),
            "severity": str(row.get("severity", "UNKNOWN")).upper(),
            "title": f"Close {row.get('blocker_id', 'UNKNOWN')}",
            "manual_action": str(row.get("remediation_action", "Manual review required.")),
            "producer_hint": str(row.get("producer_hint", "Use the documented producer.")),
            "expected_artifact": _expected_artifact(row),
            "verification_command": _verification_command(row),
            "closeout_condition": _blocker_closeout_condition(row),
            "execution_location": "manual_outside_dashboard",
            "requires_manual_operator": True,
            "execution_allowed": False,
            "safe_to_execute_from_dashboard": False,
            "changes_runtime": False,
            "changes_risk": False,
            "changes_model": False,
            "sends_orders": False,
            "sends_notifications": False,
        }
        for index, row in enumerate(ordered, start=1)
    ]


def _external_execution_sequence(checklist: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for definition in SEQUENCE_DEFINITIONS:
        matching = [
            str(item["check_id"])
            for item in checklist
            if _matches_sequence(item, definition)
        ]
        output.append(
            {
                "sequence": definition["sequence"],
                "sequence_id": definition["sequence_id"],
                "title": definition["title"],
                "check_ids": matching,
                "command_hint": definition.get(
                    "command_hint", "Follow the linked checklist items manually."
                ),
                "execution_location": "manual_outside_dashboard",
                "execution_allowed": False,
                "safe_to_execute_from_dashboard": False,
            }
        )
    return output


def _expected_post_remediation_evidence(
    checklist: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "check_id": item["check_id"],
            "domain": item["domain"],
            "expected_artifact": item["expected_artifact"],
            "expected_state": "Materialized, valid, current, and no longer represented by the blocker reason.",
        }
        for item in checklist
    ]


def _closeout_criteria(checklist: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    criteria = [
        {
            "criterion_id": str(item["check_id"]),
            "condition": item["closeout_condition"],
            "automatic_release": False,
        }
        for item in checklist
    ]
    criteria.extend(
        [
            {
                "criterion_id": "dashboard_rebuilt_from_materialized_evidence",
                "condition": "Dashboard snapshots were rebuilt externally and still preserve separate global, runtime-evidence, and combined blocker lists.",
                "automatic_release": False,
            },
            {
                "criterion_id": "readiness_duration_governance",
                "condition": "Seven days remain diagnostic and thirty continuous valid days remain the minimum readiness requirement without automatic live, canary, or order release.",
                "automatic_release": False,
            },
        ]
    )
    return criteria


def _row_order_key(row: Mapping[str, Any]) -> tuple[int, str, str]:
    blocker_id = str(row.get("blocker_id", ""))
    domain = str(row.get("domain", ""))
    if blocker_id in {"runtime_evidence_pack"}:
        priority = 3
    elif blocker_id == "readiness":
        priority = 4
    elif blocker_id == "paper_shadow_soak_gap_accounting":
        priority = 5
    elif domain in {"active_controls", "portfolio_risk"}:
        priority = 2
    else:
        priority = 1
    return priority, domain, blocker_id


def _matches_sequence(item: Mapping[str, Any], definition: Mapping[str, Any]) -> bool:
    blocker_id = str(item.get("check_id", "")).removeprefix("check_")
    if blocker_id in definition.get("blocker_ids", ()):
        return True
    domain = str(item.get("domain", ""))
    return domain in definition.get("domains", ())


def _check_id(row: Mapping[str, Any]) -> str:
    return f"check_{row.get('blocker_id', 'unknown')}"


def _expected_artifact(row: Mapping[str, Any]) -> str:
    return str(row.get("canonical_path", "UNKNOWN"))


def _verification_command(row: Mapping[str, Any]) -> str:
    path = _expected_artifact(row).replace('"', "")
    return f'Get-Content -Raw "{path}" | ConvertFrom-Json | Out-Null'


def _blocker_closeout_condition(row: Mapping[str, Any]) -> str:
    blocker_id = str(row.get("blocker_id", "UNKNOWN"))
    if blocker_id.startswith("src_"):
        return f"{blocker_id} is healthy/current and absent from global_blocking_reasons after snapshot rebuild."
    return f"{blocker_id} is absent from runtime_evidence_blocking_reasons after external evidence refresh and snapshot rebuild."


def _normalized_safety_flags(value: Any) -> dict[str, bool]:
    provided = dict(value) if isinstance(value, Mapping) else {}
    return {key: bool(provided.get(key, default)) for key, default in SAFETY_FLAGS.items()}


def _unsafe_safety_flags(flags: Mapping[str, bool]) -> list[str]:
    true_required = {"paper_only", "shadow_only", "dashboard_readonly"}
    unsafe: list[str] = []
    for key, value in flags.items():
        if key in true_required and value is not True:
            unsafe.append(key)
        elif key not in true_required and value is not False:
            unsafe.append(key)
    return sorted(unsafe)
