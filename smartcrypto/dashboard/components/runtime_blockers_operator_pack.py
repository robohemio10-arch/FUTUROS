from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smartcrypto.dashboard.ui.tables import render_html_table


SUMMARY_COLUMNS = [
    "operator_pack_status",
    "dashboard_status",
    "global_source_health_status",
    "runtime_evidence_integration_status",
    "blockers_total",
    "critical_blockers_total",
    "domains_total",
]

GROUP_COLUMNS = [
    "domain",
    "severity",
    "blockers_count",
    "blocker_ids",
    "canonical_paths",
    "operator_summary",
    "closeout_condition",
]

CHECKLIST_COLUMNS = [
    "step",
    "check_id",
    "domain",
    "severity",
    "title",
    "manual_action",
    "producer_hint",
    "expected_artifact",
    "verification_command",
    "closeout_condition",
]

SEQUENCE_COLUMNS = [
    "sequence",
    "sequence_id",
    "title",
    "check_ids",
    "command_hint",
    "execution_location",
]

EVIDENCE_COLUMNS = ["check_id", "domain", "expected_artifact", "expected_state"]
CLOSEOUT_COLUMNS = ["criterion_id", "condition", "automatic_release"]


def render_runtime_blockers_operator_pack(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    payload = runtime_blockers_operator_pack_view(snapshot)
    ui.subheader("Runtime Blockers: Operator Pack")
    if not payload:
        ui.info("UNKNOWN: operator pack ausente no snapshot.")
        return

    ui.warning(
        "Operator pack estritamente read-only. Checklist, comandos e sequencia sao "
        "orientacao para execucao manual fora do dashboard."
    )
    _render_table(ui, [operator_pack_summary_row(payload)], SUMMARY_COLUMNS, ["operator_pack_status"])
    ui.markdown("#### Grupos por dominio e severidade")
    _render_table(ui, operator_group_rows(payload), GROUP_COLUMNS, ["severity"])
    ui.markdown("#### Checklist manual")
    _render_table(ui, operator_checklist_rows(payload), CHECKLIST_COLUMNS, ["severity"])
    ui.markdown("#### Sequencia externa recomendada")
    _render_table(ui, external_sequence_rows(payload), SEQUENCE_COLUMNS, [])
    ui.markdown("#### Evidencia esperada pos-remediacao")
    _render_table(ui, expected_evidence_rows(payload), EVIDENCE_COLUMNS, [])
    ui.markdown("#### Criterios de fechamento")
    _render_table(ui, closeout_criteria_rows(payload), CLOSEOUT_COLUMNS, [])

    forbidden = payload.get("forbidden_actions", [])
    if isinstance(forbidden, list) and forbidden:
        ui.markdown("#### Acoes proibidas")
        ui.markdown("\n".join(f"- {item}" for item in forbidden))


def runtime_blockers_operator_pack_view(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    direct = snapshot.get("runtime_blockers_operator_pack")
    if isinstance(direct, Mapping):
        return dict(direct)
    sections = snapshot.get("sections", {})
    if not isinstance(sections, Mapping):
        return {}
    section = sections.get("runtime_blockers_operator_pack", {})
    if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
        return dict(section["data"])
    return {}


def operator_pack_summary_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "operator_pack_status": str(payload.get("operator_pack_status", "blocked")).upper(),
        "dashboard_status": payload.get("dashboard_status", "UNKNOWN"),
        "global_source_health_status": payload.get("global_source_health_status", "UNKNOWN"),
        "runtime_evidence_integration_status": payload.get(
            "runtime_evidence_integration_status", "UNKNOWN"
        ),
        "blockers_total": payload.get("blockers_total", 0),
        "critical_blockers_total": payload.get("critical_blockers_total", 0),
        "domains_total": payload.get("domains_total", 0),
    }


def operator_group_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("operator_groups"))


def operator_checklist_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("operator_checklist"))


def external_sequence_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("external_execution_sequence"))


def expected_evidence_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("expected_post_remediation_evidence"))


def closeout_criteria_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("closeout_criteria"))


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _render_table(
    ui: Any,
    rows: list[dict[str, Any]],
    columns: list[str],
    status_columns: list[str],
) -> None:
    ui.markdown(
        render_html_table(
            rows,
            columns=columns,
            status_columns=status_columns,
            empty_message="Nenhum dado mapeado.",
        ),
        unsafe_allow_html=True,
    )
