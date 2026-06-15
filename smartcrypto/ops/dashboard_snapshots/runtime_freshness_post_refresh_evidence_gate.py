from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.builder_common import iso_utc, json_safe
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import SAFETY_FLAGS
from smartcrypto.ops.dashboard_snapshots.runtime_freshness_producer_contracts import (
    CONTRACT_DEFINITIONS,
)


SCHEMA_VERSION = "runtime_freshness_post_refresh_evidence_gate_v1"
CONTRACTS_REPORT = Path("data/reports/runtime_freshness_producer_contracts_audit_v1.json")
PRODUCERS_REPORT = Path(
    "data/reports/runtime_evidence_freshness_remediation_producers_audit_v1.json"
)
CLOSEOUT_REPORT = Path("data/reports/runtime_blockers_closeout_evidence_audit_v1.json")

UNSAFE_FALSE_FLAGS = {
    "live_release_allowed",
    "canary_release_allowed",
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
}

FORBIDDEN_ACTIONS = [
    "Do not execute producers from the dashboard or this gate CLI.",
    "Do not edit snapshots or blocker lists to simulate evidence closeout.",
    "Do not disable the kill switch or enable live, canary, private exchange, or orders.",
    "Do not change risk, models, datasets, signals, YAML configuration, or notifications.",
    "Do not infer operational readiness from a passing freshness gate.",
]


def audit_runtime_freshness_post_refresh_evidence_gate(
    *,
    project_root: Path,
    now_utc: datetime,
    summary: Mapping[str, Any],
    global_snapshot: Mapping[str, Any],
    producer_contracts: Mapping[str, Any],
    producer_audit: Mapping[str, Any] | None = None,
    closeout_evidence: Mapping[str, Any] | None = None,
    input_errors: Sequence[str] = (),
) -> dict[str, Any]:
    current = _ensure_utc(now_utc)
    summary_view = dict(summary)
    global_view = dict(global_snapshot)
    source_rows = _source_rows(summary_view, global_view, closeout_evidence or {})
    source_by_id = {str(row.get("source_id", "")): row for row in source_rows}
    global_blocking = _string_list(
        global_view.get("global_blocking_reasons")
        or summary_view.get("global_blocking_reasons")
    )
    dashboard_status = str(
        global_view.get("dashboard_status")
        or summary_view.get("dashboard_status")
        or "UNKNOWN"
    ).upper()
    source_health_status = str(
        global_view.get("global_source_health_status")
        or summary_view.get("global_source_health_status")
        or "UNKNOWN"
    ).upper()
    safety_flags = _conservative_safety_flags(
        (summary_view, global_view, producer_contracts, producer_audit or {})
    )
    safety_violations = _unsafe_safety_flags(safety_flags)
    contracts = _mapping_rows(producer_contracts.get("producer_contracts"))
    rows = [
        _gate_row(
            project_root=project_root,
            now_utc=current,
            contract=contract,
            source_row=source_by_id.get(str(contract.get("target_source_id", "")), {}),
            global_blocking_reasons=global_blocking,
            safety_flags=safety_flags,
        )
        for contract in contracts
    ]
    missing_contracts = sorted(
        set(definition.contract_id for definition in CONTRACT_DEFINITIONS.values())
        - {str(row.get("contract_id", "")) for row in rows}
    )
    bypass_indicators = _bypass_indicators(
        rows=rows,
        global_blocking_reasons=global_blocking,
        source_health_status=source_health_status,
        dashboard_status=dashboard_status,
        safety_violations=safety_violations,
        manual_closeout_allowed=bool(
            producer_contracts.get("manual_closeout_allowed", False)
        ),
    )
    normalized_input_errors = sorted({str(error) for error in input_errors if error})
    if missing_contracts:
        normalized_input_errors.extend(
            f"missing_contract:{contract_id}" for contract_id in missing_contracts
        )

    blocked_rows = [row for row in rows if row["gate_state"] == "BLOCKED"]
    warning_rows = [row for row in rows if row["gate_state"] == "WARNING"]
    pass_rows = [row for row in rows if row["gate_state"] == "PASS"]
    gate_allowed = (
        len(pass_rows) == 3
        and not warning_rows
        and not blocked_rows
        and not bypass_indicators
        and not safety_violations
        and not normalized_input_errors
    )
    if blocked_rows or bypass_indicators or safety_violations or normalized_input_errors:
        status = "blocked"
        reason = "post_refresh_artifact_bypass_or_safety_violation"
    elif warning_rows:
        status = "warning"
        reason = "manual_refresh_still_required_and_auditable"
    else:
        status = "ok"
        reason = "post_refresh_evidence_accepted"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "generated_at_utc": iso_utc(current),
        "gate_allowed": gate_allowed,
        "gate_rows_total": len(rows),
        "gate_pass_total": len(pass_rows),
        "gate_warning_total": len(warning_rows),
        "gate_blocked_total": len(blocked_rows),
        "post_refresh_gate_rows": rows,
        "remaining_freshness_blockers": _remaining_target_blockers(rows, global_blocking),
        "remaining_global_blocking_reasons": global_blocking,
        "bypass_indicators": bypass_indicators,
        "stale_or_invalid_artifacts": [
            str(row["target_canonical_path"])
            for row in rows
            if not row["artifact_exists"]
            or not row["timestamp_valid"]
            or not row["freshness_passed"]
            or not row["schema_passed"]
        ],
        "manual_closeout_decision": {
            "allowed": gate_allowed,
            "reason": "all_post_refresh_gates_passed" if gate_allowed else reason,
        },
        "operator_summary": _operator_summary(status, rows, bypass_indicators),
        "input_errors": sorted(set(normalized_input_errors)),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "safety_flags": safety_flags,
    }
    return json_safe(payload)


