
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smartcrypto.dashboard.ui.tables import render_html_table

SUMMARY_COLUMNS = [
    "status",
    "closeout_ready",
    "open_blockers_total",
    "chain_rows_total",
    "chain_ok_total",
    "chain_warning_total",
    "chain_blocked_total",
]

CHAIN_COLUMNS = [
    "chain_id",
    "title",
    "stage_status",
    "governance_state",
    "blockers_count",
    "warnings_count",
    "operator_action",
    "closeout_condition",
]


def render_runtime_freshness_governance_closeout_index(
    snapshot: Mapping[str, Any],
    *,
    ui: Any,
) -> None:
    payload = runtime_freshness_governance_closeout_index_view(snapshot)
    ui.subheader("Runtime Freshness: Governance Closeout Index")
    if not payload:
        ui.info("UNKNOWN: índice de governança/freshness ausente no snapshot.")
        return

    ui.warning(
        "Painel estritamente read-only. Consolida a cadeia de governança; "
        "não executa producers, não altera blockers e não libera live/canary/ordens."
    )
    _render_table(
        ui,
        [governance_index_summary_row(payload)],
        SUMMARY_COLUMNS,
        ["status"],
        "Resumo do índice ausente.",
    )
    ui.markdown("#### Cadeia de governança")
    _render_table(
        ui,
        governance_chain_rows(payload),
        CHAIN_COLUMNS,
        ["stage_status", "governance_state"],
        "Nenhum estágio de governança materializado.",
    )
    _render_text_list(ui, "Blockers abertos", payload.get("open_blockers"))
    _render_text_list(ui, "Próximas ações manuais", payload.get("manual_next_actions"))
    _render_text_list(ui, "Critérios de closeout", payload.get("closeout_criteria"))
    _render_text_list(ui, "Ações proibidas", payload.get("forbidden_actions"))


def runtime_freshness_governance_closeout_index_view(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    direct = snapshot.get("runtime_freshness_governance_closeout_index")
    if isinstance(direct, Mapping):
        return dict(direct)
    sections = snapshot.get("sections", {})
    if not isinstance(sections, Mapping):
        return {}
    section = sections.get("runtime_freshness_governance_closeout_index", {})
    if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
        return dict(section["data"])
    return {}


def governance_index_summary_row(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(payload.get("status", "blocked")).upper(),
        "closeout_ready": payload.get("closeout_ready", False),
        "open_blockers_total": payload.get("open_blockers_total", 0),
        "chain_rows_total": payload.get("chain_rows_total", 0),
        "chain_ok_total": payload.get("chain_ok_total", 0),
        "chain_warning_total": payload.get("chain_warning_total", 0),
        "chain_blocked_total": payload.get("chain_blocked_total", 0),
    }


def governance_chain_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("governance_chain_rows")
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
