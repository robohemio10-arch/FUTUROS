from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.builder_common import iso_utc, json_safe
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import SAFETY_FLAGS


SCHEMA_VERSION = "runtime_blockers_closeout_evidence_audit_v1"
BLOCKING_PAYLOAD_STATUSES = {"BLOCKED", "ERROR", "FAILED", "MISSING", "STALE"}
UNSAFE_TRUE_KEYS = {
    "live_trading_enabled",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "uses_ccxt",
    "uses_network",
    "uses_private_exchange",
}
TIMESTAMP_FIELDS = (
    "generated_at_utc",
    "last_updated_utc",
    "effective_timestamp_utc",
    "updated_at_utc",
    "updated_at",
    "as_of_utc",
    "timestamp_utc",
    "created_at_utc",
)


@dataclass(frozen=True)
class EvidenceDefinition:
    evidence_id: str
    domain: str
    source_id: str
    canonical_path: str
    previous_blocker_reference: str
    freshness_required: bool = False


CORE_EVIDENCE = (
    EvidenceDefinition(
        "dashboard_snapshot_build_summary",
        "dashboard",
        "dashboard_snapshot_build_summary",
        "data/reports/dashboard_snapshot_build_summary.json",
        "source_health:BLOCKED",
    ),
    EvidenceDefinition(
        "dashboard_global_status_snapshot",
        "dashboard",
        "dashboard_global_status_snapshot",
        "data/reports/dashboard_global_status_snapshot.json",
        "source_health:BLOCKED",
    ),
    EvidenceDefinition(
        "runtime_evidence_pack",
        "runtime_evidence",
        "runtime_evidence_pack_v2",
        "data/reports/runtime_evidence_pack_v2.json",
        "runtime_evidence_pack:BLOCKED",
    ),
    EvidenceDefinition(
        "readiness_snapshot",
        "readiness",
        "readiness_snapshot_v2",
        "data/reports/readiness_snapshot_v2.json",
        "readiness:BLOCKED",
    ),
    EvidenceDefinition(
        "paper_shadow_soak_gap_accounting",
        "paper_shadow_soak",
        "paper_shadow_soak_gap_accounting_report",
        "data/reports/paper_shadow_soak_gap_accounting_report.json",
        "paper_shadow_soak_gap_accounting:BLOCKED",
    ),
    EvidenceDefinition(
        "remediation_runbook",
        "governance",
        "dashboard_runtime_blockers_remediation_runbook_v1",
        "data/reports/dashboard_runtime_blockers_remediation_runbook_v1.json",
        "governance:remediation_runbook",
    ),
    EvidenceDefinition(
        "operator_pack",
        "governance",
        "dashboard_runtime_blockers_operator_pack_v1",
        "data/reports/dashboard_runtime_blockers_operator_pack_v1.json",
        "governance:operator_pack",
    ),
    EvidenceDefinition(
        "kill_switch",
        "portfolio_risk",
        "src_data_runtime_kill_switch_json",
        "data/runtime/kill_switch.json",
        "src_data_runtime_kill_switch_json:STALE",
        freshness_required=True,
    ),
    EvidenceDefinition(
        "runtime_safety_audit_config",
        "active_controls",
        "src_data_runtime_runtime_safety_audit_config_json",
        "data/runtime/runtime_safety_audit_config.json",
        "src_data_runtime_runtime_safety_audit_config_json:STALE",
        freshness_required=True,
    ),
)