def load_runtime_freshness_post_refresh_evidence_gate_inputs(
    project_root: Path,
) -> dict[str, Any]:
    reports = project_root / "data/reports"
    summary = _load_mapping(reports / "dashboard_snapshot_build_summary.json")
    global_snapshot = _load_mapping(reports / "dashboard_global_status_snapshot.json")
    contracts_report = _load_mapping(project_root / CONTRACTS_REPORT)
    producers_report = _load_mapping(project_root / PRODUCERS_REPORT)
    closeout_report = _load_mapping(project_root / CLOSEOUT_REPORT)
    contracts = _latest_payload(
        contracts_report,
        _embedded_payload(global_snapshot, summary, "runtime_freshness_producer_contracts"),
    )
    producers = _latest_payload(
        producers_report,
        _embedded_payload(
            global_snapshot,
            summary,
            "runtime_evidence_freshness_remediation_producers",
        ),
    )
    input_errors: list[str] = []
    if not summary:
        input_errors.append(
            "missing_or_invalid:data/reports/dashboard_snapshot_build_summary.json"
        )
    if not global_snapshot:
        input_errors.append(
            "missing_or_invalid:data/reports/dashboard_global_status_snapshot.json"
        )
    if not contracts:
        input_errors.append("missing_or_invalid:runtime_freshness_producer_contracts")
    return {
        "summary": summary,
        "global_snapshot": global_snapshot,
        "producer_contracts": contracts,
        "producer_audit": producers,
        "closeout_evidence": closeout_report,
        "input_errors": input_errors,
    }


def _gate_row(
    *,
    project_root: Path,
    now_utc: datetime,
    contract: Mapping[str, Any],
    source_row: Mapping[str, Any],
    global_blocking_reasons: Sequence[str],
    safety_flags: Mapping[str, bool],
) -> dict[str, Any]:
    target = str(contract.get("target_canonical_path", ""))
    target_source_id = str(contract.get("target_source_id", ""))
    expected_timestamp_field = str(contract.get("expected_timestamp_field", ""))
    artifact = _load_artifact(project_root / target)
    blocker_present = _blocker_present(target_source_id, global_blocking_reasons)
    artifact_timestamp_value = artifact.payload.get(expected_timestamp_field)
    timestamp_value = artifact_timestamp_value
    if artifact_timestamp_value in (None, "") and blocker_present:
        timestamp_value = source_row.get("effective_timestamp_utc")
    parsed_timestamp = _parse_utc(timestamp_value)
    explicit_invalid_timestamp = (
        artifact_timestamp_value not in (None, "") and parsed_timestamp is None
    )
    age_seconds = (
        max((now_utc - parsed_timestamp).total_seconds(), 0.0)
        if parsed_timestamp is not None
        else None
    )
    max_age = _number_or_none(contract.get("max_acceptable_age_seconds_after_refresh"))
    freshness_passed = (
        parsed_timestamp is not None
        and age_seconds is not None
        and max_age is not None
        and age_seconds <= max_age
    )
    health_passed = _health_passed(contract, artifact)
    schema_passed = artifact.exists and artifact.valid_json
    safety_passed = _row_safety_passed(contract, artifact, safety_flags)
    gate_state, gate_reason = _gate_state(
        artifact=artifact,
        timestamp_valid=parsed_timestamp is not None,
        freshness_passed=freshness_passed,
        health_passed=health_passed,
        schema_passed=schema_passed,
        safety_passed=safety_passed,
        explicit_invalid_timestamp=explicit_invalid_timestamp,
        blocker_present=blocker_present,
    )
    return {
        "gate_id": f"post_refresh_gate:{target_source_id}",
        "contract_id": contract.get("contract_id"),
        "producer_id": contract.get("producer_id"),
        "domain": contract.get("domain"),
        "target_source_id": target_source_id,
        "target_canonical_path": target,
        "artifact_exists": artifact.exists,
        "artifact_status": _artifact_status(contract, artifact),
        "expected_timestamp_field": expected_timestamp_field,
        "effective_timestamp_utc": iso_utc(parsed_timestamp) if parsed_timestamp else None,
        "timestamp_valid": parsed_timestamp is not None,
        "age_seconds": age_seconds,
        "max_acceptable_age_seconds_after_refresh": max_age,
        "freshness_passed": freshness_passed,
        "health_passed": health_passed,
        "schema_passed": schema_passed,
        "safety_passed": safety_passed,
        "blocker_absent_after_snapshot_rebuild": not blocker_present,
        "current_global_blocker_present": blocker_present,
        "gate_state": gate_state,
        "gate_reason": gate_reason,
        "manual_closeout_condition": contract.get("manual_closeout_condition"),
        "verification_commands": list(contract.get("verification_commands", []))
        if isinstance(contract.get("verification_commands"), list)
        else [],
        "requires_manual_operator": True,
        "execution_allowed": False,
        "safe_to_execute_from_dashboard": False,
        "changes_runtime": False,
        "changes_risk": False,
        "changes_model": False,
        "sends_orders": False,
        "sends_notifications": False,
    }


