from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smartcrypto.dashboard.ui.tables import render_html_table


SUMMARY_COLUMNS = [
    "status",
    "dashboard_status",
    "global_source_health_status",
    "runtime_evidence_integration_status",
    "closeout_allowed",
]

EVIDENCE_COLUMNS = [
    "evidence_id",
    "domain",
    "canonical_path",
    "exists",
    "status",
    "health_status",
    "freshness_status",
    "effective_timestamp_utc",
    "age_seconds",
    "timestamp_valid",
    "payload_status",
    "current_blocker_present",
    "closeout_state",
    "closeout_reason",
    "safe_to_infer_closeout",
]

ISSUE_COLUMNS = ["category", "value"]
SAFETY_COLUMNS = ["flag", "value"]


def render_runtime_blockers_closeout_evidence(
    snapshot: Mapping[str, Any],
    *,
    ui: Any,
) -> None:
    payload = runtime_blockers_closeout_evidence_view(snapshot)
    ui.subheader("Runtime Blockers: Closeout Evidence Audit")
    if not payload:
        ui.info("UNKNOWN: auditoria de closeout evidence ausente no snapshot.")
        return

    ui.warning(
        "Auditoria estritamente read-only. Closeout evidence nao autoriza live, "
        "canary, ordens ou alteracao de runtime."
    )
    _render_table(
        ui,
        [closeout_summary_row(payload)],
        SUMMARY_COLUMNS,
        ["status"],
        "Resumo de closeout evidence ausente.",
    )
    ui.markdown("#### Evidencias de closeout")
    _render_table(
        ui,
        closeout_evidence_rows(payload),
        EVIDENCE_COLUMNS,
        ["status", "closeout_state"],
        "Nenhuma evidencia de closeout mapeada.",
    )
    ui.markdown("#### Indicadores e fontes")
    _render_table(
        ui,
        closeout_issue_rows(payload),
        ISSUE_COLUMNS,
        [],
        "Nenhum bypass, closeout suspeito ou problema de fonte detectado.",
    )
    ui.markdown("#### Safety flags")
    _render_table(
        ui,
        closeout_safety_rows(payload),
        SAFETY_COLUMNS,
        [],
        "Safety flags ausentes.",
    )


def runtime_blockers_closeout_evidence_view(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    direct = snapshot.get("runtime_blockers_closeout_evidence")
    if isinstance(direct, Mapping):
        return dict(direct)
    sections = snapshot.get("sections", {})
    if not isinstance(sections, Mapping):
        return {}
    section = sections.get("runtime_blockers_closeout_evidence", {})
    if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
        return dict(section["data"])
    return {}


def closeout_summary_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(payload.get("status", "blocked")).upper(),
        "dashboard_status": payload.get("dashboard_status", "UNKNOWN"),
        "global_source_health_status": payload.get(
            "global_source_health_status", "UNKNOWN"
        ),
        "runtime_evidence_integration_status": payload.get(
            "runtime_evidence_integration_status", "UNKNOWN"
        ),
        "closeout_allowed": bool(payload.get("closeout_allowed", False)),
    }


def closeout_evidence_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("closeout_evidence_rows"))


def closeout_issue_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in (
        "bypass_indicators",
        "suspicious_closeouts",
        "invalid_timestamp_sources",
        "missing_evidence_sources",
        "stale_evidence_sources",
        "safety_violations",
    ):
        value = payload.get(key, [])
        if not isinstance(value, list):
            continue
        rows.extend({"category": key, "value": item} for item in value)
    return rows


def closeout_safety_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    flags = payload.get("safety_flags", {})
    if not isinstance(flags, Mapping):
        return []
    return [{"flag": key, "value": value} for key, value in sorted(flags.items())]


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _render_table(
    ui: Any,
    rows: list[dict[str, Any]],
    columns: list[str],
    status_columns: list[str],
    empty_message: str,
) -> None:
    ui.markdown(
        render_html_table(
            rows,
            columns=columns,
            status_columns=status_columns,
            empty_message=empty_message,
        ),
        unsafe_allow_html=True,
    )
