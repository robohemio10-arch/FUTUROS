from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smartcrypto.dashboard.ui.tables import render_html_table


SUMMARY_COLUMNS = [
    "status",
    "dashboard_status",
    "global_source_health_status",
    "freshness_blockers_total",
    "critical_freshness_blockers_total",
]

PRODUCER_COLUMNS = [
    "producer_id",
    "domain",
    "target_canonical_path",
    "current_status",
    "current_health_status",
    "current_freshness_status",
    "age_seconds",
    "max_age_seconds",
    "effective_timestamp_utc",
    "timestamp_valid",
    "manual_command_hint",
    "verification_command",
    "post_refresh_closeout_condition",
]

PLAN_COLUMNS = [
    "step",
    "producer_id",
    "domain",
    "manual_command_hint",
    "execution_location",
    "execution_allowed",
]

VERIFICATION_COLUMNS = [
    "producer_id",
    "expected_output_path",
    "verification_command",
    "closeout_condition",
]


def render_runtime_evidence_freshness_remediation_producers(
    snapshot: Mapping[str, Any],
    *,
    ui: Any,
) -> None:
    payload = runtime_evidence_freshness_remediation_producers_view(snapshot)
    ui.subheader("Runtime Evidence: Freshness Remediation Producers")
    if not payload:
        ui.info("UNKNOWN: auditoria de producers de freshness ausente no snapshot.")
        return

    ui.warning(
        "Painel estritamente read-only. Comandos sao orientacao textual para "
        "execucao manual fora do dashboard."
    )
    _render_table(
        ui,
        [freshness_producer_summary_row(payload)],
        SUMMARY_COLUMNS,
        ["status"],
        "Resumo de producers ausente.",
    )
    ui.markdown("#### Producers externos requeridos")
    _render_table(
        ui,
        freshness_producer_rows(payload),
        PRODUCER_COLUMNS,
        ["current_status", "current_freshness_status"],
        "Nenhum producer externo requerido.",
    )
    ui.markdown("#### Plano de execucao manual")
    _render_table(
        ui,
        freshness_manual_plan_rows(payload),
        PLAN_COLUMNS,
        [],
        "Nenhum passo manual requerido.",
    )
    ui.markdown("#### Verificacao pos-execucao")
    _render_table(
        ui,
        freshness_verification_rows(payload),
        VERIFICATION_COLUMNS,
        [],
        "Nenhuma verificacao mapeada.",
    )

    forbidden = payload.get("forbidden_actions", [])
    if isinstance(forbidden, list) and forbidden:
        ui.markdown("#### Acoes proibidas")
        ui.markdown("\n".join(f"- {item}" for item in forbidden))


def runtime_evidence_freshness_remediation_producers_view(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    direct = snapshot.get("runtime_evidence_freshness_remediation_producers")
    if isinstance(direct, Mapping):
        return dict(direct)
    sections = snapshot.get("sections", {})
    if not isinstance(sections, Mapping):
        return {}
    section = sections.get("runtime_evidence_freshness_remediation_producers", {})
    if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
        return dict(section["data"])
    return {}


def freshness_producer_summary_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(payload.get("status", "blocked")).upper(),
        "dashboard_status": payload.get("dashboard_status", "UNKNOWN"),
        "global_source_health_status": payload.get(
            "global_source_health_status", "UNKNOWN"
        ),
        "freshness_blockers_total": payload.get("freshness_blockers_total", 0),
        "critical_freshness_blockers_total": payload.get(
            "critical_freshness_blockers_total", 0
        ),
    }


def freshness_producer_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("producer_rows"))


def freshness_manual_plan_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("manual_execution_plan"))


def freshness_verification_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("post_execution_verification"))


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
