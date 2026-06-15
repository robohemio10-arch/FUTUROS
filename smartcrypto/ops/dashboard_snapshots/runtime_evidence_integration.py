from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.builder_common import SAFETY_FLAGS, iso_utc, json_safe

TIMESTAMP_FIELDS = (
    "last_updated_utc",
    "generated_at_utc",
    "build_finished_utc",
    "build_started_utc",
    "timestamp_utc",
    "created_at_utc",
    "report_generated_utc",
    "finished_utc",
    "updated_at_utc",
    "as_of_utc",
)

EVIDENCE_SOURCE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "source_id": "runtime_evidence_pack_v2",
        "path": "data/reports/runtime_evidence_pack_v2.json",
        "required": True,
        "domain": "runtime_evidence",
    },
    {
        "source_id": "readiness_snapshot_v2",
        "path": "data/reports/readiness_snapshot_v2.json",
        "required": True,
        "domain": "readiness",
    },
    {
        "source_id": "paper_runtime_health_and_freshness_report",
        "path": "data/reports/paper_runtime_health_and_freshness_report.json",
        "required": False,
        "domain": "paper_runtime_health",
    },
    {
        "source_id": "paper_runtime_container_snapshot_report",
        "path": "data/reports/paper_runtime_container_snapshot_report.json",
        "required": False,
        "domain": "paper_runtime_container",
    },
    {
        "source_id": "paper_shadow_soak_gap_accounting_report",
        "path": "data/reports/paper_shadow_soak_gap_accounting_report.json",
        "required": True,
        "domain": "paper_shadow_soak",
    },
    {
        "source_id": "paper_shadow_soak_report",
        "path": "data/reports/paper_shadow_soak_report.json",
        "required": False,
        "domain": "paper_shadow_soak",
    },
    {
        "source_id": "daily_evidence_pack_latest",
        "path": "data/reports/daily_evidence_pack_latest.json",
        "required": False,
        "domain": "daily_evidence_pack",
    },
    {
        "source_id": "runtime_evidence_refresh_report",
        "path": "data/reports/runtime_evidence_refresh_report.json",
        "required": False,
        "domain": "runtime_evidence",
    },
    {
        "source_id": "dashboard_global_status_snapshot",
        "path": "data/reports/dashboard_global_status_snapshot.json",
        "required": False,
        "domain": "dashboard_generated",
    },
    {
        "source_id": "dashboard_snapshot_build_summary",
        "path": "data/reports/dashboard_snapshot_build_summary.json",
        "required": False,
        "domain": "dashboard_generated",
    },
)

BLOCKING_STATUS_VALUES = {"BLOCKED", "MISSING_REQUIRED", "ERROR", "CRITICAL", "FAILED"}
DEGRADED_STATUS_VALUES = {"WARNING", "DEGRADED", "MISSING_OPTIONAL", "STALE", "UNKNOWN"}
STALE_FRESHNESS_VALUES = {"CRITICAL_STALE", "WARNING_STALE", "STALE"}


