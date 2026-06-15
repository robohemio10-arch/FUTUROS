from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.builder_common import iso_utc, json_safe
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import SAFETY_FLAGS
from smartcrypto.ops.dashboard_snapshots.runtime_evidence_freshness_remediation_producers import (
    PRODUCER_DEFINITIONS,
)


SCHEMA_VERSION = "runtime_freshness_producer_contracts_audit_v1"
PRODUCER_AUDIT_REPORT = Path(
    "data/reports/runtime_evidence_freshness_remediation_producers_audit_v1.json"
)

FORBIDDEN_ACTIONS = [
    "Do not execute producers from the dashboard or this contract audit CLI.",
    "Do not disable the kill switch to refresh its timestamp.",
    "Do not edit source-health or blocker lists to simulate closeout.",
    "Do not enable live, canary, private exchange access, or order submission.",
    "Do not change risk, models, datasets, signals, YAML configuration, or notifications.",
]


@dataclass(frozen=True)
class ContractDefinition:
    contract_id: str
    producer_id: str
    domain: str
    target_source_id: str
    target_canonical_path: str
    expected_timestamp_field: str
    entry_criteria: tuple[str, ...]
    post_refresh_success_criteria: tuple[str, ...]
    manual_closeout_condition: str
    rollback_or_abort_condition: str
    operator_notes: tuple[str, ...]


CONTRACT_DEFINITIONS: dict[str, ContractDefinition] = {
    "market_data_health_audit": ContractDefinition(
        contract_id="market_data_health_manual_refresh_v1",
        producer_id="market_data_health_audit",
        domain="market_data",
        target_source_id="src_data_reports_market_data_health_audit_report_json",
        target_canonical_path="data/reports/market_data_health_audit_report.json",
        expected_timestamp_field="generated_at_utc",
        entry_criteria=(
            "The source is STALE or CRITICAL_STALE in the materialized source-health matrix.",
            "The operator has reviewed all documented market-health input artifacts.",
            "Execution occurs manually outside Streamlit and outside this audit CLI.",
        ),
        post_refresh_success_criteria=(
            "The expected JSON artifact exists and parses successfully.",
            "status is OK and generated_at_utc is valid and current.",
            "A dashboard snapshot rebuild removes the source ID from global_blocking_reasons.",
        ),
        manual_closeout_condition=(
            "Close manually only after the refreshed artifact is healthy/current and the source "
            "ID is absent from global_blocking_reasons after an external snapshot rebuild."
        ),
        rollback_or_abort_condition=(
            "Abort closeout if inputs are missing, status is not OK, the timestamp is invalid, "
            "or the source blocker remains after snapshot rebuild."
        ),
        operator_notes=(
            "Use only the documented project producer.",
            "A fresh timestamp alone does not establish readiness.",
        ),
    ),
    "kill_switch_state_refresh": ContractDefinition(
        contract_id="kill_switch_runtime_manual_refresh_v1",
        producer_id="kill_switch_state_refresh",
        domain="portfolio_risk",
        target_source_id="src_data_runtime_kill_switch_json",
        target_canonical_path="data/runtime/kill_switch.json",
        expected_timestamp_field="updated_at",
        entry_criteria=(
            "The kill-switch source is STALE or CRITICAL_STALE.",
            "The operator confirms the kill switch must remain enabled during refresh.",
            "Execution occurs manually outside Streamlit and outside this audit CLI.",
        ),
        post_refresh_success_criteria=(
            "The expected JSON artifact exists and parses successfully.",
            "enabled remains true and updated_at is valid and current.",
            "A dashboard snapshot rebuild removes the source ID from global_blocking_reasons.",
        ),
        manual_closeout_condition=(
            "Close manually only after enabled remains true, the timestamp is current, and the "
            "source ID is absent from global_blocking_reasons after snapshot rebuild."
        ),
        rollback_or_abort_condition=(
            "Abort immediately if enabled is false, safety state is ambiguous, the timestamp is "
            "invalid, or any live/order flag becomes enabled."
        ),
        operator_notes=(
            "Refreshing evidence must never weaken the kill switch.",
            "Do not infer operational release from source-health closeout.",
        ),
    ),
    "runtime_safety_config_validation": ContractDefinition(
        contract_id="runtime_safety_config_manual_validation_v1",
        producer_id="runtime_safety_config_validation",
        domain="active_controls",
        target_source_id="src_data_runtime_runtime_safety_audit_config_json",
        target_canonical_path="data/runtime/runtime_safety_audit_config.json",
        expected_timestamp_field="generated_at_utc",
        entry_criteria=(
            "The runtime-safety audit source is STALE or CRITICAL_STALE.",
            "The operator reviews the canonical paper configuration without modifying it.",
            "Execution occurs manually outside Streamlit and outside this audit CLI.",
        ),
        post_refresh_success_criteria=(
            "The expected JSON artifact exists and parses successfully.",
            "The audit is safe, paper_only and shadow_only, with live and orders disabled.",
            "A dashboard snapshot rebuild removes the source ID from global_blocking_reasons.",
        ),
        manual_closeout_condition=(
            "Close manually only after the validation remains paper/shadow safe, its timestamp "
            "is current, and the source ID is absent after snapshot rebuild."
        ),
        rollback_or_abort_condition=(
            "Abort closeout on any unsafe flag, invalid timestamp, validation failure, config "
            "mutation, or remaining source blocker."
        ),
        operator_notes=(
            "The producer validates existing configuration; this contract does not authorize edits.",
            "Live, canary, and order submission remain blocked after freshness remediation.",
        ),
    ),
}


