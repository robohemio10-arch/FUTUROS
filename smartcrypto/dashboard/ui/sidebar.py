"""Visual navigation and environment summary for Streamlit."""

from collections.abc import Mapping
from html import escape
from typing import Any


NAV_ITEMS = [
    ("01", "Infraestrutura", "01_infrastructure"),
    ("02", "Portfólio e Risco", "02_portfolio_risk"),
    ("03", "Grid Spot Monitor", "03_grid_monitor"),
    ("04", "Oportunidades", "04_opportunity_scanner"),
    ("05", "IA / Qlib Governance", "05_ai_governance"),
    ("06", "Controles Ativos", "06_active_controls"),
    ("07", "Relatórios & TCA", "07_quantitative_reports"),
    ("08", "Alertas & Mensageria", "08_alerts_messaging"),
]

_ENVIRONMENT_FIELDS = (
    ("Conta", "account"),
    ("Ambiente", "environment"),
    ("Exchange Paper", "exchange_paper"),
    ("Região", "region"),
    ("Dashboard versão", "dashboard_version"),
    ("Uptime", "uptime"),
    ("Snapshot", "snapshot"),
    ("Fonte de dados", "data_source"),
)


def render_sidebar(
    active_page: str,
    environment: Mapping[str, Any] | None = None,
    *,
    ui: Any | None = None,
) -> None:
    target_ui = ui or _streamlit()
    sidebar = getattr(target_ui, "sidebar", None)
    surface = sidebar if hasattr(sidebar, "markdown") else target_ui
    values = environment or {}
    summary = "".join(
        f'<div class="sfc-env-row"><span>{escape(label)}</span>'
        f'<strong>{escape(str(values.get(key, "UNKNOWN")))}</strong></div>'
        for label, key in _ENVIRONMENT_FIELDS
    )
    active_label = next(
        (f"{number} · {label}" for number, label, slug in NAV_ITEMS if slug == active_page),
        "Command Center",
    )
    surface.markdown(
        '<div class="sfc-sidebar"><div class="sfc-sidebar-brand">SMART FUTUROS</div>'
        f'<div class="sfc-nav-active">{escape(active_label)}</div></div>',
        unsafe_allow_html=True,
    )
    for number, label, slug in NAV_ITEMS:
        surface.page_link(f"pages/{slug}.py", label=f"{number}  {label}")
    surface.markdown(
        f'<div class="sfc-sidebar"><div class="sfc-env-title">AMBIENTE</div>{summary}</div>',
        unsafe_allow_html=True,
    )


def _streamlit() -> Any:
    import streamlit

    return streamlit
