from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smartcrypto.dashboard.ui.tables import render_html_table


SUMMARY_COLUMNS = [
    "status",
    "contracts_total",
    "contracts_ready_total",
    "contracts_blocked_total",
    "manual_closeout_allowed",
]

CONTRACT_COLUMNS = [
    "contract_id",
    "producer_id",
    "domain",
    "current_status",
    "current_freshness_status",
    "current_health_status",
    "entry_criteria",
    "manual_execution_hint",
    "expected_artifact_path",
    "expected_timestamp_field",
    "max_acceptable_age_seconds_after_refresh",
    "verification_commands",
    "manual_closeout_condition",
    "rollback_or_abort_condition",
]

ARTIFACT_COLUMNS = [
    "contract_id",
    "path",
    "expected_schema_version",
    "expected_timestamp_field",
]

CLOSEOUT_COLUMNS = ["contract_id", "condition", "automatic_release"]


def render_runtime_freshness_producer_contracts(
    snapshot: Mapping[str, Any],
    *,
    ui: Any,
) -> None:
    payload = runtime_freshness_producer_contracts_view(snapshot)
    ui.subheader("Runtime Freshness: Producer Contracts & Manual Closeout")
    if not payload:
        ui.info("UNKNOWN: contratos de producers de freshness ausentes no snapshot.")
        return

    ui.warning(
        "Painel estritamente read-only. Hints e comandos sao texto para execucao "
        "manual externa; nenhum closeout libera live, canary ou ordens."
    )
    _render_table(
        ui,
        [producer_contract_summary_row(payload)],
        SUMMARY_COLUMNS,
        ["status"],
        "Resumo de contratos ausente.",
    )
    ui.markdown("#### Contratos por producer")
    _render_table(
        ui,
        producer_contract_rows(payload),
        CONTRACT_COLUMNS,
        ["current_status", "current_freshness_status", "current_health_status"],
        "Nenhum contrato materializado.",
    )
    ui.markdown("#### Artefatos esperados")
    _render_table(
        ui,
        producer_contract_artifact_rows(payload),
        ARTIFACT_COLUMNS,
        [],
        "Nenhum artefato esperado materializado.",
    )
    ui.markdown("#### Criterios de fechamento manual")
    _render_table(
        ui,
        producer_contract_closeout_rows(payload),
        CLOSEOUT_COLUMNS,
        [],
        "Nenhum criterio de fechamento materializado.",
    )

    _render_text_list(ui, "Pre-checks", payload.get("pre_execution_checks"))
    _render_text_list(ui, "Post-checks", payload.get("post_execution_checks"))
    _render_text_list(ui, "Acoes proibidas", payload.get("forbidden_actions"))


def runtime_freshness_producer_contracts_view(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    direct = snapshot.get("runtime_freshness_producer_contracts")
    if isinstance(direct, Mapping):
        return dict(direct)
    sections = snapshot.get("sections", {})
    if not isinstance(sections, Mapping):
        return {}
    section = sections.get("runtime_freshness_producer_contracts", {})
    if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
        return dict(section["data"])
    return {}


def producer_contract_summary_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(payload.get("status", "blocked")).upper(),
        "contracts_total": payload.get("contracts_total", 0),
        "contracts_ready_total": payload.get("contracts_ready_total", 0),
        "contracts_blocked_total": payload.get("contracts_blocked_total", 0),
        "manual_closeout_allowed": payload.get("manual_closeout_allowed", False),
    }


def producer_contract_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("producer_contracts"))


def producer_contract_artifact_rows(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("required_artifacts"))


def producer_contract_closeout_rows(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("closeout_criteria"))


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _render_text_list(ui: Any, title: str, value: Any) -> None:
    if not isinstance(value, list) or not value:
        return
    ui.markdown(f"#### {title}")
    ui.markdown("\n".join(f"- {item}" for item in value))


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
