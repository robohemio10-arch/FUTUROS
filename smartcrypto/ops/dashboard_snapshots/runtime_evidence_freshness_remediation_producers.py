from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.builder_common import iso_utc, json_safe
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import SAFETY_FLAGS


SCHEMA_VERSION = "runtime_evidence_freshness_remediation_producers_audit_v1"
STALE_STATUSES = {"STALE", "WARNING_STALE", "CRITICAL_STALE"}
CRITICAL_SOURCE_STATUSES = {
    "STALE",
    "INVALID_TIMESTAMP",
    "MISSING_REQUIRED",
    "INVALID_JSON",
    "INVALID_SCHEMA",
    "READ_ERROR",
}

FORBIDDEN_ACTIONS = [
    "Do not execute any producer from the dashboard or this audit CLI.",
    "Do not disable the kill switch to refresh its timestamp.",
    "Do not enable live, canary, private exchange access, or order submission.",
    "Do not change risk, models, datasets, signals, YAML configuration, or notifications.",
    "Do not infer readiness from refreshed timestamps alone.",
]


@dataclass(frozen=True)
class ProducerDefinition:
    producer_id: str
    domain: str
    target_source_id: str
    target_canonical_path: str
    manual_command_hint: str
    expected_schema_version: str | None
    verification_command: str
    post_refresh_closeout_condition: str


PRODUCER_DEFINITIONS: dict[str, ProducerDefinition] = {
    "data/reports/market_data_health_audit_report.json": ProducerDefinition(
        producer_id="market_data_health_audit",
        domain="market_data",
        target_source_id="src_data_reports_market_data_health_audit_report_json",
        target_canonical_path="data/reports/market_data_health_audit_report.json",
        manual_command_hint=(
            "python scripts/run_market_data_health_audit.py "
            "--runtime-candles data/runtime/market_health/candles.json "
            "--ticker data/runtime/market_health/ticker.json "
            "--order-book data/runtime/market_health/order_book.json "
            "--trades data/runtime/market_health/trades.jsonl "
            "--rest-snapshot data/runtime/market_health/rest_snapshot.json "
            "--ws-heartbeat data/runtime/market_health/ws_heartbeat.json "
            "--report data/reports/market_data_health_audit_report.json --strict"
        ),
        expected_schema_version=None,
        verification_command=(
            "Get-Content -Raw data/reports/market_data_health_audit_report.json "
            "| ConvertFrom-Json | Select-Object status,generated_at_utc"
        ),
        post_refresh_closeout_condition=(
            "Source health reports OK with a valid current UTC timestamp and the source ID "
            "is absent from global_blocking_reasons after dashboard snapshot rebuild."
        ),
    ),
    "data/runtime/kill_switch.json": ProducerDefinition(
        producer_id="kill_switch_state_refresh",
        domain="portfolio_risk",
        target_source_id="src_data_runtime_kill_switch_json",
        target_canonical_path="data/runtime/kill_switch.json",
        manual_command_hint=(
            "python scripts/set_kill_switch.py --enabled true "
            "--reason manual_runtime_safety_freshness_refresh "
            "--path data/runtime/kill_switch.json"
        ),
        expected_schema_version=None,
        verification_command=(
            "Get-Content -Raw data/runtime/kill_switch.json | ConvertFrom-Json "
            "| Select-Object enabled,reason,updated_at"
        ),
        post_refresh_closeout_condition=(
            "Kill switch remains enabled, has a valid current UTC timestamp, and its source ID "
            "is absent from global_blocking_reasons after dashboard snapshot rebuild."
        ),
    ),
    "data/runtime/runtime_safety_audit_config.json": ProducerDefinition(
        producer_id="runtime_safety_config_validation",
        domain="active_controls",
        target_source_id="src_data_runtime_runtime_safety_audit_config_json",
        target_canonical_path="data/runtime/runtime_safety_audit_config.json",
        manual_command_hint=(
            "python scripts/validate_runtime_safety_config.py "
            "--config config/paper.example.yml --environment paper "
            "--report data/runtime/runtime_safety_audit_config.json --strict"
        ),
        expected_schema_version=None,
        verification_command=(
            "Get-Content -Raw data/runtime/runtime_safety_audit_config.json "
            "| ConvertFrom-Json | Select-Object status,generated_at_utc,paper_only,shadow_only,live_trading_enabled"
        ),
        post_refresh_closeout_condition=(
            "Runtime safety validation remains paper/shadow safe, has a valid current UTC timestamp, "
            "and its source ID is absent from global_blocking_reasons after dashboard snapshot rebuild."
        ),
    ),
}


