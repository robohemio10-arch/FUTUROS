"""Fail-closed credential-rotation attestation validator."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import (
    ALLOWED_ROTATION_STATUSES,
    ALLOWED_VERIFICATION_METHODS,
    ATTESTATION_SCHEMA_VERSION,
    INVENTORY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    LoadedJsonInput,
    ValidationState,
)
from .loader import load_sanitized_json_input
from .report import display_path, resolve_report_path, validate_report_path, write_safe_report

INCIDENT_PREFIX = "SEC-"
DEFAULT_MAX_ATTESTATION_AGE_DAYS = 30
DEFAULT_FUTURE_TOLERANCE_MINUTES = 5


def validate_credential_rotation_attestation_v1(
    *,
    project_root: str | Path,
    required_inventory_path: str | Path | None = None,
    attestation_path: str | Path | None = None,
    max_attestation_age_days: int = DEFAULT_MAX_ATTESTATION_AGE_DAYS,
    future_tolerance_minutes: int = DEFAULT_FUTURE_TOLERANCE_MINUTES,
    max_file_bytes: int = 1_000_000,
    write_report: bool = False,
    report_path: str | Path | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    report_output = resolve_report_path(root, report_path)
    write_errors = validate_report_path(root, report_output, write_report)

    inventory_load = load_sanitized_json_input(
        project_root=root,
        raw_path=required_inventory_path,
        max_file_bytes=max(1, max_file_bytes),
    )
    attestation_load = load_sanitized_json_input(
        project_root=root,
        raw_path=attestation_path,
        max_file_bytes=max(1, max_file_bytes),
    )
    state = ValidationState()
    incident_reference: str | None = None

    if inventory_load.usable and attestation_load.usable:
        incident_reference = _validate_payloads(
            inventory=inventory_load.payload or {},
            attestation=attestation_load.payload or {},
            state=state,
            now=now,
            max_attestation_age_days=max(1, max_attestation_age_days),
            future_tolerance=timedelta(minutes=max(0, future_tolerance_minutes)),
        )

    secret_finding_count = sum(
        item.secret_finding_count + item.semantic_secret_field_count
        for item in (inventory_load, attestation_load)
    )
    status, reason, decision = _decide(
        inventory_load=inventory_load,
        attestation_load=attestation_load,
        state=state,
        secret_finding_count=secret_finding_count,
        write_errors=write_errors,
        incident_mismatch=_incident_mismatch(inventory_load, attestation_load),
    )
    inventory_payload = inventory_load.payload or {}
    attestation_payload = attestation_load.payload or {}
    required_count = len(_mapping_list(inventory_payload.get("required_credentials")))
    attested_count = len(_mapping_list(attestation_payload.get("attestations")))
    status_counts = Counter(
        str(item.get("rotation_status") or "")
        for item in _mapping_list(attestation_payload.get("attestations"))
    )
    complete = decision == "ROTATION_ATTESTATION_COMPLETE"
    safety = safety_flags(read_only=not write_report)
    blockers = _collect_blockers(
        inventory_load,
        attestation_load,
        state,
        secret_finding_count,
        write_errors,
        decision,
    )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now.isoformat(),
        "status": status,
        "reason": reason,
        "decision": decision,
        "incident_reference": incident_reference,
        "required_inventory_path": inventory_load.display_path,
        "attestation_path": attestation_load.display_path,
        "required_credential_count": required_count,
        "attested_credential_count": attested_count,
        "revoked_count": status_counts["revoked"],
        "rotated_count": status_counts["rotated"],
        "not_applicable_count": status_counts["not_applicable"],
        "unverified_count": state.unverified_count,
        "missing_credential_count": state.missing_credential_count,
        "unknown_credential_count": state.unknown_credential_count,
        "duplicate_credential_count": state.duplicate_credential_count,
        "dual_control_failure_count": state.dual_control_failure_count,
        "stale_attestation_count": state.stale_attestation_count,
        "timestamp_failure_count": len(state.timestamp_errors),
        "secret_finding_count": secret_finding_count,
        "blocking_secret_finding_count": secret_finding_count,
        "all_required_credentials_resolved": complete,
        "rotation_attestation_complete": complete,
        "max_attestation_age_days": max(1, max_attestation_age_days),
        "report_path": display_path(root, report_output),
        "write_report_requested": bool(write_report),
        "write_performed": False,
        "warnings": [
            "attestation_validates_sanitized_operational_declaration_not_provider_state"
        ],
        "blockers": blockers,
        "credential_results": state.credential_results,
        **safety,
        "safety_flags": safety,
    }

    if write_report and not write_errors:
        final_report = dict(report)
        final_report["write_performed"] = True
        try:
            write_safe_report(report_output, final_report)
        except OSError:
            final_report["status"] = "blocked"
            final_report["reason"] = "report_write_failed"
            final_report["decision"] = "BLOCKED_WRITE_OUTSIDE_ALLOWED_ROOT"
            final_report["write_performed"] = False
            final_report["rotation_attestation_complete"] = False
            final_report["all_required_credentials_resolved"] = False
            final_report["blockers"] = sorted(set(final_report["blockers"] + ["report_write_failed"]))
        report = final_report
    return report


def _validate_payloads(
    *,
    inventory: Mapping[str, Any],
    attestation: Mapping[str, Any],
    state: ValidationState,
    now: datetime,
    max_attestation_age_days: int,
    future_tolerance: timedelta,
) -> str | None:
    inventory_incident = _safe_text(inventory.get("incident_reference"))
    attestation_incident = _safe_text(attestation.get("incident_reference"))
    if inventory.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        state.inventory_errors.append("inventory_schema_version_invalid")
    if attestation.get("schema_version") != ATTESTATION_SCHEMA_VERSION:
        state.attestation_errors.append("attestation_schema_version_invalid")
    if not _valid_incident_reference(inventory_incident):
        state.inventory_errors.append("inventory_incident_reference_invalid")
    if not _valid_incident_reference(attestation_incident):
        state.attestation_errors.append("attestation_incident_reference_invalid")

    inventory_generated = _validate_timestamp(
        inventory.get("generated_at_utc"),
        field="inventory.generated_at_utc",
        state=state,
        now=now,
        future_tolerance=future_tolerance,
    )
    attestation_generated = _validate_timestamp(
        attestation.get("generated_at_utc"),
        field="attestation.generated_at_utc",
        state=state,
        now=now,
        future_tolerance=future_tolerance,
    )
    if attestation_generated and now - attestation_generated > timedelta(days=max_attestation_age_days):
        state.stale_attestation_count += 1

    required = _mapping_list(inventory.get("required_credentials"))
    attestations = _mapping_list(attestation.get("attestations"))
    if not isinstance(inventory.get("required_credentials"), list) or not required:
        state.inventory_errors.append("required_credentials_missing_or_empty")
    if not isinstance(attestation.get("attestations"), list):
        state.attestation_errors.append("attestations_missing_or_invalid")

    required_ids = [_safe_text(item.get("credential_id")) for item in required]
    required_categories = [_safe_text(item.get("credential_category")) for item in required]
    attested_ids = [_safe_text(item.get("credential_id")) for item in attestations]
    attested_categories = [_safe_text(item.get("credential_category")) for item in attestations]
    state.duplicate_credential_count = _duplicate_extra_count(required_ids)
    state.duplicate_credential_count += _duplicate_extra_count(required_categories)
    state.duplicate_credential_count += _duplicate_extra_count(attested_ids)
    state.duplicate_credential_count += _duplicate_extra_count(attested_categories)

    required_by_id: dict[str, Mapping[str, Any]] = {}
    for item in required:
        credential_id = _safe_text(item.get("credential_id"))
        category = _safe_text(item.get("credential_category"))
        provider = _safe_text(item.get("provider"))
        scope = _safe_text(item.get("affected_scope"))
        action = _safe_text(item.get("required_action"))
        if not all((credential_id, category, provider, scope)) or action != "revoke_or_rotate":
            state.inventory_errors.append("required_credential_record_invalid")
        if credential_id:
            required_by_id.setdefault(credential_id, item)

    attested_by_id: dict[str, Mapping[str, Any]] = {}
    for item in attestations:
        credential_id = _safe_text(item.get("credential_id"))
        if credential_id:
            attested_by_id.setdefault(credential_id, item)

    missing_ids = sorted(set(required_by_id) - set(attested_by_id))
    unknown_ids = sorted(set(attested_by_id) - set(required_by_id))
    state.missing_credential_count = len(missing_ids)
    state.unknown_credential_count = len(unknown_ids)

    for credential_id in sorted(required_by_id):
        required_item = required_by_id[credential_id]
        attested_item = attested_by_id.get(credential_id)
        state.credential_results.append(
            _validate_credential_result(
                required_item=required_item,
                attested_item=attested_item,
                state=state,
                now=now,
                future_tolerance=future_tolerance,
            )
        )
    for credential_id in unknown_ids:
        state.credential_results.append(_unknown_credential_result(attested_by_id[credential_id]))

    del inventory_generated
    return inventory_incident if inventory_incident == attestation_incident else None


def _validate_credential_result(
    *,
    required_item: Mapping[str, Any],
    attested_item: Mapping[str, Any] | None,
    state: ValidationState,
    now: datetime,
    future_tolerance: timedelta,
) -> dict[str, Any]:
    base = {
        "credential_id": _safe_text(required_item.get("credential_id")),
        "credential_category": _safe_text(required_item.get("credential_category")),
        "provider": _safe_text(required_item.get("provider")),
        "required_action": _safe_text(required_item.get("required_action")),
        "rotation_status": None,
        "completed_at_utc": None,
        "verified_at_utc": None,
        "operator_role": None,
        "reviewer_role": None,
        "verification_method": None,
        "sanitized_evidence_reference": None,
        "status": "blocked",
        "reason": "required_credential_missing",
    }
    if attested_item is None:
        return base

    status = _safe_text(attested_item.get("rotation_status"))
    completed_text = _safe_text(attested_item.get("completed_at_utc"))
    verified_text = _safe_text(attested_item.get("verified_at_utc"))
    operator = _safe_text(attested_item.get("operator_role"))
    reviewer = _safe_text(attested_item.get("reviewer_role"))
    method = _safe_text(attested_item.get("verification_method"))
    evidence_reference = _safe_text(attested_item.get("sanitized_evidence_reference"))
    notes = _safe_text(attested_item.get("sanitized_notes"))
    base.update(
        {
            "rotation_status": status,
            "completed_at_utc": completed_text,
            "verified_at_utc": verified_text,
            "operator_role": operator,
            "reviewer_role": reviewer,
            "verification_method": method,
            "sanitized_evidence_reference": evidence_reference,
        }
    )
    failures: list[str] = []
    if _safe_text(attested_item.get("credential_category")) != base["credential_category"]:
        failures.append("credential_category_mismatch")
    if _safe_text(attested_item.get("provider")) != base["provider"]:
        failures.append("provider_mismatch")
    if status not in ALLOWED_ROTATION_STATUSES:
        state.attestation_errors.append("rotation_status_invalid")
        failures.append("rotation_status_invalid")
    if method not in ALLOWED_VERIFICATION_METHODS:
        state.attestation_errors.append("verification_method_invalid")
        failures.append("verification_method_invalid")

    if status == "unverified":
        state.unverified_count += 1
        failures.append("credential_unverified")
    elif status in {"revoked", "rotated"}:
        completed = _validate_timestamp(
            completed_text,
            field=f"credential.{base['credential_id']}.completed_at_utc",
            state=state,
            now=now,
            future_tolerance=future_tolerance,
        )
        verified = _validate_timestamp(
            verified_text,
            field=f"credential.{base['credential_id']}.verified_at_utc",
            state=state,
            now=now,
            future_tolerance=future_tolerance,
        )
        dual_failures = _dual_control_failures(operator, reviewer, evidence_reference)
        if method == "documented_not_applicable":
            dual_failures.append("verification_method_incompatible_with_rotation")
        if completed and verified and verified < completed:
            state.timestamp_errors.append(f"credential.{base['credential_id']}.verification_before_completion")
            failures.append("verification_before_completion")
        if dual_failures:
            state.dual_control_failure_count += 1
            failures.extend(dual_failures)
    elif status == "not_applicable":
        not_applicable_failures: list[str] = []
        if not reviewer:
            not_applicable_failures.append("reviewer_role_missing")
        if method != "documented_not_applicable":
            not_applicable_failures.append("documented_not_applicable_method_required")
        if not evidence_reference:
            not_applicable_failures.append("sanitized_evidence_reference_missing")
        if not notes:
            not_applicable_failures.append("sanitized_justification_missing")
        if not_applicable_failures:
            state.dual_control_failure_count += 1
            failures.extend(not_applicable_failures)

    if failures:
        base["reason"] = sorted(set(failures))[0]
    else:
        base["status"] = "ok"
        base["reason"] = "credential_attestation_resolved"
    return base


def _unknown_credential_result(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "credential_id": _safe_text(item.get("credential_id")),
        "credential_category": _safe_text(item.get("credential_category")),
        "provider": _safe_text(item.get("provider")),
        "required_action": None,
        "rotation_status": _safe_text(item.get("rotation_status")),
        "completed_at_utc": _safe_text(item.get("completed_at_utc")),
        "verified_at_utc": _safe_text(item.get("verified_at_utc")),
        "operator_role": _safe_text(item.get("operator_role")),
        "reviewer_role": _safe_text(item.get("reviewer_role")),
        "verification_method": _safe_text(item.get("verification_method")),
        "sanitized_evidence_reference": _safe_text(item.get("sanitized_evidence_reference")),
        "status": "blocked",
        "reason": "unknown_credential",
    }


def _decide(
    *,
    inventory_load: LoadedJsonInput,
    attestation_load: LoadedJsonInput,
    state: ValidationState,
    secret_finding_count: int,
    write_errors: Sequence[str],
    incident_mismatch: bool,
) -> tuple[str, str, str]:
    if write_errors:
        return "blocked", "write_output_outside_allowed_root", "BLOCKED_WRITE_OUTSIDE_ALLOWED_ROOT"
    if inventory_load.reason == "input_not_found" or attestation_load.reason == "input_not_found":
        return "blocked", "required_inputs_not_found", "BLOCKED_INPUT_NOT_FOUND"
    if inventory_load.reason == "unsafe_input_path" or attestation_load.reason == "unsafe_input_path":
        return "blocked", "unsafe_input_path", "BLOCKED_UNSAFE_INPUT_PATH"
    if secret_finding_count:
        return "blocked", "secret_material_detected", "BLOCKED_SECRET_MATERIAL_DETECTED"
    if inventory_load.reason in {"invalid_json"} or state.inventory_errors:
        return "blocked", "required_inventory_invalid", "BLOCKED_REQUIRED_INVENTORY_INVALID"
    if attestation_load.reason in {"invalid_json"} or state.attestation_errors:
        return "blocked", "attestation_invalid", "BLOCKED_ATTESTATION_INVALID"
    if state.timestamp_errors:
        return "blocked", "timestamp_invalid", "BLOCKED_TIMESTAMP_INVALID"
    if incident_mismatch:
        return "blocked", "incident_reference_mismatch", "BLOCKED_INCIDENT_REFERENCE_MISMATCH"
    if state.duplicate_credential_count:
        return "blocked", "duplicate_credential", "BLOCKED_DUPLICATE_CREDENTIAL"
    if state.missing_credential_count:
        return "blocked", "required_credential_missing", "BLOCKED_REQUIRED_CREDENTIAL_MISSING"
    if state.unknown_credential_count:
        return "blocked", "unknown_credential", "BLOCKED_UNKNOWN_CREDENTIAL"
    if state.unverified_count:
        return "blocked", "unverified_credential", "BLOCKED_UNVERIFIED_CREDENTIAL"
    if state.dual_control_failure_count:
        return "blocked", "dual_control_invalid", "BLOCKED_DUAL_CONTROL_INVALID"
    if state.stale_attestation_count:
        return "blocked", "attestation_stale", "BLOCKED_STALE_ATTESTATION"
    return "ok", "sanitized_rotation_attestation_complete", "ROTATION_ATTESTATION_COMPLETE"


def _validate_timestamp(
    value: Any,
    *,
    field: str,
    state: ValidationState,
    now: datetime,
    future_tolerance: timedelta,
) -> datetime | None:
    text = _safe_text(value)
    if not text:
        state.timestamp_errors.append(f"{field}:missing")
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        state.timestamp_errors.append(f"{field}:invalid")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        state.timestamp_errors.append(f"{field}:not_utc")
        return None
    parsed = parsed.astimezone(UTC)
    if parsed > now + future_tolerance:
        state.timestamp_errors.append(f"{field}:future")
    return parsed


def _dual_control_failures(operator: str | None, reviewer: str | None, evidence: str | None) -> list[str]:
    failures: list[str] = []
    if not operator:
        failures.append("operator_role_missing")
    if not reviewer:
        failures.append("reviewer_role_missing")
    if operator and reviewer and operator.casefold() == reviewer.casefold():
        failures.append("operator_and_reviewer_must_differ")
    if not evidence:
        failures.append("sanitized_evidence_reference_missing")
    return failures


def _incident_mismatch(inventory: LoadedJsonInput, attestation: LoadedJsonInput) -> bool:
    if not inventory.usable or not attestation.usable:
        return False
    return _safe_text(inventory.payload.get("incident_reference")) != _safe_text(
        attestation.payload.get("incident_reference")
    )


def _valid_incident_reference(value: str | None) -> bool:
    if not value or not value.startswith(INCIDENT_PREFIX):
        return False
    parts = value.split("-")
    return len(parts) == 3 and len(parts[1]) == 4 and parts[1].isdigit() and len(parts[2]) >= 3 and parts[2].isdigit()


def _duplicate_extra_count(values: Sequence[str | None]) -> int:
    filtered = [value for value in values if value]
    return len(filtered) - len(set(filtered))


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _safe_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _collect_blockers(
    inventory_load: LoadedJsonInput,
    attestation_load: LoadedJsonInput,
    state: ValidationState,
    secret_finding_count: int,
    write_errors: Sequence[str],
    decision: str,
) -> list[str]:
    if decision == "ROTATION_ATTESTATION_COMPLETE":
        return []
    blockers = list(inventory_load.blockers) + list(attestation_load.blockers) + list(write_errors)
    blockers.extend(state.inventory_errors)
    blockers.extend(state.attestation_errors)
    blockers.extend(state.timestamp_errors)
    if secret_finding_count:
        blockers.append("secret_material_detected")
    if state.stale_attestation_count:
        blockers.append("attestation_stale")
    if state.duplicate_credential_count:
        blockers.append("duplicate_credentials")
    if state.missing_credential_count:
        blockers.append("required_credentials_missing")
    if state.unknown_credential_count:
        blockers.append("unknown_credentials")
    if state.unverified_count:
        blockers.append("unverified_credentials")
    if state.dual_control_failure_count:
        blockers.append("dual_control_failures")
    return sorted(set(blockers))


def safety_flags(*, read_only: bool) -> dict[str, bool]:
    return {
        "paper_only": True,
        "security_only": True,
        "read_only": read_only,
        "live_trading_enabled": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "runs_training": False,
        "writes_runtime": False,
        "writes_feedback": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "writes_models": False,
        "writes_registries": False,
        "rotates_credentials": False,
        "revokes_credentials": False,
        "calls_provider_apis": False,
        "reads_environment_secrets": False,
    }