def audit_runtime_blockers_closeout_evidence(
    *,
    project_root: Path,
    now_utc: datetime,
    summary: Mapping[str, Any],
    global_snapshot: Mapping[str, Any],
    remediation: Mapping[str, Any],
    operator_pack: Mapping[str, Any],
    source_health_matrix: Sequence[Any],
) -> dict[str, Any]:
    """Audit whether blocker closeout is supported by materialized evidence."""
    current = _ensure_utc(now_utc)
    global_reasons = _string_list(
        _preferred_value(global_snapshot, summary, "global_blocking_reasons")
    )
    runtime_reasons = _string_list(
        _preferred_value(global_snapshot, summary, "runtime_evidence_blocking_reasons")
    )
    combined_reasons = _string_list(
        _preferred_value(global_snapshot, summary, "combined_blocking_reasons")
    )
    dashboard_status = _status(
        global_snapshot.get("dashboard_status") or summary.get("dashboard_status")
    )
    source_health_status = _status(
        global_snapshot.get("global_source_health_status")
        or summary.get("global_source_health_status")
    )
    runtime_status = _status(
        global_snapshot.get("runtime_evidence_integration_status")
        or summary.get("runtime_evidence_integration_status")
    )
    materialized_safety_payload = _load_mapping(
        project_root / "data/runtime/runtime_safety_audit_config.json"
    )
    materialized_runtime_payloads = (
        _load_mapping(project_root / "data/reports/runtime_evidence_pack_v2.json"),
        _load_mapping(project_root / "data/reports/readiness_snapshot_v2.json"),
        _load_mapping(
            project_root / "data/reports/paper_shadow_soak_gap_accounting_report.json"
        ),
        materialized_safety_payload,
    )

    source_rows = [dict(row) for row in source_health_matrix if isinstance(row, Mapping)]
    evidence_rows = _build_evidence_rows(
        project_root=project_root,
        now_utc=current,
        summary=summary,
        global_snapshot=global_snapshot,
        remediation=remediation,
        operator_pack=operator_pack,
        source_rows=source_rows,
        current_global_reasons=global_reasons,
        current_runtime_reasons=runtime_reasons,
    )
    bypass_indicators = _bypass_indicators(
        dashboard_status=dashboard_status,
        source_health_status=source_health_status,
        runtime_status=runtime_status,
        global_reasons=global_reasons,
        runtime_reasons=runtime_reasons,
        combined_reasons=combined_reasons,
        source_rows=source_rows,
        evidence_rows=evidence_rows,
        payloads=(
            summary,
            global_snapshot,
            remediation,
            operator_pack,
            *materialized_runtime_payloads,
        ),
    )
    suspicious_closeouts = [
        {
            "evidence_id": row["evidence_id"],
            "previous_blocker_reference": row["previous_blocker_reference"],
            "reason": row["closeout_reason"],
        }
        for row in evidence_rows
        if row["closeout_state"] == "SUSPICIOUS"
    ]
    missing_sources = sorted(
        str(row["canonical_path"]) for row in evidence_rows if not row["exists"]
    )
    stale_sources = sorted(
        str(row["canonical_path"])
        for row in evidence_rows
        if str(row["freshness_status"]).upper() in {"STALE", "WARNING_STALE", "CRITICAL_STALE"}
    )
    invalid_timestamps = sorted(
        str(row["canonical_path"])
        for row in evidence_rows
        if not row["timestamp_valid"] and row["freshness_status"] != "NOT_APPLICABLE"
    )
    safety_flags = _normalized_safety_flags(
        summary,
        global_snapshot,
        remediation,
        operator_pack,
        materialized_safety_payload,
    )
    safety_violations = _unsafe_safety_flags(safety_flags)
    critical_blockers_present = bool(global_reasons or runtime_reasons)
    closeout_allowed = not (
        critical_blockers_present
        or bypass_indicators
        or suspicious_closeouts
        or invalid_timestamps
        or safety_violations
    )

    if bypass_indicators or suspicious_closeouts or invalid_timestamps or safety_violations:
        status = "blocked"
        reason = "closeout_evidence_bypass_or_safety_violation"
    elif critical_blockers_present:
        status = "warning"
        reason = "blockers_remain_materialized_and_auditable"
    else:
        status = "ok" if closeout_allowed else "blocked"
        reason = "closeout_supported_by_materialized_evidence" if closeout_allowed else "closeout_not_proven"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "generated_at_utc": iso_utc(current),
        "dashboard_status": dashboard_status,
        "global_source_health_status": source_health_status,
        "runtime_evidence_integration_status": runtime_status,
        "current_global_blocking_reasons": global_reasons,
        "current_runtime_evidence_blocking_reasons": runtime_reasons,
        "current_combined_blocking_reasons": combined_reasons,
        "closeout_evidence_rows": evidence_rows,
        "suspicious_closeouts": suspicious_closeouts,
        "missing_evidence_sources": missing_sources,
        "stale_evidence_sources": stale_sources,
        "invalid_timestamp_sources": invalid_timestamps,
        "bypass_indicators": bypass_indicators,
        "safety_violations": safety_violations,
        "closeout_allowed": closeout_allowed,
        "operator_summary": _operator_summary(status, len(global_reasons), len(runtime_reasons)),
        "safety_flags": safety_flags,
    }
    return json_safe(payload)