def audit_runtime_evidence_freshness_remediation_producers(
    *,
    now_utc: datetime,
    dashboard_status: str,
    global_source_health_status: str,
    source_health_matrix: Sequence[Any],
    safety_payloads: Sequence[Any] = (),
    input_errors: Sequence[str] = (),
) -> dict[str, Any]:
    """Map critical freshness blockers to documented external producers."""
    current = _ensure_utc(now_utc)
    source_rows = [dict(row) for row in source_health_matrix if isinstance(row, Mapping)]
    freshness_rows = [row for row in source_rows if _is_freshness_blocker(row)]
    producer_rows: list[dict[str, Any]] = []
    unmapped_critical: list[str] = []
    invalid_critical_timestamps: list[str] = []

    for source_row in freshness_rows:
        canonical_path = str(source_row.get("canonical_path", ""))
        definition = PRODUCER_DEFINITIONS.get(canonical_path)
        if definition is None:
            if _is_critical(source_row):
                unmapped_critical.append(str(source_row.get("source_id", canonical_path)))
            continue
        producer_row = _producer_row(definition, source_row, current)
        producer_rows.append(producer_row)
        if _is_critical(source_row) and not producer_row["timestamp_valid"]:
            invalid_critical_timestamps.append(definition.target_source_id)

    safety_flags = _conservative_safety_flags(safety_payloads)
    safety_violations = _unsafe_safety_flags(safety_flags)
    normalized_input_errors = sorted({str(error) for error in input_errors if error})
    if (
        normalized_input_errors
        or unmapped_critical
        or invalid_critical_timestamps
        or safety_violations
    ):
        status = "blocked"
        reason = "input_mapping_timestamp_or_safety_violation"
    elif producer_rows:
        status = "warning"
        reason = "external_manual_producers_required"
    else:
        status = "ok"
        reason = "no_critical_freshness_blockers"

    sorted_rows = sorted(producer_rows, key=_producer_order)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "generated_at_utc": iso_utc(current),
        "dashboard_status": str(dashboard_status or "UNKNOWN").upper(),
        "global_source_health_status": str(
            global_source_health_status or "UNKNOWN"
        ).upper(),
        "freshness_blockers_total": len(freshness_rows),
        "critical_freshness_blockers_total": sum(
            1 for row in freshness_rows if _is_critical(row)
        ),
        "producer_rows": sorted_rows,
        "manual_execution_plan": _manual_execution_plan(sorted_rows),
        "post_execution_verification": _post_execution_verification(sorted_rows),
        "blocked_until_refreshed_sources": sorted(
            str(row["target_canonical_path"]) for row in sorted_rows
        ),
        "unmapped_critical_freshness_blockers": sorted(unmapped_critical),
        "invalid_critical_timestamp_sources": sorted(invalid_critical_timestamps),
        "input_errors": normalized_input_errors,
        "safety_violations": safety_violations,
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "safety_flags": safety_flags,
    }
    return json_safe(payload)


def load_freshness_producer_audit_inputs(project_root: Path) -> dict[str, Any]:
    reports = project_root / "data/reports"
    summary = _load_mapping(reports / "dashboard_snapshot_build_summary.json")
    global_snapshot = _load_mapping(reports / "dashboard_global_status_snapshot.json")
    input_errors = []
    if not summary:
        input_errors.append(
            "missing_or_invalid:data/reports/dashboard_snapshot_build_summary.json"
        )
    if not global_snapshot:
        input_errors.append(
            "missing_or_invalid:data/reports/dashboard_global_status_snapshot.json"
        )
    closeout = _embedded_or_file(
        summary,
        global_snapshot,
        "runtime_blockers_closeout_evidence",
        reports / "runtime_blockers_closeout_evidence_audit_v1.json",
    )
    operator_pack = _embedded_or_file(
        summary,
        global_snapshot,
        "runtime_blockers_operator_pack",
        reports / "dashboard_runtime_blockers_operator_pack_v1.json",
    )
    matrix = summary.get("source_health_matrix")
    if not isinstance(matrix, list):
        matrix = global_snapshot.get("source_health_matrix")
    return {
        "dashboard_status": str(
            global_snapshot.get("dashboard_status")
            or summary.get("dashboard_status")
            or "UNKNOWN"
        ),
        "global_source_health_status": str(
            global_snapshot.get("global_source_health_status")
            or summary.get("global_source_health_status")
            or "UNKNOWN"
        ),
        "source_health_matrix": list(matrix) if isinstance(matrix, list) else [],
        "safety_payloads": [summary, global_snapshot, closeout, operator_pack],
        "input_errors": input_errors,
    }


