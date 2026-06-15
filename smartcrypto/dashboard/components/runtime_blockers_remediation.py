from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smartcrypto.dashboard.ui.tables import render_html_table


SUMMARY_COLUMNS = [
    "status",
    "dashboard_status",
    "global_source_health_status",
    "runtime_evidence_integration_status",
    "blockers_total",
]

BLOCKER_COLUMNS = [
    "blocker_id",
    "domain",
    "severity",
    "status",
    "freshness_status",
    "health_status",
    "age_seconds",
    "canonical_path",
    "operator_summary",
    "remediation_action",
    "producer_hint",
    "requires_manual_operator",
    "execution_allowed",
]


def render_runtime_blockers_remediation(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    payload = runtime_blockers_remediation_view(snapshot)
    ui.subheader("Runtime Blockers: Runbook de Remediacao")
    if not payload:
        ui.info("UNKNOWN: runbook de remediacao ausente no snapshot.")
        return

    ui.warning(
        "Painel estritamente read-only. As acoes abaixo devem ser executadas "
        "manualmente fora do dashboard e nao liberam live, canary ou ordens."
    )
    ui.markdown(
        render_html_table(
            [runtime_blockers_summary_row(payload)],
            columns=SUMMARY_COLUMNS,
            status_columns=["status"],
            empty_message="Resumo de bloqueios ausente.",
        ),
        unsafe_allow_html=True,
    )
    ui.markdown(
        render_html_table(
            runtime_blocker_rows(payload),
            columns=BLOCKER_COLUMNS,
            status_columns=["status"],
            empty_message="Nenhum bloqueio atual mapeado.",
        ),
        unsafe_allow_html=True,
    )


def runtime_blockers_remediation_view(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    direct = snapshot.get("runtime_blockers_remediation")
    if isinstance(direct, Mapping):
        return dict(direct)
    sections = snapshot.get("sections", {})
    if not isinstance(sections, Mapping):
        return {}
    section = sections.get("runtime_blockers_remediation", {})
    if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
        return dict(section["data"])
    return {}


def runtime_blockers_summary_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(payload.get("status", "blocked")).upper(),
        "dashboard_status": payload.get("dashboard_status", "UNKNOWN"),
        "global_source_health_status": payload.get("global_source_health_status", "UNKNOWN"),
        "runtime_evidence_integration_status": payload.get(
            "runtime_evidence_integration_status", "UNKNOWN"
        ),
        "blockers_total": payload.get("blockers_total", 0),
    }


def runtime_blocker_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("blocker_rows", [])
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]
