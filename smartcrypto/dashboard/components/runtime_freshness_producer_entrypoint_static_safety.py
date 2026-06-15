from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smartcrypto.dashboard.ui.tables import render_html_table


SUMMARY_COLUMNS = [
    "status",
    "entrypoints_total",
    "entrypoints_ok_total",
    "entrypoints_warning_total",
    "entrypoints_blocked_total",
    "manual_execution_only",
]

ENTRYPOINT_COLUMNS = [
    "producer_id",
    "domain",
    "entrypoint_path",
    "exists",
    "parseable_python",
    "cli_compatible",
    "expected_cli_flags_present",
    "missing_cli_flags",
    "expected_output_path",
    "output_path_supported",
    "status",
    "reason",
    "execution_allowed",
    "safe_to_execute_from_dashboard",
]

FINDING_COLUMNS = ["category", "finding"]


def render_runtime_freshness_producer_entrypoint_static_safety(
    snapshot: Mapping[str, Any],
    *,
    ui: Any,
) -> None:
    payload = runtime_freshness_producer_entrypoint_static_safety_view(snapshot)
    ui.subheader("Runtime Freshness: Entrypoint Static Safety")
    if not payload:
        ui.info("UNKNOWN: auditoria estatica de entrypoints ausente no snapshot.")
        return

    ui.warning(
        "Auditoria read-only por AST/texto. Este painel nao executa producers, "
        "nao autoriza live/canary/ordens e nao altera blockers."
    )
    _render_table(
        ui,
        [entrypoint_static_safety_summary_row(payload)],
        SUMMARY_COLUMNS,
        ["status"],
        "Resumo de auditoria estatica ausente.",
    )
    ui.markdown("#### Entry points auditados")
    _render_table(
        ui,
        entrypoint_static_safety_rows(payload),
        ENTRYPOINT_COLUMNS,
        ["status"],
        "Nenhum entrypoint auditado.",
    )
    ui.markdown("#### Findings criticos")
    _render_table(
        ui,
        entrypoint_static_safety_finding_rows(payload),
        FINDING_COLUMNS,
        [],
        "Nenhum finding critico materializado.",
    )
    _render_text_list(ui, "Acoes proibidas", payload.get("forbidden_actions"))


def runtime_freshness_producer_entrypoint_static_safety_view(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    direct = snapshot.get("runtime_freshness_producer_entrypoint_static_safety")
    if isinstance(direct, Mapping):
        return dict(direct)
    sections = snapshot.get("sections", {})
    if not isinstance(sections, Mapping):
        return {}
    section = sections.get("runtime_freshness_producer_entrypoint_static_safety", {})
    if isinstance(section, Mapping) and isinstance(section.get("data"), Mapping):
        return dict(section["data"])
    return {}


def entrypoint_static_safety_summary_row(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "status": str(payload.get("status", "blocked")).upper(),
        "entrypoints_total": payload.get("entrypoints_total", 0),
        "entrypoints_ok_total": payload.get("entrypoints_ok_total", 0),
        "entrypoints_warning_total": payload.get("entrypoints_warning_total", 0),
        "entrypoints_blocked_total": payload.get("entrypoints_blocked_total", 0),
        "manual_execution_only": payload.get("manual_execution_only", True),
    }


def entrypoint_static_safety_rows(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return _mapping_rows(payload.get("entrypoint_rows"))


def entrypoint_static_safety_finding_rows(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for category in (
        "missing_entrypoints",
        "missing_cli_flags",
        "forbidden_findings",
        "unsafe_write_findings",
        "network_findings",
        "subprocess_findings",
        "private_exchange_findings",
    ):
        value = payload.get(category)
        if isinstance(value, list):
            rows.extend(
                {"category": category, "finding": str(item)}
                for item in value
                if item
            )
    return rows


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