def _producer_row(
    definition: ProducerDefinition,
    source_row: Mapping[str, Any],
    now_utc: datetime,
) -> dict[str, Any]:
    effective_timestamp = source_row.get("effective_timestamp_utc")
    timestamp_valid = _valid_utc_timestamp(effective_timestamp)
    return {
        "producer_id": definition.producer_id,
        "domain": definition.domain,
        "target_source_id": definition.target_source_id,
        "target_canonical_path": definition.target_canonical_path,
        "current_status": str(source_row.get("status", "UNKNOWN")).upper(),
        "current_health_status": str(
            source_row.get("health_status", "UNKNOWN")
        ).upper(),
        "current_freshness_status": str(
            source_row.get("freshness_status", "UNKNOWN")
        ).upper(),
        "age_seconds": source_row.get("age_seconds"),
        "max_age_seconds": source_row.get("max_age_seconds"),
        "critical_age_seconds": source_row.get("critical_age_seconds"),
        "effective_timestamp_utc": effective_timestamp,
        "timestamp_valid": timestamp_valid,
        "manual_command_hint": definition.manual_command_hint,
        "expected_output_path": definition.target_canonical_path,
        "expected_schema_version": source_row.get("expected_schema_version")
        or definition.expected_schema_version,
        "verification_command": definition.verification_command,
        "post_refresh_closeout_condition": definition.post_refresh_closeout_condition,
        "execution_location": "manual_outside_dashboard",
        "execution_allowed": False,
        "safe_to_execute_from_dashboard": False,
        "requires_manual_operator": True,
        "changes_runtime": False,
        "changes_risk": False,
        "changes_model": False,
        "sends_orders": False,
        "sends_notifications": False,
        "audited_at_utc": iso_utc(now_utc),
    }


def _manual_execution_plan(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "step": index,
            "producer_id": row["producer_id"],
            "domain": row["domain"],
            "manual_command_hint": row["manual_command_hint"],
            "execution_location": "manual_outside_dashboard",
            "execution_allowed": False,
        }
        for index, row in enumerate(rows, start=1)
    ]


def _post_execution_verification(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    verification = [
        {
            "producer_id": row["producer_id"],
            "expected_output_path": row["expected_output_path"],
            "verification_command": row["verification_command"],
            "closeout_condition": row["post_refresh_closeout_condition"],
        }
        for row in rows
    ]
    verification.append(
        {
            "producer_id": "rebuild_dashboard_snapshots",
            "expected_output_path": "data/reports/dashboard_snapshot_build_summary.json",
            "verification_command": (
                "python scripts/build_dashboard_snapshots.py --project-root . "
                "--output-dir data/reports --strict false --once --json"
            ),
            "closeout_condition": (
                "Refreshed source IDs are absent from global_blocking_reasons while live, "
                "canary, and order submission remain disabled."
            ),
        }
    )
    return verification


def _is_freshness_blocker(row: Mapping[str, Any]) -> bool:
    status = str(row.get("status", "")).upper()
    freshness = str(row.get("freshness_status", "")).upper()
    return bool(row.get("blocks_dashboard_readiness", False)) and (
        status in CRITICAL_SOURCE_STATUSES or freshness in STALE_STATUSES
    )


def _is_critical(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("severity", "")).upper() == "CRITICAL"
        or str(row.get("health_status", "")).upper() == "BLOCKED"
        or bool(row.get("blocks_dashboard_readiness", False))
    )


def _producer_order(row: Mapping[str, Any]) -> tuple[int, str]:
    order = {
        "market_data_health_audit": 1,
        "kill_switch_state_refresh": 2,
        "runtime_safety_config_validation": 3,
    }
    producer_id = str(row.get("producer_id", ""))
    return order.get(producer_id, 99), producer_id


def _valid_utc_timestamp(value: Any) -> bool:
    if value in (None, ""):
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(timezone.utc).utcoffset() is not None


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


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
