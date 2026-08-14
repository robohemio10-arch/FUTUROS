from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import get_streamlit, render_snapshot_page
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.dashboard.ui import (
    inject_smart_futuros_command_center_css,
    render_blocked_action_card,
    render_chart_placeholder,
    render_compact_kpi,
    render_footer_audit_bar,
    render_global_topbar,
    render_html_table,
    render_page_title,
    render_readonly_banner,
    render_section_panel,
    render_sidebar,
    render_status_card,
    status_to_label,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "04. Oportunidades"
PAGE_NUMBER = "04"
PAGE_NAME = "Oportunidades"
PAGE_SUBTITLE = (
    "Scanner institucional snapshot-first; arbitragem, sniper e envio de ordens "
    "permanecem hard-blocked."
)
ACTIVE_PAGE = "04_opportunity_scanner"
SNAPSHOT_PATH = "data/reports/dashboard_opportunity_scanner_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_opportunity_scanner_snapshot_v1"
REQUIRED_SECTIONS = (
    "status",
    "spread_scanner",
    "triangular_arbitrage",
    "order_flow_imbalance",
    "launch_radar",
    "opportunity_ranking",
    "events",
    "governance",
    "audit",
)

_RANKING_COLUMNS = (
    "Origem",
    "Ativo / rota",
    "Score",
    "Edge",
    "PnL proj.",
    "Status",
)
_EVENT_COLUMNS = ("Timestamp", "Tipo", "Ativo", "Mensagem", "Status")


def render_page(snapshot: dict[str, Any], *, ui: Any | None = None) -> None:
    """Render Aba 04 strictly from the canonical read-only opportunity snapshot."""

    target_ui = ui or get_streamlit()
    inject_smart_futuros_command_center_css(ui=target_ui)
    render_global_topbar(
        last_updated=_text_or_none(snapshot.get("last_updated_utc")),
        ui=target_ui,
    )
    render_sidebar(
        ACTIVE_PAGE,
        {"environment": "shadow", "snapshot": SNAPSHOT_PATH},
        ui=target_ui,
    )
    render_page_title(PAGE_NUMBER, PAGE_NAME, PAGE_SUBTITLE, ui=target_ui)
    render_readonly_banner(ui=target_ui)

    sections = _sections(snapshot)
    status_section = _mapping(sections.get("status"))
    spread = _mapping(sections.get("spread_scanner"))
    triangular = _mapping(sections.get("triangular_arbitrage"))
    order_flow = _mapping(sections.get("order_flow_imbalance"))
    launch_radar = _mapping(sections.get("launch_radar"))
    ranking_section = _mapping(sections.get("opportunity_ranking"))
    events_section = _mapping(sections.get("events"))
    governance = _mapping(sections.get("governance"))
    audit = _mapping(sections.get("audit"))

    spread_rows = _rows(spread.get("opportunities"))
    triangular_rows = _rows(triangular.get("opportunities"))
    order_flow_rows = _rows(order_flow.get("observations"))
    launch_rows = _rows(launch_radar.get("observations"))
    ranking_rows = _rows(ranking_section.get("ranking"))

    _render_primary_kpi_grid(
        _primary_kpi_cards(
            status_section=status_section,
            spread=spread,
            spread_rows=spread_rows,
            triangular=triangular,
            triangular_rows=triangular_rows,
            order_flow=order_flow,
            order_flow_rows=order_flow_rows,
            launch_radar=launch_radar,
            launch_rows=launch_rows,
            ranking_section=ranking_section,
            ranking_rows=ranking_rows,
            governance=governance,
        ),
        ui=target_ui,
    )

    ranking_status = _ranking_status(ranking_section, ranking_rows)
    target_ui.markdown(
        render_section_panel(
            "Opportunity Ranking",
            _ranking_body(
                ranking_rows=ranking_rows,
                spread_rows=spread_rows,
                triangular_rows=triangular_rows,
                order_flow_rows=order_flow_rows,
                launch_rows=launch_rows,
            ),
            subtitle=(
                "Ranking somente leitura. Nenhuma oportunidade ausente é sintetizada "
                "e nenhuma seleção possui autoridade operacional."
            ),
            status=ranking_status,
        ),
        unsafe_allow_html=True,
    )

    left_column, right_column = target_ui.columns(2)
    left_column.markdown(
        render_section_panel(
            "Scanner de Spread",
            _scanner_body(
                rows=spread_rows,
                source="SPREAD",
                empty_message=(
                    "Fonte opportunity_spread_scanner_snapshot não materializada · UNKNOWN"
                ),
            ),
            subtitle="Spreads observados no snapshot; sem execução multi-exchange.",
            status=_section_status(spread),
        ),
        unsafe_allow_html=True,
    )
    right_column.markdown(
        render_section_panel(
            "Arbitragem Triangular",
            _scanner_body(
                rows=triangular_rows,
                source="TRIANGULAR",
                empty_message=(
                    "Fonte triangular_arbitrage_snapshot não materializada · UNKNOWN"
                ),
            ),
            subtitle="Rotas observadas; arbitragem real permanece HARD_BLOCKED.",
            status=_section_status(triangular),
        ),
        unsafe_allow_html=True,
    )

    flow_column, launch_column = target_ui.columns(2)
    flow_column.markdown(
        render_section_panel(
            "Order Flow Imbalance",
            _scanner_body(
                rows=order_flow_rows,
                source="ORDER_FLOW",
                empty_message=(
                    "Fonte order_flow_imbalance_snapshot não materializada · UNKNOWN"
                ),
            ),
            subtitle="Pressão bid/ask e OFI somente quando materializados.",
            status=_section_status(order_flow),
        ),
        unsafe_allow_html=True,
    )
    launch_column.markdown(
        render_section_panel(
            "Launch Radar",
            _scanner_body(
                rows=launch_rows,
                source="LAUNCH",
                empty_message="Fonte launch_radar_snapshot não materializada · UNKNOWN",
            ),
            subtitle="Radar observacional; sniper real permanece HARD_BLOCKED.",
            status=_section_status(launch_radar),
        ),
        unsafe_allow_html=True,
    )

    governance_column, events_column = target_ui.columns(2)
    governance_column.markdown(
        render_section_panel(
            "Execution Boundary",
            _governance_body(governance),
            subtitle=(
                "Dashboard observa, mas não arma sniper, não executa arbitragem "
                "e não envia ordens."
            ),
            status=_governance_status(governance),
        ),
        unsafe_allow_html=True,
    )
    events_column.markdown(
        render_section_panel(
            "Eventos Financeiros",
            render_html_table(
                _event_rows(events_section),
                columns=list(_EVENT_COLUMNS),
                status_columns=["Status"],
                empty_message="Nenhum evento materializado no snapshot · UNKNOWN",
            ),
            subtitle="Últimos eventos disponíveis no snapshot canônico.",
            status=_section_status(events_section),
        ),
        unsafe_allow_html=True,
    )

    target_ui.markdown(
        render_section_panel(
            "Diagnóstico do Snapshot",
            (
                render_html_table(
                    _diagnostic_rows(snapshot, audit),
                    columns=["Campo", "Valor"],
                    empty_message="Diagnóstico indisponível · UNKNOWN",
                )
                + render_html_table(
                    _safety_rows(snapshot, governance),
                    columns=["Controle", "Valor"],
                    empty_message="Safety contract indisponível · UNKNOWN",
                )
            ),
            subtitle="Contrato, cobertura de fontes e invariantes de segurança da Aba 04.",
            status=_snapshot_status(snapshot),
        ),
        unsafe_allow_html=True,
    )

    _render_canonical_snapshot_details(snapshot, ui=target_ui)
    render_footer_audit_bar(SNAPSHOT_PATH, ui=target_ui)


def _render_canonical_snapshot_details(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    """Preserve the shared read-only contract behind collapsed audit details."""

    with ui.expander(
        "Detalhamento canônico da Aba 04 · snapshot-first/read-only",
        expanded=False,
    ):
        ui.title(PAGE_TITLE)
        render_snapshot_page(
            title=PAGE_TITLE,
            snapshot_path=SNAPSHOT_PATH,
            snapshot=snapshot,
            section_order=REQUIRED_SECTIONS,
            ui=ui,
            render_chrome=False,
        )


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(
        load_page_snapshot(
            DashboardPageId.opportunity_scanner,
            project_root=project_root,
        ),
        ui=ui,
    )


def _sections(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(snapshot.get("sections"))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _section_status(section: Mapping[str, Any]) -> str:
    return str(section.get("status") or "UNKNOWN")


def _snapshot_status(snapshot: Mapping[str, Any]) -> str:
    summary = _mapping(snapshot.get("status_summary"))
    return str(summary.get("status") or snapshot.get("status") or "UNKNOWN")


def _text_or_none(value: Any) -> str | None:
    return None if value in (None, "") else str(value)



def _render_primary_kpi_grid(cards: Sequence[str], *, ui: Any) -> None:
    """Render six KPI cards as two balanced three-column desktop rows."""

    materialized = list(cards)
    for offset in range(0, len(materialized), 3):
        columns = ui.columns(3)
        for column, card in zip(columns, materialized[offset : offset + 3]):
            column.markdown(card, unsafe_allow_html=True)


def _primary_kpi_cards(
    *,
    status_section: Mapping[str, Any],
    spread: Mapping[str, Any],
    spread_rows: list[dict[str, Any]],
    triangular: Mapping[str, Any],
    triangular_rows: list[dict[str, Any]],
    order_flow: Mapping[str, Any],
    order_flow_rows: list[dict[str, Any]],
    launch_radar: Mapping[str, Any],
    launch_rows: list[dict[str, Any]],
    ranking_section: Mapping[str, Any],
    ranking_rows: list[dict[str, Any]],
    governance: Mapping[str, Any],
) -> list[str]:
    observed_count = _observed_opportunity_count(
        status_section,
        spread=spread,
        spread_rows=spread_rows,
        triangular=triangular,
        triangular_rows=triangular_rows,
        order_flow=order_flow,
        order_flow_rows=order_flow_rows,
        launch_radar=launch_radar,
        launch_rows=launch_rows,
    )
    ranking_count = _materialized_count(ranking_section, "ranking", ranking_rows)
    execution_status = _governance_status(governance)
    return [
        render_compact_kpi(
            "Oportunidades observadas",
            _format_count(observed_count),
            helper="Somente fontes materializadas",
            status=_section_status(status_section),
        ),
        render_compact_kpi(
            "Ranking materializado",
            _format_count(ranking_count),
            helper="Entradas no ranking canônico",
            status=_ranking_status(ranking_section, ranking_rows),
        ),
        render_compact_kpi(
            "Melhor score",
            _format_score(_best_score(ranking_rows)),
            helper="Maior score observado",
            status=_ranking_status(ranking_section, ranking_rows),
        ),
        render_compact_kpi(
            "Spread candidates",
            _format_count(_materialized_count(spread, "opportunities", spread_rows)),
            helper="Scanner especializado",
            status=_section_status(spread),
        ),
        render_compact_kpi(
            "Triangular candidates",
            _format_count(
                _materialized_count(triangular, "opportunities", triangular_rows)
            ),
            helper="Rotas especializadas",
            status=_section_status(triangular),
        ),
        render_compact_kpi(
            "Execução real",
            status_to_label(execution_status),
            helper="Arbitragem / sniper / ordens",
            status=execution_status,
        ),
    ]


def _observed_opportunity_count(
    status_section: Mapping[str, Any],
    *,
    spread: Mapping[str, Any],
    spread_rows: list[dict[str, Any]],
    triangular: Mapping[str, Any],
    triangular_rows: list[dict[str, Any]],
    order_flow: Mapping[str, Any],
    order_flow_rows: list[dict[str, Any]],
    launch_radar: Mapping[str, Any],
    launch_rows: list[dict[str, Any]],
) -> int | None:
    explicit = _non_negative_int(status_section.get("opportunity_count"))
    if explicit is not None and _section_status(status_section).upper() != "UNKNOWN":
        return explicit

    counts = (
        _materialized_count(spread, "opportunities", spread_rows),
        _materialized_count(triangular, "opportunities", triangular_rows),
        _materialized_count(order_flow, "observations", order_flow_rows),
        _materialized_count(launch_radar, "observations", launch_rows),
    )
    if all(value is None for value in counts):
        return None
    return sum(value or 0 for value in counts)


def _materialized_count(
    section: Mapping[str, Any],
    key: str,
    rows: list[dict[str, Any]],
) -> int | None:
    if rows:
        return len(rows)
    if key not in section:
        return None
    if _section_status(section).upper() in {"UNKNOWN", "MISSING", "UNAVAILABLE"}:
        return None
    raw = section.get(key)
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return 0
    return None


def _ranking_status(section: Mapping[str, Any], rows: list[dict[str, Any]]) -> str:
    if rows:
        return _section_status(section)
    if "ranking" not in section:
        return "UNKNOWN"
    if _section_status(section).upper() in {"UNKNOWN", "MISSING", "UNAVAILABLE"}:
        return "UNKNOWN"
    return _section_status(section)


def _ranking_body(
    *,
    ranking_rows: list[dict[str, Any]],
    spread_rows: list[dict[str, Any]],
    triangular_rows: list[dict[str, Any]],
    order_flow_rows: list[dict[str, Any]],
    launch_rows: list[dict[str, Any]],
) -> str:
    if not ranking_rows:
        message = (
            "Ranking ainda não materializado · UNKNOWN"
            if _materialized_source_count(
                spread_rows,
                triangular_rows,
                order_flow_rows,
                launch_rows,
            )
            else "Fontes especializadas não materializadas · UNKNOWN"
        )
        return render_chart_placeholder("Opportunity Ranking", message, status="UNKNOWN")
    return render_html_table(
        _candidate_rows(ranking_rows, source="RANKING"),
        columns=list(_RANKING_COLUMNS),
        status_columns=["Status"],
        empty_message="Ranking não materializado · UNKNOWN",
    )


def _scanner_body(
    *,
    rows: list[dict[str, Any]],
    source: str,
    empty_message: str,
) -> str:
    if not rows:
        return render_chart_placeholder(
            source.replace("_", " ").title(),
            empty_message,
            status="UNKNOWN",
        )
    return render_html_table(
        _candidate_rows(rows, source=source),
        columns=list(_RANKING_COLUMNS),
        status_columns=["Status"],
        empty_message=empty_message,
    )


def _candidate_rows(rows: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
    return [_candidate_row(row, source=source) for row in rows[:50]]


def _candidate_row(row: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    asset_or_route = _first_text(
        row.get("symbol"),
        row.get("pair"),
        row.get("route"),
        row.get("path"),
        row.get("asset"),
        row.get("token"),
        row.get("market"),
        _exchange_route(row),
    )
    score = _first_not_none(row.get("opportunity_score"), row.get("score"))
    edge = _first_not_none(
        row.get("spread_net_pct"),
        row.get("spread_bps"),
        row.get("edge_pct"),
        row.get("triangular_return_pct"),
        row.get("ofi_score"),
        row.get("expected_return_pct"),
    )
    projected_profit = _first_not_none(
        row.get("projected_net_profit_usdt"),
        row.get("triangular_net_profit_usdt"),
        row.get("expected_profit_usdt"),
        row.get("net_profit_usdt"),
    )
    return {
        "Origem": _first_text(
            row.get("source"),
            row.get("opportunity_type"),
            row.get("type"),
            source,
        ),
        "Ativo / rota": asset_or_route,
        "Score": _format_score(score),
        "Edge": _format_edge(edge, row),
        "PnL proj.": _format_usdt(projected_profit),
        "Status": _first_text(
            row.get("status"),
            row.get("decision"),
            row.get("state"),
            "UNKNOWN",
        ),
    }


def _event_rows(section: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _rows(section.get("events"))[-20:]:
        output.append(
            {
                "Timestamp": _first_text(
                    row.get("timestamp"),
                    row.get("event_time"),
                    row.get("created_at"),
                    row.get("time"),
                ),
                "Tipo": _first_text(
                    row.get("event_type"),
                    row.get("type"),
                    row.get("category"),
                ),
                "Ativo": _first_text(row.get("symbol"), row.get("asset"), row.get("pair")),
                "Mensagem": _first_text(
                    row.get("message"),
                    row.get("reason"),
                    row.get("description"),
                    row.get("decision"),
                ),
                "Status": _first_text(row.get("status"), row.get("severity"), "UNKNOWN"),
            }
        )
    return output


def _governance_body(governance: Mapping[str, Any]) -> str:
    if not governance:
        return render_chart_placeholder(
            "Execution Boundary",
            "Governança não materializada · UNKNOWN",
            status="UNKNOWN",
        )

    rows = [
        {"Controle": "Opportunity Scanner", "Valor": _display_text(governance.get("opportunity_scanner"))},
        {"Controle": "Real Arbitrage", "Valor": _display_text(governance.get("real_arbitrage"))},
        {
            "Controle": "Real Sniper",
            "Valor": _display_text(
                _first_not_none(governance.get("sniper_real"), governance.get("real_sniper"))
            ),
        },
        {"Controle": "Multi-exchange Live", "Valor": _display_text(governance.get("multi_exchange_live"))},
        {"Controle": "Dashboard Can Send Order", "Valor": _format_bool(governance.get("dashboard_can_send_order"))},
        {"Controle": "Dashboard Can Arm Sniper", "Valor": _format_bool(governance.get("dashboard_can_arm_sniper"))},
    ]
    status = _governance_status(governance)
    return (
        render_status_card(
            "Autoridade operacional",
            status,
            description="Nenhuma autoridade de execução é concedida ao dashboard.",
            size="sm",
        )
        + render_html_table(
            rows,
            columns=["Controle", "Valor"],
            empty_message="Governança indisponível · UNKNOWN",
        )
        + render_blocked_action_card(
            "Execução real bloqueada",
            (
                "Aba 04 é observacional. Arbitragem, sniper, multi-exchange live "
                "e envio de ordem não são ações disponíveis no dashboard."
            ),
            status="HARD_BLOCKED",
        )
    )


def _governance_status(governance: Mapping[str, Any]) -> str:
    if not governance:
        return "UNKNOWN"
    if governance.get("dashboard_can_send_order") is True:
        return "HARD_BLOCKED"
    if governance.get("dashboard_can_arm_sniper") is True:
        return "HARD_BLOCKED"
    blocked_values = (
        governance.get("real_arbitrage"),
        _first_not_none(governance.get("sniper_real"), governance.get("real_sniper")),
        governance.get("multi_exchange_live"),
    )
    if any(str(value or "").upper() == "HARD_BLOCKED" for value in blocked_values):
        return "HARD_BLOCKED"
    if any(str(value or "").upper() == "BLOCKED" for value in blocked_values):
        return "BLOCKED"
    return _section_status(governance)


def _diagnostic_rows(snapshot: Mapping[str, Any], audit: Mapping[str, Any]) -> list[dict[str, Any]]:
    summary = _mapping(snapshot.get("status_summary"))
    source_health = _mapping(snapshot.get("source_health"))
    return [
        {"Campo": "schema_version", "Valor": _display_text(snapshot.get("schema_version"))},
        {"Campo": "runtime_mode", "Valor": _display_text(snapshot.get("runtime_mode"))},
        {"Campo": "status", "Valor": _snapshot_status(snapshot)},
        {"Campo": "last_updated_utc", "Valor": _display_text(snapshot.get("last_updated_utc"))},
        {
            "Campo": "missing_required_sources",
            "Valor": _display_collection(
                _first_not_none(
                    summary.get("missing_required_sources"),
                    snapshot.get("missing_required_sources"),
                )
            ),
        },
        {
            "Campo": "missing_optional_sources",
            "Valor": _display_collection(
                _first_not_none(
                    summary.get("missing_optional_sources"),
                    snapshot.get("missing_optional_sources"),
                )
            ),
        },
        {
            "Campo": "future_sources_pending",
            "Valor": _display_collection(
                _first_not_none(
                    summary.get("future_sources_pending"),
                    snapshot.get("future_sources_pending"),
                )
            ),
        },
        {
            "Campo": "source_health",
            "Valor": _display_text(
                _first_not_none(source_health.get("status"), snapshot.get("source_health_status"))
            ),
        },
        {
            "Campo": "audit.dashboard_reads_only",
            "Valor": _format_bool(
                _first_not_none(audit.get("dashboard_reads_only"), snapshot.get("dashboard_readonly"))
            ),
        },
    ]


def _safety_rows(snapshot: Mapping[str, Any], governance: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"Controle": "dashboard_readonly", "Valor": _format_bool(snapshot.get("dashboard_readonly"))},
        {"Controle": "paper_only", "Valor": _format_bool(snapshot.get("paper_only"))},
        {"Controle": "shadow_only", "Valor": _format_bool(snapshot.get("shadow_only"))},
        {"Controle": "live_locked", "Valor": _format_bool(snapshot.get("live_locked"))},
        {"Controle": "order_submission_enabled", "Valor": _format_bool(snapshot.get("order_submission_enabled"))},
        {
            "Controle": "real_order_submission_enabled",
            "Valor": _format_bool(snapshot.get("real_order_submission_enabled")),
        },
        {
            "Controle": "dashboard_can_send_order",
            "Valor": _format_bool(governance.get("dashboard_can_send_order")),
        },
        {
            "Controle": "dashboard_can_arm_sniper",
            "Valor": _format_bool(governance.get("dashboard_can_arm_sniper")),
        },
    ]


def _materialized_source_count(*groups: list[dict[str, Any]]) -> int:
    return sum(1 for rows in groups if rows)


def _materialized_source_summary(*groups: list[dict[str, Any]]) -> str:
    materialized = _materialized_source_count(*groups)
    return "UNKNOWN" if materialized == 0 else f"{materialized}/4"


def _best_score(rows: list[dict[str, Any]]) -> float | None:
    scores = [
        value
        for row in rows
        for value in [_finite_float(_first_not_none(row.get("opportunity_score"), row.get("score")))]
        if value is not None
    ]
    return max(scores) if scores else None


def _exchange_route(row: Mapping[str, Any]) -> str | None:
    left = _first_text(
        row.get("exchange_a"),
        row.get("buy_exchange"),
        row.get("source_exchange"),
    )
    right = _first_text(
        row.get("exchange_b"),
        row.get("sell_exchange"),
        row.get("target_exchange"),
    )
    if left == "UNKNOWN" and right == "UNKNOWN":
        return None
    return f"{left} → {right}"


def _format_edge(value: Any, row: Mapping[str, Any]) -> str:
    number = _finite_float(value)
    if number is None:
        return "UNKNOWN"
    if "spread_bps" in row and _first_not_none(row.get("spread_net_pct"), row.get("edge_pct")) is None:
        return f"{number:.3f} bps"
    return f"{number:.4f}%"


def _format_score(value: Any) -> str:
    number = _finite_float(value)
    return "UNKNOWN" if number is None else f"{number:.4f}"


def _format_usdt(value: Any) -> str:
    number = _finite_float(value)
    return "UNKNOWN" if number is None else f"{number:,.2f} USDT"


def _format_count(value: Any) -> str:
    count = _non_negative_int(value)
    return "UNKNOWN" if count is None else str(count)


def _format_bool(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "UNKNOWN"


def _display_text(value: Any) -> str:
    if value in (None, ""):
        return "UNKNOWN"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _display_collection(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        materialized = [str(item) for item in value if item not in (None, "")]
        return ", ".join(materialized) if materialized else "0"
    return _display_text(value)


def _first_text(*values: Any) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return "UNKNOWN"


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        result = int(value)
        return result if result >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


if __name__ == "__main__":
    main()