def build_runtime_evidence_view(
    *,
    project_root: Path,
    now_utc: datetime,
    source_closeout: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a read-only runtime evidence view from already materialized files."""
    source_rows = _source_rows_by_path(source_closeout)
    evidence_sources = [
        _evaluate_evidence_source(project_root, definition, source_rows, now_utc)
        for definition in EVIDENCE_SOURCE_DEFINITIONS
    ]
    payloads = {
        str(source["source_id"]): source.get("payload")
        for source in evidence_sources
        if isinstance(source.get("payload"), Mapping)
    }

    source_health_status = str(source_closeout.get("global_source_health_status", "UNKNOWN"))
    dashboard_status = str(source_closeout.get("dashboard_status", "UNKNOWN"))
    blocking_sources = _blocking_sources(evidence_sources)
    degraded_sources = _degraded_sources(evidence_sources)
    missing_sources = [str(source["path"]) for source in evidence_sources if source.get("missing")]
    stale_sources = [str(source["path"]) for source in evidence_sources if source.get("stale")]

    readiness_payload = _mapping(payloads.get("readiness_snapshot_v2"))
    runtime_pack_payload = _mapping(payloads.get("runtime_evidence_pack_v2"))
    paper_runtime_payload = _mapping(payloads.get("paper_runtime_health_and_freshness_report"))
    container_payload = _mapping(payloads.get("paper_runtime_container_snapshot_report"))
    gap_payload = _mapping(payloads.get("paper_shadow_soak_gap_accounting_report"))
    soak_payload = _mapping(payloads.get("paper_shadow_soak_report"))

    runtime_pack_status = _status_from_source_or_payload(
        evidence_sources,
        "runtime_evidence_pack_v2",
        runtime_pack_payload,
        ("status", "runtime_evidence_status", "runtime_evidence_pack_status"),
    )
    readiness_status = _status_from_source_or_payload(
        evidence_sources,
        "readiness_snapshot_v2",
        readiness_payload,
        ("status", "readiness_status", "thirty_day_readiness_status"),
    )
    paper_runtime_status = _status_from_source_or_payload(
        evidence_sources,
        "paper_runtime_health_and_freshness_report",
        paper_runtime_payload,
        ("paper_runtime_health_status", "status"),
    )
    container_status = _status_from_source_or_payload(
        evidence_sources,
        "paper_runtime_container_snapshot_report",
        container_payload,
        ("container_snapshot_status", "docker_services_status", "status"),
        missing_default="DISABLED",
    )
    gap_status = _status_from_source_or_payload(
        evidence_sources,
        "paper_shadow_soak_gap_accounting_report",
        gap_payload,
        ("status", "gap_accounting_status", "thirty_day_readiness_status"),
    )
    soak_status = _status_from_source_or_payload(
        evidence_sources,
        "paper_shadow_soak_report",
        soak_payload,
        ("status", "soak_status", "thirty_day_readiness_status"),
        missing_default="UNKNOWN",
    )

    raw_canary = _first_bool(
        runtime_pack_payload,
        readiness_payload,
        gap_payload,
        keys=("canary_release_allowed",),
    )
    raw_live = _first_bool(
        runtime_pack_payload,
        readiness_payload,
        gap_payload,
        keys=("live_release_allowed", "live_trading_enabled"),
    )
    source_health_blocks = source_health_status == "BLOCKED" or dashboard_status == "BLOCKED"
    gap_blocks = _gap_blocks_readiness(gap_payload, readiness_payload)
    readiness_blocks = _status_blocks(readiness_status)
    runtime_pack_blocks = _status_blocks(runtime_pack_status)

    blocking_reasons = list(blocking_sources)
    if source_health_blocks:
        blocking_reasons.append(f"source_health:{source_health_status}")
    if runtime_pack_blocks:
        blocking_reasons.append(f"runtime_evidence_pack:{runtime_pack_status}")
    if readiness_blocks:
        blocking_reasons.append(f"readiness:{readiness_status}")
    if gap_blocks:
        blocking_reasons.append("paper_shadow_soak_gap_accounting:BLOCKED")
    if raw_canary is True and source_health_blocks:
        blocking_reasons.append("canary_release_allowed_conflicts_with_blocked_source_health")
    if raw_live is True and (source_health_blocks or blocking_reasons):
        blocking_reasons.append("live_release_allowed_conflicts_with_incomplete_evidence_chain")

    status = "BLOCKED" if blocking_reasons else "DEGRADED" if degraded_sources else "OK"
    if not evidence_sources:
        status = "UNKNOWN"

    view = {
        "schema_version": "dashboard_runtime_evidence_integration_v1",
        "last_updated_utc": iso_utc(now_utc),
        "runtime_evidence_status": status,
        "runtime_evidence_reason": _reason(status, blocking_reasons, degraded_sources),
        "runtime_evidence_pack_status": runtime_pack_status,
        "readiness_status": readiness_status,
        "readiness_reason": str(_first_value(readiness_payload, ("reason", "readiness_reason"), "not_available")),
        "paper_runtime_health_status": paper_runtime_status,
        "paper_runtime_alive": _truthy(_first_value(paper_runtime_payload, ("paper_runtime_alive",), False)),
        "paper_runtime_fresh": _truthy(_first_value(paper_runtime_payload, ("paper_runtime_fresh",), False)),
        "container_snapshot_status": container_status,
        "soak_status": soak_status,
        "gap_accounting_status": gap_status,
        "continuous_valid_soak_days": _float_from_payloads(
            gap_payload,
            readiness_payload,
            keys=("continuous_valid_soak_days",),
        ),
        "observed_calendar_days": _float_from_payloads(
            gap_payload,
            readiness_payload,
            keys=("observed_calendar_days",),
        ),
        "required_soak_days": _float_from_payloads(
            gap_payload,
            readiness_payload,
            keys=("required_soak_days",),
            default=30.0,
        ),
        "critical_gap_count": int(
            _float_from_payloads(gap_payload, readiness_payload, keys=("critical_gap_count",), default=0.0)
        ),
        "warning_gap_count": int(
            _float_from_payloads(gap_payload, readiness_payload, keys=("warning_gap_count",), default=0.0)
        ),
        "max_gap_minutes": _float_from_payloads(
            gap_payload,
            readiness_payload,
            keys=("max_gap_minutes",),
        ),
        "seven_day_diagnostic_status": str(
            _first_value(gap_payload, ("seven_day_diagnostic_status",), "unknown")
        ),
        "thirty_day_readiness_status": str(
            _first_value(gap_payload, ("thirty_day_readiness_status",), "blocked")
        ),
        "canary_release_allowed_raw": raw_canary is True,
        "live_release_allowed_raw": raw_live is True,
        "canary_release_allowed": False,
        "live_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "evidence_sources_total": len(evidence_sources),
        "evidence_sources_ok": _count_by_status(evidence_sources, "OK"),
        "evidence_sources_degraded": len(degraded_sources),
        "evidence_sources_blocked": len(blocking_sources),
        "evidence_sources_missing": len(missing_sources),
        "evidence_sources_stale": len(stale_sources),
        "blocking_evidence_sources": sorted(set(blocking_reasons)),
        "degraded_evidence_sources": degraded_sources,
        "missing_evidence_sources": missing_sources,
        "stale_evidence_sources": stale_sources,
        "evidence_sources": _strip_payloads(evidence_sources),
        "operator_action": _operator_action(status),
        "remediation_hint": _remediation_hint(status),
        "source_health_global_status": source_health_status,
        "dashboard_status": dashboard_status,
        "runtime_evidence_safety_flags": dict(SAFETY_FLAGS),
        "safety_flags": dict(SAFETY_FLAGS),
    }
    return json_safe(view)


def runtime_evidence_section_status(view: Mapping[str, Any]) -> str:
    status = str(view.get("runtime_evidence_status", "UNKNOWN")).upper()
    if status in {"OK", "WARNING", "DEGRADED", "BLOCKED", "MISSING", "STALE", "UNKNOWN", "DISABLED", "NOT_APPLICABLE"}:
        return status
    return "UNKNOWN"


def _evaluate_evidence_source(
    project_root: Path,
    definition: Mapping[str, Any],
    source_rows: Mapping[str, Mapping[str, Any]],
    now_utc: datetime,
) -> dict[str, Any]:
    relative_path = str(definition["path"])
    target = project_root / relative_path
    row = dict(source_rows.get(relative_path, {}))
    required = bool(definition.get("required", False))
    payload: Any = None
    load_error: str | None = None
    exists = target.is_file()
    if exists:
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            load_error = f"invalid_json:{exc.msg}"
        except OSError as exc:
            load_error = f"read_error:{type(exc).__name__}:{exc}"

    if row:
        status = str(row.get("status") or row.get("health_status") or "UNKNOWN").upper()
        health_status = str(row.get("health_status", "UNKNOWN")).upper()
        freshness_status = str(row.get("freshness_status", "UNKNOWN")).upper()
        stale = bool(row.get("stale", False)) or freshness_status in STALE_FRESHNESS_VALUES
    elif not exists:
        status = "MISSING_REQUIRED" if required else "MISSING_OPTIONAL"
        health_status = "BLOCKED" if required else "DEGRADED"
        freshness_status = "UNKNOWN"
        stale = False
    elif load_error:
        status = "ERROR"
        health_status = "BLOCKED" if required else "DEGRADED"
        freshness_status = "UNKNOWN"
        stale = False
    else:
        status = _normalize_payload_status(_mapping(payload), default="OK")
        health_status = "HEALTHY" if status == "OK" else "DEGRADED"
        freshness_status = "UNKNOWN"
        stale = False

    blocking = required and (
        status in BLOCKING_STATUS_VALUES
        or health_status == "BLOCKED"
        or freshness_status == "CRITICAL_STALE"
        or load_error is not None
        or not exists
    )
    degraded = (not blocking) and (
        status in DEGRADED_STATUS_VALUES
        or health_status == "DEGRADED"
        or stale
        or load_error is not None
        or not exists
    )
    effective_timestamp = row.get("effective_timestamp_utc") or _payload_timestamp(_mapping(payload))
    return {
        "source_id": definition["source_id"],
        "path": relative_path,
        "domain": definition.get("domain", "runtime_evidence"),
        "required": required,
        "exists": exists,
        "missing": not exists,
        "status": status,
        "health_status": health_status,
        "freshness_status": freshness_status,
        "stale": stale,
        "blocking": blocking,
        "degraded": degraded,
        "load_error": load_error,
        "effective_timestamp_utc": effective_timestamp,
        "timestamp_source": row.get("timestamp_source", "payload" if effective_timestamp else "unavailable"),
        "age_seconds": row.get("age_seconds"),
        "operator_hint": row.get(
            "operator_hint",
            f"Run the documented producer for {relative_path}, then rebuild dashboard snapshots.",
        ),
        "remediation_action": row.get(
            "remediation_action",
            "Refresh the documented evidence producer; do not generate data from Streamlit.",
        ),
        "reason": load_error or row.get("reason") or _source_reason(exists, status, required),
        "payload": payload,
    }


def _source_rows_by_path(source_closeout: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    output: dict[str, Mapping[str, Any]] = {}
    for row in source_closeout.get("source_health_matrix", source_closeout.get("source_matrix", [])):
        if isinstance(row, Mapping):
            path = str(row.get("canonical_path") or "")
            if path:
                output[path] = row
    return output


def _blocking_sources(evidence_sources: list[Mapping[str, Any]]) -> list[str]:
    return [f"{source['source_id']}:{source['status']}" for source in evidence_sources if source.get("blocking")]


def _degraded_sources(evidence_sources: list[Mapping[str, Any]]) -> list[str]:
    return [f"{source['source_id']}:{source['status']}" for source in evidence_sources if source.get("degraded")]


def _count_by_status(evidence_sources: list[Mapping[str, Any]], status: str) -> int:
    return sum(1 for source in evidence_sources if str(source.get("status", "")).upper() == status)


def _strip_payloads(evidence_sources: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in evidence_sources:
        row = dict(source)
        row.pop("payload", None)
        output.append(row)
    return output


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _payload_timestamp(payload: Mapping[str, Any]) -> str | None:
    value = _first_value(payload, TIMESTAMP_FIELDS, None)
    return str(value) if value is not None else None


def _first_value(payload: Any, keys: tuple[str, ...], default: Any = None) -> Any:
    if isinstance(payload, Mapping):
        for key in keys:
            if key in payload and payload[key] is not None:
                return payload[key]
        for child in payload.values():
            found = _first_value(child, keys, None)
            if found is not None:
                return found
    elif isinstance(payload, list | tuple):
        for child in payload:
            found = _first_value(child, keys, None)
            if found is not None:
                return found
    return default


def _first_bool(*payloads: Mapping[str, Any], keys: tuple[str, ...]) -> bool | None:
    for payload in payloads:
        value = _first_value(payload, keys, None)
        if value is not None:
            return _truthy(value)
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "allowed", "enabled"}


def _float_from_payloads(
    *payloads: Mapping[str, Any],
    keys: tuple[str, ...],
    default: float = 0.0,
) -> float:
    for payload in payloads:
        value = _first_value(payload, keys, None)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default
    return default


def _normalize_payload_status(payload: Mapping[str, Any], *, default: str = "UNKNOWN") -> str:
    raw = _first_value(
        payload,
        (
            "status",
            "runtime_evidence_status",
            "readiness_status",
            "paper_runtime_health_status",
            "gap_accounting_status",
            "thirty_day_readiness_status",
        ),
        default,
    )
    normalized = str(raw or default).strip().upper()
    if normalized in {"PASS", "PASSED", "VALID", "READY", "FRESH"}:
        return "OK"
    if normalized in {"NOT_REACHED", "INSUFFICIENT", "CRITICAL", "FAILED", "FAIL"}:
        return "BLOCKED"
    if normalized in {"WARN", "WARNING_STALE"}:
        return "WARNING"
    if normalized in {"CRITICAL_STALE"}:
        return "STALE"
    if normalized in {"OK", "WARNING", "DEGRADED", "BLOCKED", "MISSING", "STALE", "UNKNOWN", "DISABLED", "NOT_APPLICABLE"}:
        return normalized
    return default


def _status_from_source_or_payload(
    evidence_sources: list[Mapping[str, Any]],
    source_id: str,
    payload: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    missing_default: str = "MISSING",
) -> str:
    source = next((item for item in evidence_sources if item.get("source_id") == source_id), None)
    if source and source.get("missing"):
        return missing_default
    if source and source.get("load_error"):
        return "BLOCKED" if source.get("required") else "DEGRADED"
    if payload:
        return _normalize_payload_status({"status": _first_value(payload, keys, "UNKNOWN")})
    if source:
        status = str(source.get("status", "UNKNOWN")).upper()
        if status == "OK":
            return "OK"
        if status == "MISSING_OPTIONAL":
            return missing_default if missing_default != "MISSING" else "MISSING"
        if status == "MISSING_REQUIRED":
            return "MISSING"
        return status
    return "UNKNOWN"


def _status_blocks(status: str) -> bool:
    return str(status).upper() in {"BLOCKED", "MISSING", "MISSING_REQUIRED", "ERROR", "STALE"}


def _gap_blocks_readiness(
    gap_payload: Mapping[str, Any],
    readiness_payload: Mapping[str, Any],
) -> bool:
    critical_gaps = int(
        _float_from_payloads(gap_payload, readiness_payload, keys=("critical_gap_count",), default=0.0)
    )
    thirty_day = str(
        _first_value(gap_payload, ("thirty_day_readiness_status",), "blocked")
    ).lower()
    gap_status = _normalize_payload_status(gap_payload, default="UNKNOWN")
    return critical_gaps > 0 or thirty_day in {"blocked", "not_reached", "failed"} or gap_status == "BLOCKED"


def _reason(status: str, blocking: list[str], degraded: list[str]) -> str:
    if status == "BLOCKED":
        return ";".join(blocking[:10]) or "runtime_evidence_blocked"
    if status == "DEGRADED":
        return ";".join(degraded[:10]) or "runtime_evidence_degraded"
    if status == "OK":
        return "runtime_evidence_chain_ok"
    return "runtime_evidence_unknown"


def _operator_action(status: str) -> str:
    if status == "BLOCKED":
        return "Regenerate or repair blocked evidence producers outside Streamlit, then rebuild dashboard snapshots."
    if status == "DEGRADED":
        return "Review degraded evidence sources; do not change runtime state from the dashboard."
    if status == "OK":
        return "Continue monitoring; no dashboard-side action is authorized."
    return "Inspect evidence producers and source health; no automated action is authorized."


def _remediation_hint(status: str) -> str:
    if status == "BLOCKED":
        return "Use the documented runtime evidence/readiness runbooks. Keep live, canary and order submission disabled."
    if status == "DEGRADED":
        return "Refresh optional observability evidence if needed. This does not authorize live/canary."
    return "Read-only evidence view only."


def _source_reason(exists: bool, status: str, required: bool) -> str:
    if not exists:
        return "required_evidence_missing" if required else "optional_evidence_missing"
    if status == "OK":
        return "evidence_available"
    return f"evidence_status:{status}"