def audit_runtime_freshness_producer_contracts(
    *,
    now_utc: datetime,
    producer_audit: Mapping[str, Any],
    safety_payloads: Sequence[Any] = (),
    input_errors: Sequence[str] = (),
    contract_definitions: Mapping[str, ContractDefinition] | None = None,
) -> dict[str, Any]:
    definitions = dict(
        CONTRACT_DEFINITIONS if contract_definitions is None else contract_definitions
    )
    current = _ensure_utc(now_utc)
    producer_rows = _mapping_rows(producer_audit.get("producer_rows"))
    rows_by_producer = {str(row.get("producer_id", "")): row for row in producer_rows}
    contracts = [
        _build_contract(definition, rows_by_producer.get(producer_id), current)
        for producer_id, definition in definitions.items()
    ]

    required_ids = set(CONTRACT_DEFINITIONS)
    missing_contracts = sorted(required_ids - set(definitions))
    incomplete_contracts = sorted(
        str(contract["contract_id"])
        for contract in contracts
        if not _contract_complete(contract)
    )
    unsafe_flags = _unsafe_safety_flags(
        _conservative_safety_flags((producer_audit, *safety_payloads))
    )
    normalized_input_errors = sorted({str(error) for error in input_errors if error})
    critical_blockers = int(producer_audit.get("critical_freshness_blockers_total", 0) or 0)
    producer_status = str(producer_audit.get("status", "blocked")).lower()
    producer_input_errors = [
        str(error) for error in producer_audit.get("input_errors", []) if error
    ]
    all_input_errors = sorted({*normalized_input_errors, *producer_input_errors})

    contracts_ready_total = sum(_contract_complete(contract) for contract in contracts)
    contracts_blocked_total = len(contracts) - contracts_ready_total + len(missing_contracts)
    manual_closeout_allowed = not (
        critical_blockers
        or missing_contracts
        or incomplete_contracts
        or unsafe_flags
        or all_input_errors
        or producer_status == "blocked"
    )

    if not manual_closeout_allowed and (
        missing_contracts
        or incomplete_contracts
        or unsafe_flags
        or all_input_errors
        or producer_status == "blocked"
    ):
        status = "blocked"
        reason = "required_contract_input_or_safety_violation"
    elif critical_blockers:
        status = "warning"
        reason = "manual_external_execution_and_closeout_required"
    else:
        status = "ok"
        reason = "no_critical_freshness_blockers"

    safety_flags = _conservative_safety_flags((producer_audit, *safety_payloads))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "generated_at_utc": iso_utc(current),
        "contracts_total": len(contracts),
        "contracts_ready_total": contracts_ready_total,
        "contracts_blocked_total": contracts_blocked_total,
        "manual_closeout_allowed": manual_closeout_allowed,
        "producer_contracts": contracts,
        "manual_runbook": _manual_runbook(contracts),
        "pre_execution_checks": _pre_execution_checks(contracts),
        "post_execution_checks": _post_execution_checks(contracts),
        "required_artifacts": _required_artifacts(contracts),
        "closeout_criteria": _closeout_criteria(contracts),
        "missing_required_contracts": missing_contracts,
        "incomplete_contracts": incomplete_contracts,
        "input_errors": all_input_errors,
        "safety_violations": unsafe_flags,
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "safety_flags": safety_flags,
    }
    return json_safe(payload)


def load_runtime_freshness_producer_contract_inputs(
    project_root: Path,
) -> dict[str, Any]:
    reports = project_root / "data/reports"
    summary = _load_mapping(reports / "dashboard_snapshot_build_summary.json")
    global_snapshot = _load_mapping(reports / "dashboard_global_status_snapshot.json")
    report = _load_mapping(project_root / PRODUCER_AUDIT_REPORT)
    embedded = _embedded_producer_audit(global_snapshot, summary)
    producer_audit = _latest_payload(report, embedded)
    input_errors: list[str] = []
    if not summary:
        input_errors.append(
            "missing_or_invalid:data/reports/dashboard_snapshot_build_summary.json"
        )
    if not global_snapshot:
        input_errors.append(
            "missing_or_invalid:data/reports/dashboard_global_status_snapshot.json"
        )
    if not producer_audit:
        input_errors.append("missing_or_invalid:runtime_freshness_producer_audit")
    return {
        "producer_audit": producer_audit,
        "safety_payloads": [summary, global_snapshot],
        "input_errors": input_errors,
    }