def load_closeout_audit_inputs(project_root: Path) -> dict[str, Any]:
    reports = project_root / "data/reports"
    summary = _load_mapping(reports / "dashboard_snapshot_build_summary.json")
    global_snapshot = _load_mapping(reports / "dashboard_global_status_snapshot.json")
    remediation = _embedded_or_file(
        summary,
        global_snapshot,
        "runtime_blockers_remediation",
        reports / "dashboard_runtime_blockers_remediation_runbook_v1.json",
    )
    operator_pack = _embedded_or_file(
        summary,
        global_snapshot,
        "runtime_blockers_operator_pack",
        reports / "dashboard_runtime_blockers_operator_pack_v1.json",
    )
    source_health_matrix = summary.get("source_health_matrix") or global_snapshot.get(
        "source_health_matrix"
    )
    return {
        "summary": summary,
        "global_snapshot": global_snapshot,
        "remediation": remediation,
        "operator_pack": operator_pack,
        "source_health_matrix": list(source_health_matrix)
        if isinstance(source_health_matrix, list)
        else [],
    }


def _build_evidence_rows(
    *,
    project_root: Path,
    now_utc: datetime,
    summary: Mapping[str, Any],
    global_snapshot: Mapping[str, Any],
    remediation: Mapping[str, Any],
    operator_pack: Mapping[str, Any],
    source_rows: Sequence[Mapping[str, Any]],
    current_global_reasons: Sequence[str],
    current_runtime_reasons: Sequence[str],
) -> list[dict[str, Any]]:
    definitions = list(CORE_EVIDENCE)
    known_paths = {definition.canonical_path for definition in definitions}
    for source_row in source_rows:
        if not bool(source_row.get("blocks_dashboard_readiness", False)):
            continue
        path = str(source_row.get("canonical_path", ""))
        if not path or path in known_paths:
            continue
        source_id = str(source_row.get("source_id", path))
        definitions.append(
            EvidenceDefinition(
                evidence_id=source_id,
                domain=str(source_row.get("owner_domain", "source_health")),
                source_id=source_id,
                canonical_path=path,
                previous_blocker_reference=f"{source_id}:{source_row.get('status', 'BLOCKED')}",
                freshness_required=bool(source_row.get("freshness_required", False)),
            )
        )

    source_by_path = {
        str(row.get("canonical_path", "")): row for row in source_rows if row.get("canonical_path")
    }
    payload_by_path: dict[str, Mapping[str, Any]] = {
        "data/reports/dashboard_snapshot_build_summary.json": summary,
        "data/reports/dashboard_global_status_snapshot.json": global_snapshot,
        "data/reports/dashboard_runtime_blockers_remediation_runbook_v1.json": remediation,
        "data/reports/dashboard_runtime_blockers_operator_pack_v1.json": operator_pack,
    }
    rows: list[dict[str, Any]] = []
    for definition in definitions:
        path = project_root / definition.canonical_path
        payload = payload_by_path.get(definition.canonical_path)
        if payload is None:
            payload = _load_mapping(path)
        source_row = source_by_path.get(definition.canonical_path, {})
        rows.append(
            _evidence_row(
                definition=definition,
                path=path,
                payload=payload,
                source_row=source_row,
                now_utc=now_utc,
                current_global_reasons=current_global_reasons,
                current_runtime_reasons=current_runtime_reasons,
            )
        )
    return rows


