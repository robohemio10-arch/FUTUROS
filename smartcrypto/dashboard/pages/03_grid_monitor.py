from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import get_streamlit
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.dashboard.ui import (
    inject_smart_futuros_command_center_css,
    render_card_grid,
    render_chart_placeholder,
    render_compact_kpi,
    render_depth_preview,
    render_footer_audit_bar,
    render_global_topbar,
    render_grid_channel_preview,
    render_html_table,
    render_mini_bar_stack,
    render_page_title,
    render_readonly_banner,
    render_section_panel,
    render_sidebar,
    render_status_card,
    status_to_label,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "03. Grid Spot Monitor"
PAGE_NUMBER = "03"
PAGE_NAME = "Grid Spot Monitor"
PAGE_SUBTITLE = "Integridade, densidade e microestrutura pública do grid em modo read-only."
ACTIVE_PAGE = "03_grid_monitor"
SNAPSHOT_PATH = "data/reports/dashboard_grid_monitor_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_grid_monitor_snapshot_v1"
REQUIRED_SECTIONS = (
    "selected_grid",
    "grid_channel",
    "grid_density",
    "dust",
    "order_book",
    "heatmap",
    "last_executions",
    "grid_summary",
    "integrity",
    "audit",
)


def render_page(snapshot: dict[str, Any], *, ui: Any | None = None) -> None:
    """Render the Grid Monitor strictly from the canonical read-only snapshot."""

    target_ui = ui or get_streamlit()
    inject_smart_futuros_command_center_css(ui=target_ui)
    render_global_topbar(last_updated=_text_or_none(snapshot.get("last_updated_utc")), ui=target_ui)
    render_sidebar(
        ACTIVE_PAGE,
        {"environment": "paper", "snapshot": SNAPSHOT_PATH},
        ui=target_ui,
    )
    render_page_title(PAGE_NUMBER, PAGE_NAME, PAGE_SUBTITLE, ui=target_ui)
    render_readonly_banner(ui=target_ui)

    sections = _sections(snapshot)
    selected_grid = _mapping(sections.get("selected_grid"))
    grid_channel = _mapping(sections.get("grid_channel"))
    grid_density = _mapping(sections.get("grid_density"))
    dust = _mapping(sections.get("dust"))
    order_book = _mapping(sections.get("order_book"))
    heatmap = _mapping(sections.get("heatmap"))
    integrity = _mapping(sections.get("integrity"))

    target_ui.markdown(
        render_card_grid(
            _primary_kpi_cards(
                selected_grid=selected_grid,
                grid_channel=grid_channel,
                grid_density=grid_density,
                integrity=integrity,
            )
        ),
        unsafe_allow_html=True,
    )

    channel_column, depth_column = target_ui.columns((1, 1))
    channel_column.markdown(
        render_section_panel(
            "Canal do Grid",
            render_grid_channel_preview(
                status=_section_status(grid_channel),
                lower_price=grid_channel.get("lower_price"),
                upper_price=grid_channel.get("upper_price"),
                current_price=_first_not_none(
                    grid_channel.get("current_price"),
                    selected_grid.get("current_price"),
                ),
                level_prices=_sequence(grid_channel.get("level_prices")),
                label="Canal de preços materializado",
            ),
            subtitle="Limites, preço atual e níveis provenientes do snapshot canônico.",
            status=_section_status(grid_channel),
        ),
        unsafe_allow_html=True,
    )
    depth_column.markdown(
        render_section_panel(
            "Order Book Público",
            render_depth_preview(
                status=_section_status(order_book),
                bids=_rows(order_book.get("bids")),
                asks=_rows(order_book.get("asks")),
                label="Profundidade pública materializada",
            ),
            subtitle="Somente bids/asks públicos materializados; nenhuma chamada privada é executada.",
            status=_section_status(order_book),
        ),
        unsafe_allow_html=True,
    )

    target_ui.markdown(
        render_section_panel(
            "Microestrutura",
            render_card_grid(_microstructure_cards(order_book)),
            subtitle="Top of book e liquidez calculados exclusivamente sobre dados públicos do snapshot.",
            status=_section_status(order_book),
        ),
        unsafe_allow_html=True,
    )

    distribution_rows = _distribution_rows(heatmap)
    distribution_status = _distribution_status(heatmap, grid_channel)
    distribution_body = _distribution_body_html(
        heatmap=heatmap,
        rows=distribution_rows,
        status=distribution_status,
    )
    target_ui.markdown(
        render_section_panel(
            "Distribuição dos Níveis do Grid",
            distribution_body,
            subtitle=(
                "Histograma espacial de níveis por faixa de preço. "
                "Não representa um heatmap temporal de mercado."
            ),
            status=distribution_status,
        ),
        unsafe_allow_html=True,
    )

    integrity_column, heatmap_column = target_ui.columns((1, 1))
    integrity_status = _section_status(integrity)
    integrity_column.markdown(
        render_section_panel(
            "Integridade do Grid",
            (
                render_status_card(
                    "Estado de integridade",
                    integrity_status,
                    description=_section_reason(integrity),
                    size="sm",
                )
                + render_html_table(
                    _integrity_rows(integrity, grid_density, dust),
                    columns=["Métrica", "Valor"],
                    empty_message="Integridade não materializada · UNKNOWN",
                )
            ),
            subtitle="Duplicidades, níveis ausentes, canal, dust, freshness e kill switch.",
            status=integrity_status,
        ),
        unsafe_allow_html=True,
    )

    heatmap_status = _section_status(heatmap)
    heatmap_column.markdown(
        render_section_panel(
            "Heatmap Temporal",
            render_chart_placeholder(
                "Heatmap temporal",
                _heatmap_message(heatmap),
                status=heatmap_status,
            ),
            subtitle="Sem eixo temporal real, o estado permanece explicitamente UNKNOWN.",
            status=heatmap_status,
        ),
        unsafe_allow_html=True,
    )

    target_ui.markdown(
        render_section_panel(
            "Diagnóstico do Snapshot",
            (
                render_html_table(
                    _diagnostic_rows(snapshot),
                    columns=["Campo", "Valor"],
                    empty_message="Diagnóstico indisponível · UNKNOWN",
                )
                + render_html_table(
                    _safety_rows(snapshot),
                    columns=["Controle", "Valor"],
                    empty_message="Safety contract indisponível · UNKNOWN",
                )
            ),
            subtitle="Contrato, freshness, cobertura de fontes e invariantes de segurança.",
            status=_snapshot_status(snapshot),
        ),
        unsafe_allow_html=True,
    )

    render_footer_audit_bar(SNAPSHOT_PATH, ui=target_ui)


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(
        load_page_snapshot(DashboardPageId.grid_monitor, project_root=project_root),
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


def _sequence(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return list(value)


def _section_status(section: Mapping[str, Any]) -> str:
    return str(section.get("status") or "UNKNOWN")


def _section_reason(section: Mapping[str, Any]) -> str:
    reason = section.get("reason")
    return str(reason) if reason not in (None, "") else "Sem razão adicional no snapshot."


def _snapshot_status(snapshot: Mapping[str, Any]) -> str:
    summary = _mapping(snapshot.get("status_summary"))
    return str(summary.get("status") or snapshot.get("status") or "UNKNOWN")


def _primary_kpi_cards(
    *,
    selected_grid: Mapping[str, Any],
    grid_channel: Mapping[str, Any],
    grid_density: Mapping[str, Any],
    integrity: Mapping[str, Any],
) -> list[str]:
    selected_status = _section_status(selected_grid)
    channel_status = _section_status(grid_channel)
    density_status = _section_status(grid_density)
    integrity_status = _section_status(integrity)
    current_price = _first_not_none(
        selected_grid.get("current_price"),
        grid_channel.get("current_price"),
    )
    return [
        render_compact_kpi(
            "Par",
            _display_text(selected_grid.get("symbol")),
            helper="Grid selecionado",
            status=selected_status,
        ),
        render_compact_kpi(
            "Preço atual",
            _format_price(current_price),
            helper="Snapshot público",
            status=selected_status,
        ),
        render_compact_kpi(
            "Centro do grid",
            _format_price(grid_channel.get("grid_center")),
            helper="Centro geométrico",
            status=channel_status,
        ),
        render_compact_kpi(
            "Faixa do grid",
            _format_price_range(
                grid_channel.get("lower_price"),
                grid_channel.get("upper_price"),
            ),
            helper="Limite inferior → superior",
            status=channel_status,
        ),
        render_compact_kpi(
            "Níveis ativos",
            _format_count(grid_density.get("active_levels")),
            helper=(
                "esperados "
                + _format_count(grid_density.get("expected_levels"))
                + " · ausentes "
                + _format_count(grid_density.get("missing_levels"))
            ),
            status=density_status,
        ),
        render_compact_kpi(
            "Integridade",
            _format_score(integrity.get("grid_integrity_score")),
            helper=status_to_label(integrity_status),
            status=integrity_status,
        ),
    ]


def _microstructure_cards(order_book: Mapping[str, Any]) -> list[str]:
    status = _section_status(order_book)
    bids = _rows(order_book.get("bids"))
    asks = _rows(order_book.get("asks"))
    has_bid = bool(bids)
    has_ask = bool(asks)
    best_bid = order_book.get("best_bid") if has_bid else None
    best_ask = order_book.get("best_ask") if has_ask else None
    spread = _spread(best_bid, best_ask) if has_bid and has_ask else None
    spread_bps = order_book.get("spread_bps") if has_bid and has_ask else None
    imbalance = order_book.get("order_book_imbalance") if has_bid or has_ask else None
    depth = order_book.get("top_of_book_depth_usdt") if has_bid or has_ask else None
    return [
        render_compact_kpi("Best Bid", _format_price(best_bid), status=status),
        render_compact_kpi("Best Ask", _format_price(best_ask), status=status),
        render_compact_kpi(
            "Spread",
            _format_price(spread),
            helper="best ask − best bid",
            status=status,
        ),
        render_compact_kpi("Spread bps", _format_decimal(spread_bps, digits=3), status=status),
        render_compact_kpi(
            "Imbalance",
            _format_decimal(imbalance, digits=4),
            helper="bid depth vs ask depth",
            status=status,
        ),
        render_compact_kpi(
            "Top depth",
            _format_usdt(depth),
            helper=(
                f"bids {len(bids)} · asks {len(asks)}"
                + (" · truncado" if bool(order_book.get("depth_levels_truncated")) else "")
            ),
            status=status,
        ),
    ]


def _distribution_rows(heatmap: Mapping[str, Any]) -> list[dict[str, Any]]:
    if heatmap.get("level_distribution_available") is not True:
        return []

    output: list[dict[str, Any]] = []
    for row in _rows(heatmap.get("level_distribution")):
        output.append(
            {
                "Bucket": _bucket_label(row.get("bucket_index")),
                "Faixa inferior": _format_price(row.get("lower_price")),
                "Faixa superior": _format_price(row.get("upper_price")),
                "Níveis": _format_count(row.get("level_count")),
                "Participação": _format_percent(row.get("level_share_pct")),
            }
        )
    return output


def _distribution_bar_values(heatmap: Mapping[str, Any]) -> dict[str, Any]:
    if heatmap.get("level_distribution_available") is not True:
        return {}

    values: dict[str, Any] = {}
    for sequence_index, row in enumerate(_rows(heatmap.get("level_distribution"))):
        count = _finite_float(row.get("level_count"))
        if count is None or count < 0.0:
            continue
        label = _bucket_label(row.get("bucket_index"), fallback=sequence_index)
        values[label] = count
    return values


def _distribution_body_html(
    *,
    heatmap: Mapping[str, Any],
    rows: list[dict[str, Any]],
    status: str,
) -> str:
    values = _distribution_bar_values(heatmap)
    if not rows or not values:
        return render_chart_placeholder(
            "Distribuição espacial",
            "Buckets de níveis não materializados · UNKNOWN",
            status="UNKNOWN",
        )

    return (
        render_mini_bar_stack(
            values,
            label="Densidade espacial por bucket",
            status=status,
        )
        + render_html_table(
            rows,
            columns=["Bucket", "Faixa inferior", "Faixa superior", "Níveis", "Participação"],
            empty_message="Distribuição espacial indisponível · UNKNOWN",
        )
    )


def _distribution_status(
    heatmap: Mapping[str, Any],
    grid_channel: Mapping[str, Any],
) -> str:
    if heatmap.get("level_distribution_available") is not True:
        return "UNKNOWN"
    return _section_status(grid_channel)


def _integrity_rows(
    integrity: Mapping[str, Any],
    grid_density: Mapping[str, Any],
    dust: Mapping[str, Any],
) -> list[dict[str, Any]]:
    missing_levels = _first_not_none(
        integrity.get("gap_count"),
        grid_density.get("missing_levels"),
    )
    return [
        {"Métrica": "Score", "Valor": _format_score(integrity.get("grid_integrity_score"))},
        {"Métrica": "Ordens duplicadas", "Valor": _format_count(integrity.get("duplicate_orders"))},
        {"Métrica": "Níveis ausentes", "Valor": _format_count(missing_levels)},
        {"Métrica": "Fora do canal", "Valor": _format_count(integrity.get("outside_channel_count"))},
        {"Métrica": "Dados stale", "Valor": _format_bool(integrity.get("stale_data"))},
        {"Métrica": "Kill switch ativo", "Valor": _format_bool(integrity.get("kill_switch_active"))},
        {"Métrica": "Dust", "Valor": _format_usdt(dust.get("dust_value_usdt"))},
    ]


def _diagnostic_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    summary = _mapping(snapshot.get("status_summary"))
    return [
        {"Campo": "Schema", "Valor": _display_text(snapshot.get("schema_version"))},
        {"Campo": "Schema esperado", "Valor": EXPECTED_SCHEMA_VERSION},
        {"Campo": "Runtime mode", "Valor": _display_text(snapshot.get("runtime_mode"))},
        {"Campo": "Status", "Valor": status_to_label(_snapshot_status(snapshot))},
        {"Campo": "Atualizado UTC", "Valor": _display_text(snapshot.get("last_updated_utc"))},
        {
            "Campo": "Fontes obrigatórias ausentes",
            "Valor": _format_count(summary.get("missing_required_sources_count")),
        },
        {
            "Campo": "Fontes opcionais ausentes",
            "Valor": _format_count(summary.get("missing_optional_sources_count")),
        },
        {
            "Campo": "Fontes FUTURE pendentes",
            "Valor": _format_count(summary.get("future_sources_pending_count")),
        },
        {"Campo": "Erros de fonte", "Valor": _format_count(summary.get("errors_count"))},
    ]


def _safety_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    safety = _mapping(snapshot.get("safety"))
    return [
        {
            "Controle": "dashboard_readonly",
            "Valor": _format_bool(snapshot.get("dashboard_readonly")),
        },
        {"Controle": "paper_only", "Valor": _format_bool(snapshot.get("paper_only"))},
        {"Controle": "shadow_only", "Valor": _format_bool(snapshot.get("shadow_only"))},
        {"Controle": "live_locked", "Valor": _format_bool(snapshot.get("live_locked"))},
        {
            "Controle": "order_submission_enabled",
            "Valor": _format_bool(snapshot.get("order_submission_enabled")),
        },
        {
            "Controle": "real_order_submission_enabled",
            "Valor": _format_bool(snapshot.get("real_order_submission_enabled")),
        },
        {"Controle": "sends_orders", "Valor": _format_bool(safety.get("sends_orders"))},
        {"Controle": "changes_risk", "Valor": _format_bool(safety.get("changes_risk"))},
        {"Controle": "changes_model", "Valor": _format_bool(safety.get("changes_model"))},
        {
            "Controle": "uses_private_exchange",
            "Valor": _format_bool(safety.get("uses_private_exchange")),
        },
    ]


def _heatmap_message(heatmap: Mapping[str, Any]) -> str:
    available = heatmap.get("heatmap_available") is True
    reason = _display_text(heatmap.get("heatmap_reason"))
    if available:
        return (
            "Fonte temporal declarada, porém nenhuma série temporal renderizável existe "
            f"neste contrato · {reason} · UNKNOWN"
        )
    return f"Eixo temporal real não materializado · {reason} · UNKNOWN"


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _spread(best_bid: Any, best_ask: Any) -> float | None:
    bid = _finite_float(best_bid)
    ask = _finite_float(best_ask)
    if bid is None or ask is None:
        return None
    return ask - bid


def _display_text(value: Any) -> str:
    if value is None or value == "":
        return "UNKNOWN"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _text_or_none(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _format_price(value: Any) -> str:
    number = _finite_float(value)
    if number is None:
        return "UNKNOWN"
    magnitude = abs(number)
    if magnitude >= 1000.0:
        return f"{number:,.2f}"
    if magnitude >= 1.0:
        return f"{number:.4f}".rstrip("0").rstrip(".")
    return f"{number:.8f}".rstrip("0").rstrip(".") or "0"


def _format_price_range(lower: Any, upper: Any) -> str:
    if _finite_float(lower) is None or _finite_float(upper) is None:
        return "UNKNOWN"
    return f"{_format_price(lower)} → {_format_price(upper)}"


def _format_decimal(value: Any, *, digits: int) -> str:
    number = _finite_float(value)
    return "UNKNOWN" if number is None else f"{number:.{digits}f}"


def _format_count(value: Any) -> str:
    number = _finite_float(value)
    if number is None:
        return "UNKNOWN"
    if number.is_integer():
        return str(int(number))
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _format_score(value: Any) -> str:
    number = _finite_float(value)
    return "UNKNOWN" if number is None else f"{number:.1f}/100"


def _format_usdt(value: Any) -> str:
    number = _finite_float(value)
    return "UNKNOWN" if number is None else f"US$ {number:,.2f}"


def _format_percent(value: Any) -> str:
    number = _finite_float(value)
    return "UNKNOWN" if number is None else f"{number:.2f}%"


def _format_bool(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "UNKNOWN"


def _bucket_label(value: Any, *, fallback: int | None = None) -> str:
    number = _finite_float(value)
    if number is not None and number.is_integer() and number >= 0.0:
        return f"B{int(number):02d}"
    if fallback is not None:
        return f"B{fallback:02d}"
    return "UNKNOWN"


if __name__ == "__main__":
    main()