def _build_contract(
    definition: ContractDefinition,
    producer_row: Mapping[str, Any] | None,
    now_utc: datetime,
) -> dict[str, Any]:
    row = producer_row or {}
    producer_definition = next(
        (
            candidate
            for candidate in PRODUCER_DEFINITIONS.values()
            if candidate.producer_id == definition.producer_id
        ),
        None,
    )
    manual_execution_hint = str(row.get("manual_command_hint", "")).strip()
    if not manual_execution_hint and producer_definition is not None:
        manual_execution_hint = producer_definition.manual_command_hint
    verification_command = str(row.get("verification_command", "")).strip()
    if not verification_command and producer_definition is not None:
        verification_command = producer_definition.verification_command
    verification_commands = [
        command
        for command in (
            verification_command,
            "python scripts/build_dashboard_snapshots.py --project-root . "
            "--output-dir data/reports --strict false --once --json",
            "python scripts/audit_runtime_freshness_producer_contracts_v1.py "
            "--project-root . --json",
        )
        if command
    ]
    return {
        "contract_id": definition.contract_id,
        "producer_id": definition.producer_id,
        "domain": definition.domain,
        "target_source_id": definition.target_source_id,
        "target_canonical_path": definition.target_canonical_path,
        "current_status": str(row.get("current_status", "NOT_REQUIRED")).upper(),
        "current_freshness_status": str(
            row.get("current_freshness_status", "NOT_REQUIRED")
        ).upper(),
        "current_health_status": str(
            row.get("current_health_status", "NOT_REQUIRED")
        ).upper(),
        "entry_criteria": list(definition.entry_criteria),
        "manual_execution_hint": manual_execution_hint,
        "expected_artifact_path": definition.target_canonical_path,
        "expected_schema_version": row.get("expected_schema_version"),
        "expected_timestamp_field": definition.expected_timestamp_field,
        "max_acceptable_age_seconds_after_refresh": row.get("max_age_seconds"),
        "verification_commands": verification_commands,
        "post_refresh_success_criteria": list(
            definition.post_refresh_success_criteria
        ),
        "manual_closeout_condition": definition.manual_closeout_condition,
        "rollback_or_abort_condition": definition.rollback_or_abort_condition,
        "operator_notes": list(definition.operator_notes),
        "execution_location": "manual_outside_dashboard",
        "requires_manual_operator": True,
        "execution_allowed": False,
        "safe_to_execute_from_dashboard": False,
        "changes_runtime": False,
        "changes_risk": False,
        "changes_model": False,
        "sends_orders": False,
        "sends_notifications": False,
        "audited_at_utc": iso_utc(now_utc),
    }


def _contract_complete(contract: Mapping[str, Any]) -> bool:
    required_text = (
        "contract_id",
        "producer_id",
        "domain",
        "target_source_id",
        "target_canonical_path",
        "manual_execution_hint",
        "expected_artifact_path",
        "expected_timestamp_field",
        "manual_closeout_condition",
        "rollback_or_abort_condition",
    )
    required_lists = (
        "entry_criteria",
        "verification_commands",
        "post_refresh_success_criteria",
        "operator_notes",
    )
    return all(str(contract.get(key, "")).strip() for key in required_text) and all(
        isinstance(contract.get(key), list) and bool(contract[key])
        for key in required_lists
    )


def _manual_runbook(contracts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "step": index,
            "contract_id": contract["contract_id"],
            "producer_id": contract["producer_id"],
            "entry_criteria": contract["entry_criteria"],
            "manual_execution_hint": contract["manual_execution_hint"],
            "execution_location": "manual_outside_dashboard",
            "execution_allowed": False,
        }
        for index, contract in enumerate(contracts, start=1)
    ]


def _pre_execution_checks(contracts: Sequence[Mapping[str, Any]]) -> list[str]:
    checks = [
        "Confirm the dashboard remains BLOCKED and live/canary/orders remain disabled.",
        "Review the current source-health row and the producer audit before execution.",
        "Confirm execution is manual, external to Streamlit, and uses the documented producer.",
    ]
    checks.extend(
        f"{contract['contract_id']}: {criterion}"
        for contract in contracts
        for criterion in contract["entry_criteria"]
    )
    return checks


def _post_execution_checks(contracts: Sequence[Mapping[str, Any]]) -> list[str]:
    checks = [
        f"{contract['contract_id']}: {criterion}"
        for contract in contracts
        for criterion in contract["post_refresh_success_criteria"]
    ]
    checks.append(
        "Verify global, runtime-evidence, and combined blocker lists changed only from new materialized evidence."
    )
    return checks


def _required_artifacts(contracts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "contract_id": contract["contract_id"],
            "path": contract["expected_artifact_path"],
            "expected_schema_version": contract["expected_schema_version"],
            "expected_timestamp_field": contract["expected_timestamp_field"],
        }
        for contract in contracts
    ]


def _closeout_criteria(contracts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "contract_id": contract["contract_id"],
            "condition": contract["manual_closeout_condition"],
            "automatic_release": False,
        }
        for contract in contracts
    ]


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


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


def _embedded_producer_audit(
    global_snapshot: Mapping[str, Any], summary: Mapping[str, Any]
) -> dict[str, Any]:
    key = "runtime_evidence_freshness_remediation_producers"
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
    value = payload.get("generated_at_utc")
    if value in (None, ""):
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return _ensure_utc(parsed)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