def _evidence_row(
    *,
    definition: EvidenceDefinition,
    path: Path,
    payload: Mapping[str, Any],
    source_row: Mapping[str, Any],
    now_utc: datetime,
    current_global_reasons: Sequence[str],
    current_runtime_reasons: Sequence[str],
) -> dict[str, Any]:
    exists = path.is_file() or bool(payload)
    freshness_required = bool(source_row.get("freshness_required", definition.freshness_required))
    effective_timestamp = source_row.get("effective_timestamp_utc") or _first_value(
        payload, TIMESTAMP_FIELDS
    )
    timestamp_valid, age_seconds = _timestamp_state(effective_timestamp, now_utc)
    freshness_status = str(
        source_row.get(
            "freshness_status",
            "UNKNOWN" if freshness_required else "NOT_APPLICABLE",
        )
    ).upper()
    if freshness_required and not timestamp_valid:
        freshness_status = "INVALID_TIMESTAMP"
    payload_status = _payload_status(payload)
    current_blocker_present = (
        definition.previous_blocker_reference in current_global_reasons
        or definition.previous_blocker_reference in current_runtime_reasons
        or any(reason.startswith(f"{definition.source_id}:") for reason in current_global_reasons)
    )
    evidence_valid = (
        exists
        and (timestamp_valid or not freshness_required)
        and payload_status not in BLOCKING_PAYLOAD_STATUSES
        and str(source_row.get("health_status", "HEALTHY")).upper() != "BLOCKED"
    )
    if current_blocker_present:
        closeout_state = "OPEN"
        closeout_reason = "blocker_remains_present_in_authoritative_reason_list"
    elif evidence_valid:
        closeout_state = "PROVEN"
        closeout_reason = "blocker_absent_and_materialized_evidence_is_valid"
    else:
        closeout_state = "SUSPICIOUS"
        closeout_reason = "blocker_absent_without_valid_materialized_evidence"

    return {
        "evidence_id": definition.evidence_id,
        "domain": definition.domain,
        "source_id": definition.source_id,
        "canonical_path": definition.canonical_path,
        "exists": exists,
        "status": str(source_row.get("status", "OK" if exists else "MISSING")),
        "health_status": str(source_row.get("health_status", "HEALTHY" if exists else "BLOCKED")),
        "freshness_status": freshness_status,
        "effective_timestamp_utc": effective_timestamp,
        "age_seconds": source_row.get("age_seconds", age_seconds),
        "timestamp_valid": timestamp_valid if freshness_required else True,
        "schema_version": payload.get("schema_version"),
        "payload_status": payload_status,
        "previous_blocker_reference": definition.previous_blocker_reference,
        "current_blocker_present": current_blocker_present,
        "closeout_state": closeout_state,
        "closeout_reason": closeout_reason,
        "expected_evidence": definition.canonical_path,
        "manual_verification_hint": f"Inspect {definition.canonical_path} and rebuild dashboard snapshots externally.",
        "safe_to_infer_closeout": closeout_state == "PROVEN",
        "requires_manual_operator": True,
        "execution_allowed": False,
        "changes_runtime": False,
        "changes_risk": False,
        "changes_model": False,
        "sends_orders": False,
        "sends_notifications": False,
    }


def _bypass_indicators(
    *,
    dashboard_status: str,
    source_health_status: str,
    runtime_status: str,
    global_reasons: Sequence[str],
    runtime_reasons: Sequence[str],
    combined_reasons: Sequence[str],
    source_rows: Sequence[Mapping[str, Any]],
    evidence_rows: Sequence[Mapping[str, Any]],
    payloads: Sequence[Mapping[str, Any]],
) -> list[str]:
    indicators: list[str] = []
    if source_health_status == "BLOCKED" and not global_reasons:
        indicators.append("global_blocking_reasons_empty_while_source_health_blocked")
    blocked_runtime_payload = any(
        row["evidence_id"] in {"runtime_evidence_pack", "readiness_snapshot", "paper_shadow_soak_gap_accounting"}
        and str(row["payload_status"]).upper() in BLOCKING_PAYLOAD_STATUSES
        for row in evidence_rows
    )
    if (runtime_status == "BLOCKED" or blocked_runtime_payload) and not runtime_reasons:
        indicators.append("runtime_evidence_blocking_reasons_empty_while_evidence_blocked")
    expected_combined = sorted(set(global_reasons) | set(runtime_reasons))
    if sorted(combined_reasons) != expected_combined:
        indicators.append("combined_blocking_reasons_not_equal_to_authoritative_union")
    required_unhealthy = any(
        str(row.get("required_level", "")).upper() == "REQUIRED"
        and str(row.get("status", "")).upper()
        in {"STALE", "MISSING_REQUIRED", "INVALID_TIMESTAMP", "INVALID_JSON", "INVALID_SCHEMA"}
        for row in source_rows
    )
    if dashboard_status == "OK" and required_unhealthy:
        indicators.append("dashboard_ok_with_required_source_unhealthy")
    for payload in payloads:
        indicators.extend(_unsafe_payload_indicators(payload))
    for row in evidence_rows:
        if row["freshness_status"] == "INVALID_TIMESTAMP":
            indicators.append(f"freshness_required_timestamp_invalid:{row['source_id']}")
    governance_claims_closeout = any(
        str(payload.get("status", "")).lower() == "ok"
        or payload.get("closeout_allowed") is True
        for payload in payloads[2:4]
    )
    if governance_claims_closeout and any(row["closeout_state"] != "PROVEN" for row in evidence_rows):
        indicators.append("runbook_or_operator_pack_claims_closeout_without_evidence")
    return sorted(set(indicators))