def _gate_state(
    *,
    artifact: "ArtifactRead",
    timestamp_valid: bool,
    freshness_passed: bool,
    health_passed: bool,
    schema_passed: bool,
    safety_passed: bool,
    explicit_invalid_timestamp: bool,
    blocker_present: bool,
) -> tuple[str, str]:
    if not artifact.exists:
        return "BLOCKED", "artifact_missing"
    if not schema_passed:
        return "BLOCKED", "artifact_invalid_json"
    if explicit_invalid_timestamp:
        return "BLOCKED", "artifact_timestamp_invalid"
    if not safety_passed:
        return "BLOCKED", "artifact_or_global_safety_failed"
    if not blocker_present and (not timestamp_valid or not freshness_passed or not health_passed):
        return "BLOCKED", "blocker_absent_but_artifact_not_acceptable"
    if timestamp_valid and freshness_passed and health_passed and not blocker_present:
        return "PASS", "post_refresh_evidence_valid_and_blocker_absent"
    return "WARNING", "manual_refresh_still_required_or_blocker_present"


class ArtifactRead:
    def __init__(self, *, exists: bool, valid_json: bool, payload: Mapping[str, Any]):
        self.exists = exists
        self.valid_json = valid_json
        self.payload = dict(payload)


def _load_artifact(path: Path) -> ArtifactRead:
    if not path.is_file():
        return ArtifactRead(exists=False, valid_json=False, payload={})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ArtifactRead(exists=True, valid_json=False, payload={})
    return ArtifactRead(
        exists=True,
        valid_json=isinstance(payload, Mapping),
        payload=payload if isinstance(payload, Mapping) else {},
    )


def _health_passed(contract: Mapping[str, Any], artifact: ArtifactRead) -> bool:
    if not artifact.exists or not artifact.valid_json:
        return False
    producer_id = str(contract.get("producer_id", ""))
    if producer_id == "kill_switch_state_refresh":
        return artifact.payload.get("enabled") is True
    status = str(artifact.payload.get("status", "ok")).lower()
    return status in {"ok", "healthy", "pass", "passed", "warning"}


def _row_safety_passed(
    contract: Mapping[str, Any], artifact: ArtifactRead, safety_flags: Mapping[str, bool]
) -> bool:
    if any(bool(safety_flags.get(key, False)) for key in UNSAFE_FALSE_FLAGS):
        return False
    producer_id = str(contract.get("producer_id", ""))
    if producer_id == "kill_switch_state_refresh":
        return artifact.payload.get("enabled") is True
    if producer_id != "runtime_safety_config_validation":
        return True
    true_flags = {"paper_only", "shadow_only"}
    false_flags = {
        "live_trading_enabled",
        "live_release_allowed",
        "canary_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
    }
    return all(artifact.payload.get(key) is True for key in true_flags) and all(
        artifact.payload.get(key, False) is False for key in false_flags
    )


def _artifact_status(contract: Mapping[str, Any], artifact: ArtifactRead) -> str:
    if not artifact.exists:
        return "MISSING"
    if not artifact.valid_json:
        return "INVALID_JSON"
    if str(contract.get("producer_id", "")) == "kill_switch_state_refresh":
        return "ENABLED" if artifact.payload.get("enabled") is True else "DISABLED"
    return str(artifact.payload.get("status", "OK")).upper()