def _unsafe_payload_indicators(payload: Mapping[str, Any]) -> list[str]:
    indicators: list[str] = []
    for key, value in _walk_items(payload):
        if key in UNSAFE_TRUE_KEYS and value is True:
            indicators.append(f"unsafe_flag_true:{key}")
    return indicators


def _walk_items(value: Any) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            items.append((str(key), child))
            items.extend(_walk_items(child))
    elif isinstance(value, list | tuple):
        for child in value:
            items.extend(_walk_items(child))
    return items


def _normalized_safety_flags(*payloads: Mapping[str, Any]) -> dict[str, bool]:
    flags = dict(SAFETY_FLAGS)
    true_required = {"paper_only", "shadow_only", "dashboard_readonly"}
    for payload in payloads:
        for container_key in ("safety_flags", "safety"):
            container = payload.get(container_key)
            if isinstance(container, Mapping):
                for key in flags:
                    if key in container:
                        flags[key] = _conservative_flag_value(
                            key,
                            flags[key],
                            bool(container[key]),
                            true_required,
                        )
        for key in flags:
            if key in payload:
                flags[key] = _conservative_flag_value(
                    key,
                    flags[key],
                    bool(payload[key]),
                    true_required,
                )
    return flags


def _conservative_flag_value(
    key: str,
    current: bool,
    observed: bool,
    true_required: set[str],
) -> bool:
    return current and observed if key in true_required else current or observed


def _unsafe_safety_flags(flags: Mapping[str, bool]) -> list[str]:
    true_required = {"paper_only", "shadow_only", "dashboard_readonly"}
    return sorted(
        key
        for key, value in flags.items()
        if (key in true_required and value is not True)
        or (key not in true_required and value is not False)
    )


def _timestamp_state(value: Any, now_utc: datetime) -> tuple[bool, float | None]:
    if value in (None, ""):
        return False, None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False, None
    if parsed.tzinfo is None:
        return False, None
    parsed_utc = parsed.astimezone(timezone.utc)
    return True, max((now_utc - parsed_utc).total_seconds(), 0.0)


def _payload_status(payload: Mapping[str, Any]) -> str:
    raw = _first_value(
        payload,
        (
            "status",
            "runtime_evidence_status",
            "readiness_status",
            "thirty_day_readiness_status",
        ),
    )
    normalized = str(raw or "UNKNOWN").upper()
    if normalized in {"FAIL", "FAILED", "CRITICAL", "INSUFFICIENT", "NOT_REACHED"}:
        return "BLOCKED"
    if normalized in {"PASS", "PASSED", "READY", "VALID", "FRESH"}:
        return "OK"
    return normalized


def _first_value(payload: Any, keys: Sequence[str]) -> Any:
    if isinstance(payload, Mapping):
        for key in keys:
            if payload.get(key) is not None:
                return payload[key]
        for child in payload.values():
            found = _first_value(child, keys)
            if found is not None:
                return found
    elif isinstance(payload, list | tuple):
        for child in payload:
            found = _first_value(child, keys)
            if found is not None:
                return found
    return None


def _embedded_or_file(
    summary: Mapping[str, Any],
    global_snapshot: Mapping[str, Any],
    key: str,
    path: Path,
) -> dict[str, Any]:
    for payload in (global_snapshot, summary):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return _load_mapping(path)


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    return sorted({str(item) for item in value}) if isinstance(value, list | tuple) else []


def _preferred_value(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any],
    key: str,
) -> Any:
    return primary[key] if key in primary else fallback.get(key)


def _status(value: Any) -> str:
    return str(value or "UNKNOWN").upper()


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _operator_summary(status: str, global_count: int, runtime_count: int) -> str:
    if status == "blocked":
        return "Closeout is blocked because evidence, timestamp, blocker-list, or safety integrity is inconsistent."
    if status == "warning":
        return f"Closeout is not allowed: {global_count} source-health and {runtime_count} runtime-evidence blockers remain auditable."
    return "Closeout is supported by valid materialized evidence; no operational release is implied."