def _bypass_indicators(
    *,
    rows: Sequence[Mapping[str, Any]],
    global_blocking_reasons: Sequence[str],
    source_health_status: str,
    dashboard_status: str,
    safety_violations: Sequence[str],
    manual_closeout_allowed: bool,
) -> list[str]:
    indicators: list[str] = []
    if not global_blocking_reasons and source_health_status == "BLOCKED":
        indicators.append("global_blockers_empty_while_source_health_blocked")
    for row in rows:
        source_id = str(row["target_source_id"])
        if row["freshness_passed"] and row["current_global_blocker_present"]:
            indicators.append(f"fresh_artifact_but_blocker_present:{source_id}")
        artifact_bad = (
            not row["artifact_exists"]
            or not row["timestamp_valid"]
            or not row["freshness_passed"]
            or not row["schema_passed"]
            or not row["safety_passed"]
        )
        if row["blocker_absent_after_snapshot_rebuild"] and artifact_bad:
            indicators.append(f"blocker_absent_with_invalid_artifact:{source_id}")
        if dashboard_status == "OK" and artifact_bad:
            indicators.append(f"dashboard_ok_with_invalid_critical_artifact:{source_id}")
        if row["producer_id"] == "kill_switch_state_refresh" and not row["safety_passed"]:
            indicators.append("kill_switch_enabled_false_or_unsafe")
    indicators.extend(f"unsafe_flag_true:{flag}" for flag in safety_violations)
    if manual_closeout_allowed and any(row["gate_state"] == "BLOCKED" for row in rows):
        indicators.append("manual_closeout_allowed_while_gate_blocked")
    return sorted(set(indicators))


def _remaining_target_blockers(
    rows: Sequence[Mapping[str, Any]], global_blocking_reasons: Sequence[str]
) -> list[str]:
    source_ids = {str(row["target_source_id"]) for row in rows}
    return sorted(
        reason
        for reason in global_blocking_reasons
        if reason.split(":", maxsplit=1)[0] in source_ids
    )


def _source_rows(*payloads: Mapping[str, Any]) -> list[dict[str, Any]]:
    for payload in payloads:
        value = payload.get("source_health_matrix")
        if isinstance(value, list):
            return [dict(row) for row in value if isinstance(row, Mapping)]
    return []


def _operator_summary(
    status: str, rows: Sequence[Mapping[str, Any]], bypass_indicators: Sequence[str]
) -> str:
    if status == "ok":
        return "All post-refresh evidence gates passed; this does not release live, canary, or orders."
    if bypass_indicators:
        return "Post-refresh evidence gate is blocked by bypass indicators or unsafe evidence."
    warnings = sum(1 for row in rows if row["gate_state"] == "WARNING")
    return f"{warnings} post-refresh gate row(s) still require manual refresh and snapshot rebuild."


def _blocker_present(source_id: str, global_blocking_reasons: Sequence[str]) -> bool:
    return any(reason.split(":", maxsplit=1)[0] == source_id for reason in global_blocking_reasons)


def _conservative_safety_flags(payloads: Sequence[Any]) -> dict[str, bool]:
    flags = dict(SAFETY_FLAGS)
    true_required = {"paper_only", "shadow_only", "dashboard_readonly"}
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        for candidate in (payload, payload.get("safety_flags"), payload.get("safety")):
            if not isinstance(candidate, Mapping):
                continue
            for key in flags:
                if key not in candidate:
                    continue
                observed = bool(candidate[key])
                flags[key] = (
                    flags[key] and observed
                    if key in true_required
                    else flags[key] or observed
                )
    return flags


def _unsafe_safety_flags(flags: Mapping[str, bool]) -> list[str]:
    true_required = {"paper_only", "shadow_only", "dashboard_readonly"}
    return sorted(
        key
        for key, value in flags.items()
        if (key in true_required and value is not True)
        or (key not in true_required and value is not False)
    )


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _embedded_payload(
    global_snapshot: Mapping[str, Any], summary: Mapping[str, Any], key: str
) -> dict[str, Any]:
    for payload in (global_snapshot, summary):
        direct = payload.get(key)
        if isinstance(direct, Mapping):
            return dict(direct)
        sections = payload.get("sections")
        if isinstance(sections, Mapping):
            section = sections.get(key)
            if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
                return dict(section["data"])
    return {}


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _latest_payload(*payloads: Mapping[str, Any]) -> dict[str, Any]:
    available = [dict(payload) for payload in payloads if payload]
    if not available:
        return {}
    return max(available, key=_payload_timestamp)


def _payload_timestamp(payload: Mapping[str, Any]) -> datetime:
    parsed = _parse_utc(payload.get("generated_at_utc"))
    return parsed or datetime.min.replace(tzinfo=timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
