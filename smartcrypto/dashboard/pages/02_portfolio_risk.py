from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import get_streamlit, render_snapshot_page
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.dashboard.ui import (
    inject_smart_futuros_command_center_css,
    render_footer_audit_bar,
    render_global_topbar,
    render_page_title,
    render_sidebar,
    status_to_label,
    worst_status,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "02. Portfólio e Risco"
PAGE_NUMBER = "02"
PAGE_NAME = "Portfólio e Risco"
PAGE_SUBTITLE = "Capital, resultado e reconciliação sob autoridade final do RiskManager."
ACTIVE_PAGE = "02_portfolio_risk"
SNAPSHOT_PATH = "data/reports/dashboard_portfolio_risk_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_portfolio_risk_snapshot_v1"
REQUIRED_SECTIONS = (
    "capital_summary", "allocation", "pnl", "drawdown_risk", "tail_risk",
    "financial_truth", "risk_events", "audit",
)
METRICS = (
    ("Estimated Equity", "capital_summary", "estimated_equity"),
    ("Free Capital", "capital_summary", "free_capital_for_entries"),
    ("Capital Reserved", "capital_summary", "capital_reserved"),
    ("Inventory Value", "capital_summary", "inventory_value"),
    ("Capital Deployed", "capital_summary", "capital_deployed"),
    ("Allocated %", "allocation", "allocated_pct"),
    ("Net PnL", "pnl", "net_pnl"),
    ("Unrealized PnL", "pnl", "unrealized_pnl"),
    ("Max Drawdown", "drawdown_risk", "max_drawdown_pct"),
    ("VaR 95", "tail_risk", "parametric_var_95"),
    ("CVaR 95", "tail_risk", "cvar_95"),
    ("Reconciliation", "financial_truth", "reconciliation_status"),
)


_UNKNOWN = "DESCONHECIDO"
_SIDEBAR_NAV = (
    ("01", "Infraestrutura geral", "/infrastructure", False),
    ("02", "Portfólio e Risco", "/portfolio_risk", True),
    ("03", "Grid Spot Monitor", "/grid_monitor", False),
    ("04", "Arbitragem e Lançamentos", "/opportunity_scanner", False),
    ("05", "Governança IA / Qlib", "/ai_governance", False),
    ("06", "Painel de Controle Ativo", "/active_controls", False),
    ("07", "Relatórios Quantitativos & TCA", "/quantitative_reports", False),
    ("08", "Alertas & Mensageria", "/alerts_messaging", False),
)


# These names are intentionally referenced to preserve the shared visual-contract imports while
# this page uses the full-screen custom shell validated on Aba 01.
_SHARED_VISUAL_CONTRACT_REFERENCES = (
    render_global_topbar,
    render_page_title,
    render_sidebar,
)


def render_page(snapshot: dict[str, Any], *, ui: Any | None = None) -> None:
    target_ui = ui or get_streamlit()
    target_ui.title(PAGE_TITLE)
    inject_smart_futuros_command_center_css(ui=target_ui)
    target_ui.markdown(_portfolio_risk_css(), unsafe_allow_html=True)
    target_ui.markdown(_portfolio_risk_shell_html(snapshot), unsafe_allow_html=True)

    target_ui.markdown(
        '<div class="sfc-readonly-banner">'
        "DETALHAMENTO CANÔNICO READ-ONLY · tabela preservada para auditoria e testes."
        "</div>",
        unsafe_allow_html=True,
    )
    with target_ui.expander("Detalhamento canônico da Aba 02 · snapshot-first/read-only", expanded=False):
        render_snapshot_page(
            title=PAGE_TITLE,
            snapshot_path=SNAPSHOT_PATH,
            snapshot=snapshot,
            section_order=REQUIRED_SECTIONS,
            metric_specs=METRICS,
            ui=target_ui,
            render_chrome=False,
        )
    render_footer_audit_bar(SNAPSHOT_PATH, ui=target_ui)


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(load_page_snapshot(DashboardPageId.portfolio_risk, project_root=project_root), ui=ui)




def _portfolio_risk_css() -> str:
    return """
<style>
[data-testid="stSidebar"],
[data-testid="collapsedControl"],
[data-testid="stSidebarNav"] { display: none !important; }
[data-testid="stHeader"] { background: transparent !important; height: 0 !important; }
[data-testid="stToolbar"], #MainMenu, footer { display: none !important; }
h1 { display: none !important; }
.block-container { max-width: 100vw !important; padding: 0 !important; }
[data-testid="stAppViewContainer"] > .main { margin-left: 0 !important; }

.fpr-root {
    --fpr-bg: #020811;
    --fpr-surface: #061321;
    --fpr-surface-2: #081b2b;
    --fpr-line: rgba(59, 135, 183, .42);
    --fpr-line-soft: rgba(59, 135, 183, .20);
    --fpr-cyan: #00c8ff;
    --fpr-blue: #3aa6ff;
    --fpr-green: #00e69a;
    --fpr-yellow: #ffd84a;
    --fpr-red: #ff4b45;
    --fpr-purple: #b65cff;
    --fpr-muted: #8ba6bb;
    --fpr-text: #e9f6ff;
    display: grid;
    grid-template-columns: 188px minmax(0, 1fr);
    gap: 7px;
    min-height: calc(100vh - 4px);
    height: calc(100vh - 4px);
    max-width: 100vw;
    overflow: hidden;
    padding: 6px 8px 4px 6px;
    color: var(--fpr-text);
    background:
        radial-gradient(circle at 18% 0%, rgba(0, 200, 255, .11), transparent 26%),
        radial-gradient(circle at 78% 12%, rgba(182, 92, 255, .07), transparent 24%),
        linear-gradient(180deg, #020811 0%, #03101d 100%);
    border: 1px solid rgba(0, 200, 255, .18);
    box-sizing: border-box;
    font-family: "Inter", "Segoe UI", "Roboto Condensed", system-ui, sans-serif;
}
.fpr-sidebar {
    display: grid;
    grid-template-rows: auto 1fr auto;
    min-height: 0;
    border: 1px solid var(--fpr-line-soft);
    background: linear-gradient(180deg, rgba(5, 20, 33, .98), rgba(2, 10, 18, .98));
    box-shadow: inset -1px 0 0 rgba(0, 200, 255, .10);
}
.fpr-side-brand { padding: 12px 10px 10px; border-bottom: 1px solid var(--fpr-line-soft); }
.fpr-side-brand strong { display: block; color: var(--fpr-cyan); font-size: 12px; line-height: 1.15; letter-spacing: .06em; text-transform: uppercase; }
.fpr-side-brand span { color: var(--fpr-muted); font-size: 10px; line-height: 1.2; }
.fpr-nav { padding: 8px 6px; overflow: hidden; }
.fpr-nav a {
    display: grid;
    grid-template-columns: 27px 1fr;
    align-items: center;
    gap: 5px;
    min-height: 39px;
    padding: 5px 6px;
    margin-bottom: 4px;
    border-radius: 6px;
    color: #bfd4e5;
    text-decoration: none;
    font-size: 11px;
    line-height: 1.12;
    border: 1px solid transparent;
}
.fpr-nav a span {
    display: inline-grid;
    place-items: center;
    width: 21px;
    height: 21px;
    border-radius: 50%;
    border: 1px solid rgba(0, 200, 255, .35);
    color: var(--fpr-blue);
    font-weight: 800;
    font-size: 11px;
}
.fpr-nav a.is-active { color: #fff; background: rgba(0, 155, 255, .16); border-color: rgba(0, 200, 255, .38); box-shadow: 0 0 18px rgba(0, 200, 255, .10); }
.fpr-env { padding: 7px 8px 10px; border-top: 1px solid var(--fpr-line-soft); }
.fpr-env-title { color: var(--fpr-muted); font-size: 9px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 5px; }
.fpr-env-row { display: flex; justify-content: space-between; gap: 5px; padding: 3px 0; border-bottom: 1px solid rgba(59, 135, 183, .14); font-size: 9px; line-height: 1.18; color: var(--fpr-muted); }
.fpr-env-row strong { color: #d6eaff; max-width: 108px; text-align: right; overflow-wrap: anywhere; }

.fpr-main { min-width: 0; min-height: 0; display: grid; grid-template-rows: 88px minmax(0, 1fr); gap: 7px; }
.fpr-topbar {
    display: grid;
    grid-template-columns: minmax(340px, 1fr) auto;
    gap: 9px;
    align-items: center;
    min-height: 70px;
    padding: 8px 11px;
    border: 1px solid var(--fpr-line);
    border-radius: 7px;
    background: linear-gradient(135deg, rgba(5, 19, 32, .98), rgba(3, 13, 22, .94));
    box-shadow: 0 0 24px rgba(0, 200, 255, .08);
}
.fpr-title strong { display: block; color: #f5fbff; font-size: 24px; letter-spacing: .02em; line-height: 1.08; }
.fpr-title strong span { color: var(--fpr-cyan); }
.fpr-title small { display: block; color: var(--fpr-muted); font-size: 10px; line-height: 1.25; margin-top: 3px; max-width: 1560px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fpr-badges { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 7px; }
.fpr-badge { display: inline-flex; align-items: center; border-radius: 5px; padding: 4px 8px; font-size: 9px; line-height: 1.1; font-weight: 900; letter-spacing: .03em; text-transform: uppercase; border: 1px solid currentColor; background: rgba(2, 10, 18, .55); white-space: nowrap; }
.fpr-badge.paper { color: var(--fpr-cyan); }
.fpr-badge.live, .fpr-badge.orders { color: var(--fpr-red); }
.fpr-badge.ready { color: var(--fpr-yellow); }
.fpr-badge.risk { color: var(--fpr-green); }
.fpr-meta { display: grid; grid-template-columns: repeat(2, auto); gap: 4px 10px; justify-content: end; color: var(--fpr-muted); font-size: 10px; line-height: 1.2; text-align: right; }
.fpr-meta strong { color: #d9efff; font-weight: 700; }
.fpr-board { min-height: 0; overflow: hidden; }

.fpr-grid {
    display: grid;
    grid-template-columns: repeat(12, minmax(0, 1fr));
    grid-template-rows: minmax(90px, .74fr) minmax(210px, 1.72fr) minmax(170px, 1.26fr) minmax(150px, 1.08fr);
    gap: 7px;
    height: calc(100vh - 108px);
    min-height: 0;
}
.fpr-panel {
    min-height: 0;
    overflow: hidden;
    padding: 8px;
    border-radius: 7px;
    border: 1px solid var(--fpr-line-soft);
    background:
        radial-gradient(circle at top right, rgba(0, 200, 255, .08), transparent 38%),
        linear-gradient(135deg, rgba(6, 19, 31, .96), rgba(4, 13, 22, .98));
    box-shadow: 0 0 16px rgba(0, 200, 255, .055);
}
.fpr-panel-head { display: flex; justify-content: space-between; gap: 8px; align-items: start; padding-bottom: 5px; margin-bottom: 6px; border-bottom: 1px solid rgba(59, 135, 183, .28); }
.fpr-panel-title { font-size: 10.5px; line-height: 1.18; letter-spacing: .045em; color: #f3fbff; text-transform: uppercase; font-weight: 900; }
.fpr-panel-subtitle { font-size: 9px; line-height: 1.18; color: var(--fpr-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.fpr-pill { display: inline-flex; align-items: center; border-radius: 4px; padding: 3px 7px; font-size: 8.5px; line-height: 1.05; font-weight: 900; border: 1px solid currentColor; background: rgba(2,10,18,.55); white-space: nowrap; }
.fpr-status-ok { color: var(--fpr-green); }
.fpr-status-info, .fpr-status-paper, .fpr-status-readonly { color: var(--fpr-cyan); }
.fpr-status-warning, .fpr-status-monitoring, .fpr-status-stale { color: var(--fpr-yellow); }
.fpr-status-error, .fpr-status-critical, .fpr-status-blocked, .fpr-status-hard_blocked { color: var(--fpr-red); }
.fpr-status-purple, .fpr-status-shadow { color: var(--fpr-purple); }
.fpr-status-disabled, .fpr-status-neutral, .fpr-status-unknown, .fpr-status-planned { color: #8a98a8; }

.fpr-capital { grid-column: 1 / -1; }
.fpr-allocation { grid-column: span 5; }
.fpr-pnl { grid-column: span 7; }
.fpr-drawdown { grid-column: span 4; }
.fpr-tail { grid-column: span 4; }
.fpr-truth { grid-column: span 4; }
.fpr-events { grid-column: span 8; }
.fpr-integrity { grid-column: span 4; }

.fpr-kpi-grid { display: grid; grid-template-columns: repeat(6, minmax(142px, 1fr)); gap: 7px; min-height: 0; }
.fpr-mini-grid { display: grid; grid-template-columns: repeat(2, minmax(132px, 1fr)); gap: 7px; }
.fpr-kpi {
    min-height: 58px;
    padding: 7px;
    border-radius: 6px;
    border: 1px solid rgba(59, 135, 183, .24);
    background: rgba(8, 24, 39, .72);
    overflow: hidden;
}
.fpr-kpi-label { font-size: 9px; line-height: 1.15; color: #9bb4c9; margin-bottom: 3px; }
.fpr-kpi-value { color: #f1f9ff; font-size: 15px; line-height: 1.08; font-weight: 900; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; word-break: keep-all; }
.fpr-kpi-helper { color: var(--fpr-muted); font-size: 8.5px; line-height: 1.12; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fpr-card-status-ok { border-left: 3px solid var(--fpr-green); box-shadow: inset 0 0 0 1px rgba(0, 230, 154, .14); }
.fpr-card-status-warning, .fpr-card-status-monitoring, .fpr-card-status-stale { border-left: 3px solid var(--fpr-yellow); box-shadow: inset 0 0 0 1px rgba(255, 216, 74, .14); }
.fpr-card-status-error, .fpr-card-status-critical, .fpr-card-status-blocked, .fpr-card-status-hard_blocked { border-left: 3px solid var(--fpr-red); box-shadow: inset 0 0 0 1px rgba(255, 75, 69, .18), 0 0 12px rgba(255, 75, 69, .08); }
.fpr-card-status-purple, .fpr-card-status-shadow { border-left: 3px solid var(--fpr-purple); }
.fpr-card-status-info, .fpr-card-status-paper, .fpr-card-status-readonly { border-left: 3px solid var(--fpr-cyan); }
.fpr-card-status-disabled, .fpr-card-status-neutral, .fpr-card-status-unknown, .fpr-card-status-planned { border-left: 3px solid #7c8ea0; }

.fpr-split-allocation { display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 9px; align-items: center; min-height: 0; }
.fpr-donut { width: 108px; height: 108px; border-radius: 50%; margin: 0 auto; position: relative; background: conic-gradient(#7c8ea0 0 100%); border: 1px solid rgba(59, 135, 183, .28); box-shadow: inset 0 0 18px rgba(0,0,0,.45), 0 0 18px rgba(0,200,255,.10); }
.fpr-donut::after { content: attr(data-label); position: absolute; inset: 24px; display: grid; place-items: center; border-radius: 50%; text-align: center; white-space: pre-line; background: #061321; border: 1px solid rgba(59,135,183,.24); color: #e9f6ff; font-size: 10px; line-height: 1.15; font-weight: 900; }
.fpr-table { width: 100%; border-collapse: collapse; font-size: 9px; line-height: 1.15; }
.fpr-table th { text-align: left; color: #a9bed0; font-size: 8px; text-transform: uppercase; padding: 4px 5px; border-bottom: 1px solid rgba(59,135,183,.28); }
.fpr-table td { color: #e6f1ff; padding: 4px 5px; border-bottom: 1px solid rgba(59,135,183,.12); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 168px; }
.fpr-table td:last-child, .fpr-table th:last-child { text-align: right; }
.fpr-muted-box { border: 1px dashed rgba(59,135,183,.32); border-radius: 6px; padding: 12px; color: #8ba6bb; font-size: 10px; text-align: center; background: rgba(3,13,22,.40); }

.fpr-pnl-strip { display: grid; grid-template-columns: repeat(5, minmax(118px, 1fr)); gap: 7px; margin-bottom: 7px; }
.fpr-chart-row { display: grid; grid-template-columns: 1.65fr .95fr; gap: 7px; min-height: 0; }
.fpr-chart-card { border: 1px solid rgba(59,135,183,.24); border-radius: 6px; padding: 7px; min-height: 112px; background: rgba(3, 13, 22, .42); overflow: hidden; }
.fpr-chart-title { color: #dff6ff; font-size: 9.5px; line-height: 1.12; font-weight: 900; letter-spacing: .05em; text-transform: uppercase; margin-bottom: 6px; }
.fpr-chart-placeholder { position: relative; height: 112px; border-radius: 5px; border: 1px solid rgba(59,135,183,.15); background: linear-gradient(rgba(59,135,183,.10) 1px, transparent 1px) 0 0 / 100% 22px, linear-gradient(90deg, rgba(59,135,183,.10) 1px, transparent 1px) 0 0 / 52px 100%, rgba(2,10,18,.55); overflow: hidden; }
.fpr-chart-placeholder svg { display: block; width: 100%; height: 112px; }
.fpr-chart-note { position: absolute; right: 7px; bottom: 5px; color: rgba(139,166,187,.70); font-size: 8.5px; letter-spacing: .06em; }
.fpr-chart-empty { display: grid; place-items: center; height: 112px; color: #8ba6bb; font-size: 10px; }

.fpr-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 7px; align-items: center; padding: 4px 0; border-bottom: 1px solid rgba(59,135,183,.13); color: #a9bed0; font-size: 9px; line-height: 1.18; }
.fpr-row strong { color: #f2fbff; font-size: 9px; text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 220px; }
.fpr-row .fpr-good { color: var(--fpr-green); }
.fpr-row .fpr-warn { color: var(--fpr-yellow); }
.fpr-row .fpr-bad { color: var(--fpr-red); }
.fpr-truth-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 2px; min-height: 0; }
.fpr-footer { display: flex; justify-content: space-between; gap: 10px; padding: 6px 8px 0; color: #8ba6bb; font-size: 9px; border-top: 1px solid rgba(59,135,183,.18); margin-top: 6px; }

@media (max-width: 1380px) {
    .fpr-root { grid-template-columns: 146px minmax(0, 1fr); overflow: visible; height: auto; }
    .fpr-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); height: auto; grid-template-rows: auto; }
    .fpr-capital, .fpr-allocation, .fpr-pnl, .fpr-drawdown, .fpr-tail, .fpr-truth, .fpr-events, .fpr-integrity { grid-column: span 1; }
    .fpr-kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .fpr-pnl-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .fpr-chart-row { grid-template-columns: 1fr; }
}
@media (max-width: 900px) {
    .fpr-root { grid-template-columns: 1fr; overflow: visible; }
    .fpr-sidebar { display: none; }
    .fpr-main { display: block; }
    .fpr-topbar { grid-template-columns: 1fr; }
    .fpr-meta { justify-content: start; text-align: left; }
    .fpr-split-allocation { grid-template-columns: 1fr; }
}
</style>
"""


def _portfolio_risk_shell_html(snapshot: Mapping[str, Any]) -> str:
    dashboard_status = _dashboard_status(snapshot)
    financial_truth = _section(snapshot, "financial_truth")
    risk_events = _section(snapshot, "risk_events")
    reconciliation_status = _text(_first_value(financial_truth, "reconciliation_status", "status"))
    reconciliation_visual = _reconciliation_status(financial_truth)
    kill_visual = _kill_switch_status(risk_events)
    board_status = worst_status(dashboard_status, reconciliation_visual, kill_visual)
    return (
        '<div class="fpr-root">'
        f'{_sidebar_html(snapshot)}'
        '<main class="fpr-main">'
        f'{_topbar_html(snapshot, board_status, reconciliation_status)}'
        '<section class="fpr-board">'
        f'{_grid_html(snapshot)}'
        '</section>'
        '</main>'
        '</div>'
    )


def _sidebar_html(snapshot: Mapping[str, Any]) -> str:
    environment = _environment(snapshot)
    nav_html = "".join(
        '<a class="{}" href="{}"><span>{}</span><b>{}</b></a>'.format(
            "is-active" if active else "",
            escape(path),
            escape(number),
            escape(label),
        )
        for number, label, path, active in _SIDEBAR_NAV
    )
    env_rows = "".join(
        '<div class="fpr-env-row"><span>{}</span><strong>{}</strong></div>'.format(
            escape(label), escape(str(environment.get(key, _UNKNOWN)))
        )
        for label, key in (
            ("Conta", "account"),
            ("Ambiente", "environment"),
            ("Exchange", "exchange_paper"),
            ("Região", "region"),
            ("Dashboard", "dashboard_version"),
            ("Snapshot", "snapshot"),
            ("Fonte", "data_source"),
        )
    )
    return (
        '<aside class="fpr-sidebar">'
        '<div class="fpr-side-brand"><strong>SMART FUTUROS</strong><span>Command Center Institucional</span></div>'
        f'<nav class="fpr-nav">{nav_html}</nav>'
        f'<div class="fpr-env"><div class="fpr-env-title">Resumo ambiente</div>{env_rows}</div>'
        '</aside>'
    )


def _topbar_html(snapshot: Mapping[str, Any], board_status: str, reconciliation_status: str) -> str:
    updated = _first_text(snapshot.get("last_updated_utc"), snapshot.get("generated_at_utc"), _UNKNOWN)
    now_utc = _format_datetime_utc(datetime.now(timezone.utc))
    updated_display = _format_datetime_utc(updated)
    dashboard_version = _first_text(snapshot.get("dashboard_version"), snapshot.get("version"), "aba02-visual-v1")
    account = _first_text(snapshot.get("account"), snapshot.get("account_name"), "PAPER-INSTITUCIONAL")
    mode = _first_text(snapshot.get("mode"), snapshot.get("execution_mode"), "SHADOW ONLY")
    runtime = _first_text(snapshot.get("runtime_mode"), snapshot.get("environment"), "PAPER").upper()
    status_line = " · ".join(
        (
            f"Status {status_to_label(board_status)}",
            f"Reconciliação {escape(reconciliation_status)}",
            "capital / ledger / drawdown / VaR",
            "RiskManager Authority",
        )
    )
    badges = "".join(
        (
            '<span class="fpr-badge paper">PAPER / SHADOW ONLY</span>',
            '<span class="fpr-badge live">LIVE LOCKED</span>',
            '<span class="fpr-badge orders">ORDER SUBMISSION DISABLED</span>',
            '<span class="fpr-badge ready">READINESS BLOCKED</span>',
            '<span class="fpr-badge risk">RISKMANAGER AUTHORITY</span>',
        )
    )
    return (
        '<header class="fpr-topbar">'
        '<div class="fpr-title">'
        '<strong><span>02.</span> PORTFÓLIO E RISCO</strong>'
        f'<small>Visão consolidada de capital, exposição, PnL, cauda e reconciliação · {escape(status_line)}</small>'
        f'<div class="fpr-badges">{badges}</div>'
        '</div>'
        '<div class="fpr-meta">'
        f'<span>Conta <strong>{escape(account)}</strong></span>'
        f'<span>Modo <strong>{escape(mode)}</strong></span>'
        f'<span>Data <strong>{escape(now_utc)}</strong></span>'
        f'<span>Versão <strong>{escape(dashboard_version)}</strong></span>'
        f'<span>UTC <strong>{escape(now_utc)}</strong></span>'
        f'<span>Env <strong>{escape(runtime)}</strong></span>'
        '<span>Região <strong>SAO-1</strong></span>'
        '<span>Status <strong>READ-ONLY</strong></span>'
        f'<span>Snapshot <strong>{escape(updated_display)}</strong></span>'
        '</div>'
        '</header>'
    )


def _grid_html(snapshot: Mapping[str, Any]) -> str:
    return (
        '<section class="fpr-grid">'
        f'{_capital_panel(snapshot)}'
        f'{_allocation_panel(snapshot)}'
        f'{_pnl_panel(snapshot)}'
        f'{_drawdown_panel(snapshot)}'
        f'{_tail_risk_panel(snapshot)}'
        f'{_financial_truth_panel(snapshot)}'
        f'{_risk_events_panel(snapshot)}'
        f'{_integrity_panel(snapshot)}'
        '</section>'
    )


def _capital_panel(snapshot: Mapping[str, Any]) -> str:
    capital = _section(snapshot, "capital_summary")
    allocation = _section(snapshot, "allocation")
    pnl = _section(snapshot, "pnl")
    truth = _section(snapshot, "financial_truth")
    status = worst_status(_status_from(capital), _status_from(allocation), _reconciliation_status(truth))
    cards = (
        _kpi("Saldo disponível", _money(_first_value(capital, "cash_available", "cash_available_usdt", "free_capital_for_entries")), "USDT", status),
        _kpi("Saldo bloqueado em ordens", _money(_first_value(capital, "cash_locked", "cash_locked_usdt", "locked_capital")), "ordens/ledger", _status_from(capital)),
        _kpi("Capital reservado pelo ledger", _money(_first_value(capital, "capital_reserved", "reserved_notional")), "USDT", _status_from(capital)),
        _kpi("Exposição total em cripto", _money(_first_value(capital, "inventory_value", "crypto_exposure", "total_exposure")), "notional", _status_from(allocation)),
        _kpi("Patrimônio líquido estimado", _money(_first_value(capital, "estimated_equity", "estimated_equity_usdt")), "USDT", _status_from(capital)),
        _kpi("Reconciliação", _reconciliation_label(truth), "ledger vs fontes", _reconciliation_status(truth)),
    )
    return _panel(
        "1. Resumo de capital",
        "Capital e exposição lidos do snapshot autorizado; sem cálculo financeiro crítico na UI.",
        status,
        f'<div class="fpr-kpi-grid">{"".join(cards)}</div>',
        "fpr-capital",
    )


def _allocation_panel(snapshot: Mapping[str, Any]) -> str:
    allocation = _section(snapshot, "allocation")
    capital = _section(snapshot, "capital_summary")
    status = _status_from(allocation)
    rows = _allocation_rows(allocation)
    if rows:
        table = _allocation_table(rows)
        donut_label = "Total\\A" + _short_money(_first_value(capital, "estimated_equity"))
        donut = f'<div class="fpr-donut" data-label="{escape(donut_label)}" style="background:{escape(_donut_gradient(rows))};"></div>'
    else:
        table = '<div class="fpr-muted-box">Alocação por ativo não materializada no snapshot.</div>'
        donut = f'<div class="fpr-donut" data-label="{escape(_UNKNOWN)}"></div>'
    body = f'<div class="fpr-split-allocation">{donut}<div>{table}</div></div>'
    body += _rows(
        ("Capital alocado", _money(_first_value(capital, "capital_deployed")), None),
        ("Capital livre", _money(_first_value(capital, "free_capital_for_entries")), None),
        ("Alocação do PL", _pct(_first_value(allocation, "allocated_pct")), None),
    )
    return _panel("2. Alocação e exposição por ativo", "Donut e tabela usam somente campos existentes no snapshot.", status, body, "fpr-allocation")


def _pnl_panel(snapshot: Mapping[str, Any]) -> str:
    pnl = _section(snapshot, "pnl")
    drawdown = _section(snapshot, "drawdown_risk")
    status = worst_status(_status_from(pnl), _status_from(drawdown))
    strip = "".join(
        (
            _kpi("Realizado 24h", _money(_first_value(pnl, "realized_24h", "net_pnl_24h", "pnl_24h")), "USDT", _status_from(pnl)),
            _kpi("Realizado 7d", _money(_first_value(pnl, "realized_7d", "net_pnl_7d", "pnl_7d")), "USDT", _status_from(pnl)),
            _kpi("Realizado 30d", _money(_first_value(pnl, "realized_30d", "net_pnl_30d", "pnl_30d")), "USDT", _status_from(pnl)),
            _kpi("Realizado total", _money(_first_value(pnl, "net_pnl", "gross_pnl")), "líquido", _status_from(pnl)),
            _kpi("Não realizado", _money(_first_value(pnl, "unrealized_pnl")), "flutuante", _status_from(pnl)),
        )
    )
    equity = _number_list(_first_value(drawdown, "equity_curve"))
    dd_series = _number_list(_first_value(drawdown, "drawdown_series_pct"))
    body = f'<div class="fpr-pnl-strip">{strip}</div>'
    body += (
        '<div class="fpr-chart-row">'
        f'<div class="fpr-chart-card"><div class="fpr-chart-title">Curva de patrimônio líquido</div>{_sparkline(equity, "Equity curve")}</div>'
        f'<div class="fpr-chart-card"><div class="fpr-chart-title">Drawdown (%)</div>{_sparkline(dd_series, "Drawdown", invert=True)}</div>'
        '</div>'
    )
    return _panel("3. PnL realizado e não realizado", "Séries financeiras derivadas do snapshot; sem recomputar relatório.", status, body, "fpr-pnl")


def _drawdown_panel(snapshot: Mapping[str, Any]) -> str:
    drawdown = _section(snapshot, "drawdown_risk")
    capital = _section(snapshot, "capital_summary")
    events = _section(snapshot, "risk_events")
    status = worst_status(_status_from(drawdown), _kill_switch_status(events))
    dd_series = _number_list(_first_value(drawdown, "drawdown_series_pct"))
    current_dd = dd_series[-1] if dd_series else _first_value(drawdown, "current_drawdown_pct")
    body = '<div class="fpr-mini-grid">' + "".join(
        (
            _kpi("Max drawdown", _pct(_first_value(drawdown, "max_drawdown_pct")), "risco", _status_from(drawdown)),
            _kpi("Drawdown atual", _pct(current_dd), "atual", _status_from(drawdown)),
            _kpi("Drawdown duration", _duration(_first_value(drawdown, "drawdown_duration", "drawdown_duration_seconds")), "tempo", _status_from(drawdown)),
            _kpi("Recovery time", _duration(_first_value(drawdown, "recovery_time", "recovery_time_seconds")), "tempo", _status_from(drawdown)),
            _kpi("Capital preso", _money(_first_value(capital, "capital_deployed", "capital_reserved")), "USDT", _status_from(capital)),
            _kpi("Distância até break-even", _money(_first_value(drawdown, "distance_to_breakeven", "distance_to_break_even")), "USDT", _status_from(drawdown)),
            _kpi("Risk mode", _text(_first_value(events, "risk_mode")), "governança", _status_from(events)),
            _kpi("Kill switch status", _kill_switch_label(events), "autoridade risco", _kill_switch_status(events)),
        )
    ) + '</div>'
    body += _rows(
        ("Safety Orders bloqueadas", _yes_no_unknown(_first_value(events, "safety_orders_blocked")), _status_from_bool(_first_value(events, "safety_orders_blocked"), true_status="blocked", false_status="ok")),
        ("Reduce-only mode", _yes_no_unknown(_first_value(events, "reduce_only_mode")), _status_from(events)),
        ("Protection mode", _yes_no_unknown(_first_value(events, "protection_mode")), _status_from(events)),
        ("Limite diário de perda", _money(_first_value(events, "daily_loss_limit", "max_daily_loss_usdt")), _status_from(events)),
    )
    return _panel("4. Drawdown e controles de risco", "Kill switch e divergência devem permanecer visualmente dominantes.", status, body, "fpr-drawdown")


def _tail_risk_panel(snapshot: Mapping[str, Any]) -> str:
    tail = _section(snapshot, "tail_risk")
    status = _status_from(tail)
    body = _rows(
        ("VaR paramétrico 95%", _money(_first_value(tail, "parametric_var_95", "var_95")), status),
        ("VaR paramétrico 99%", _money(_first_value(tail, "parametric_var_99", "var_99")), status),
        ("VaR histórico 95%", _money(_first_value(tail, "historical_var_95")), status),
        ("VaR histórico 99%", _money(_first_value(tail, "historical_var_99")), status),
        ("CVaR / Expected Shortfall 95%", _money(_first_value(tail, "cvar_95")), status),
        ("CVaR / Expected Shortfall 99%", _money(_first_value(tail, "cvar_99")), status),
        ("Risk of Ruin", _pct(_first_value(tail, "risk_of_ruin", "risk_of_ruin_pct")), status),
        ("Stress Scenario: Flash Crash", _money(_first_value(tail, "flash_crash_loss", "stress_flash_crash")), "blocked" if _has_value(_first_value(tail, "flash_crash_loss", "stress_flash_crash")) else status),
    )
    return _panel("5. VaR / CVaR / risco de cauda", "Cauda negativa e cenários adversos em leitura conservadora.", status, body, "fpr-tail")


def _financial_truth_panel(snapshot: Mapping[str, Any]) -> str:
    truth = _section(snapshot, "financial_truth")
    capital = _section(snapshot, "capital_summary")
    status = _reconciliation_status(truth)
    state_repository = _first_value(truth, "state_repository_status", "state_repository")
    position_repository = _first_value(truth, "position_repository_status", "position_repository")
    order_repository = _first_value(truth, "order_repository_status", "order_repository")
    last_reconciliation = _first_value(truth, "last_reconciliation_utc", "last_reconciled_at")
    body = '<div class="fpr-truth-grid">' + _rows(
        ("StateRepository", _text(state_repository), _status_if_present(state_repository, status)),
        ("CapitalReservationLedger", _ledger_status(capital, truth), _status_if_present(_first_value(capital, "capital_reserved"), status)),
        ("PositionRepository", _text(position_repository), _status_if_present(position_repository, status)),
        ("OrderRepository", _text(order_repository), _status_if_present(order_repository, status)),
        ("ReconciliationRepository", _reconciliation_label(truth), status),
        ("Snapshot autorizado", SNAPSHOT_PATH, "readonly"),
        ("Última reconciliação", _text(last_reconciliation), _status_if_present(last_reconciliation, status)),
        ("Status geral", "OK — Sem divergência" if status == "ok" else status_to_label(status), status),
    ) + '</div>'
    return _panel("6. Fonte de verdade financeira", "Ledger e reconciliação são fontes autoritativas; dashboard não é fonte financeira.", status, body, "fpr-truth")


def _risk_events_panel(snapshot: Mapping[str, Any]) -> str:
    events = _section(snapshot, "risk_events")
    truth = _section(snapshot, "financial_truth")
    status = worst_status(_status_from(events), _reconciliation_status(truth), _kill_switch_status(events))
    event_rows = _event_rows(events)
    if not event_rows:
        body = '<div class="fpr-muted-box">Sem eventos recentes de risco materializados no snapshot.</div>'
    else:
        body = _events_table(event_rows)
    return _panel("7. Eventos recentes de risco", "Timeline operacional de risco, reconciliação e bloqueios.", status, body, "fpr-events")


def _integrity_panel(snapshot: Mapping[str, Any]) -> str:
    truth = _section(snapshot, "financial_truth")
    status = _reconciliation_status(truth)
    divergence_status = "ok" if status == "ok" else status
    ledger_divergence = _first_value(truth, "ledger_snapshot_divergence", "ledger_snapshot_divergence_usdt")
    positions_divergence = _first_value(truth, "positions_snapshot_divergence", "position_divergence")
    unknown_orders = _first_value(truth, "unknown_orders", "dispatch_unknown_count")
    unrecognized_partial_fills = _first_value(truth, "unrecognized_partial_fills", "partial_fill_unrecognized_count")
    dispatch_unknown = _first_value(truth, "dispatch_unknown", "dispatch_unknown_count")
    body = _rows(
        ("Divergência Ledger vs snapshot", _money(ledger_divergence), _status_if_present(ledger_divergence, divergence_status)),
        ("Divergência posições vs snapshot", _text(positions_divergence), _status_if_present(positions_divergence, divergence_status)),
        ("Ordens desconhecidas", _text(unknown_orders), _status_if_present(unknown_orders, divergence_status)),
        ("Partial fills não reconhecidos", _text(unrecognized_partial_fills), _status_if_present(unrecognized_partial_fills, divergence_status)),
        ("Dispatch unknown", _text(dispatch_unknown), _status_if_present(dispatch_unknown, divergence_status)),
        ("Novas entradas bloqueadas", _yes_no_unknown(_first_value(truth, "new_entries_blocked")), _status_from_bool(_first_value(truth, "new_entries_blocked"), true_status="blocked", false_status="ok")),
        ("Integridade geral", "OK — Sistema íntegro" if status == "ok" else status_to_label(status), status),
    )
    body += '<div class="fpr-footer"><span>Dashboard Read-only</span><span>Snapshot: dashboard_portfolio_risk_snapshot.json</span></div>'
    return _panel("8. Reconciliação e integridade", "Divergência financeira nunca é suavizada pela UI.", status, body, "fpr-integrity")


def _panel(title: str, subtitle: str, status: str, body: str, css_class: str) -> str:
    safe_status = _normalize_status(status)
    return (
        f'<article class="fpr-panel {escape(css_class)} fpr-card-status-{escape(safe_status)}">'
        '<div class="fpr-panel-head"><div>'
        f'<div class="fpr-panel-title">{escape(title)}</div>'
        f'<div class="fpr-panel-subtitle">{escape(subtitle)}</div>'
        '</div>'
        f'<span class="fpr-pill fpr-status-{escape(safe_status)}">{escape(status_to_label(safe_status))}</span>'
        '</div>'
        f'{body}</article>'
    )


def _kpi(label: str, value: str, helper: str, status: str) -> str:
    safe_status = _normalize_status(status)
    return (
        f'<div class="fpr-kpi fpr-card-status-{escape(safe_status)}">'
        f'<div class="fpr-kpi-label">{escape(label)}</div>'
        f'<div class="fpr-kpi-value">{escape(value)}</div>'
        f'<div class="fpr-kpi-helper">{escape(helper)}</div>'
        '</div>'
    )


def _rows(*rows: tuple[str, str, str | None]) -> str:
    html_rows = []
    for label, value, status in rows:
        safe_status = _normalize_status(status or value)
        value_class = "fpr-good" if safe_status == "ok" else "fpr-bad" if safe_status in {"blocked", "hard_blocked", "error", "critical"} else "fpr-warn" if safe_status in {"warning", "stale", "monitoring"} else ""
        html_rows.append(
            '<div class="fpr-row">'
            f'<span>{escape(label)}</span><strong class="{escape(value_class)}">{escape(value)}</strong>'
            '</div>'
        )
    return "".join(html_rows)


def _allocation_table(rows: list[Mapping[str, Any]]) -> str:
    body = "".join(
        "<tr>"
        f"<td>{escape(_text(row.get('asset')))}</td>"
        f"<td>{escape(_money(row.get('notional')))}</td>"
        f"<td>{escape(_pct(row.get('pct')))}</td>"
        f"<td>{escape(_money(row.get('pnl')))}</td>"
        f"<td>{escape(_text(row.get('status')))}</td>"
        "</tr>"
        for row in rows[:7]
    )
    return (
        '<table class="fpr-table"><thead><tr>'
        '<th>Ativo</th><th>Notional</th><th>% PL</th><th>PnL</th><th>Status</th>'
        f'</tr></thead><tbody>{body}</tbody></table>'
    )


def _events_table(rows: list[Mapping[str, Any]]) -> str:
    body = "".join(
        "<tr>"
        f"<td>{escape(_text(row.get('time')))}</td>"
        f"<td>{escape(_text(row.get('severity')))}</td>"
        f"<td>{escape(_text(row.get('category')))}</td>"
        f"<td>{escape(_text(row.get('event')))}</td>"
        f"<td>{escape(_text(row.get('details')))}</td>"
        f"<td>{escape(_text(row.get('status')))}</td>"
        "</tr>"
        for row in rows[:8]
    )
    return (
        '<table class="fpr-table"><thead><tr>'
        '<th>Horário</th><th>Sev.</th><th>Categoria</th><th>Evento</th><th>Detalhes</th><th>Status</th>'
        f'</tr></thead><tbody>{body}</tbody></table>'
    )


def _sparkline(values: list[float], label: str, *, invert: bool = False) -> str:
    if len(values) < 2:
        return '<div class="fpr-chart-placeholder"><div class="fpr-chart-empty">Série insuficiente no snapshot</div><div class="fpr-chart-note">SNAPSHOT-FIRST</div></div>'
    safe_values = values[:160]
    width = 520
    height = 112
    pad = 10
    min_v = min(safe_values)
    max_v = max(safe_values)
    span = max(max_v - min_v, 1e-9)
    step = (width - (2 * pad)) / max(len(safe_values) - 1, 1)
    points = []
    for idx, value in enumerate(safe_values):
        x = pad + idx * step
        normalized = (value - min_v) / span
        y = pad + normalized * (height - 2 * pad) if invert else height - pad - normalized * (height - 2 * pad)
        points.append(f"{x:.2f},{y:.2f}")
    start_y = points[0].split(",")[1]
    end_x, end_y = points[-1].split(",")
    return (
        '<div class="fpr-chart-placeholder">'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">'
        '<defs><linearGradient id="fprLine" x1="0" x2="1" y1="0" y2="0">'
        '<stop offset="0%" stop-color="#00c8ff" stop-opacity=".45"/>'
        '<stop offset="100%" stop-color="#00e69a" stop-opacity=".92"/>'
        '</linearGradient></defs>'
        f'<line x1="{pad}" y1="{start_y}" x2="{width - pad}" y2="{start_y}" stroke="rgba(255,216,74,.35)" stroke-width="1"/>'
        f'<polyline fill="none" stroke="url(#fprLine)" stroke-width="2.4" points="{" ".join(points)}"/>'
        f'<circle cx="{end_x}" cy="{end_y}" r="3.4" fill="#00e69a"/>'
        '</svg>'
        f'<div class="fpr-chart-note">min {min_v:.2f} · max {max_v:.2f}</div>'
        '</div>'
    )


def _allocation_rows(allocation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = _first_value(allocation, "asset_allocation", "allocation", "assets")
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return []
    rows: list[Mapping[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        asset = _first_value(item, "asset", "symbol", "currency", "coin")
        notional = _first_value(item, "notional", "notional_usdt", "value", "value_usdt")
        pct = _first_value(item, "pct", "percent", "portfolio_pct", "allocation_pct")
        pnl = _first_value(item, "pnl", "floating_pnl", "unrealized_pnl", "unrealized_pnl_usdt")
        status = _first_value(item, "status", "risk_status") or "UNKNOWN"
        rows.append({"asset": asset, "notional": notional, "pct": pct, "pnl": pnl, "status": status})
    return rows


def _event_rows(events: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = _first_value(events, "recent", "events", "items", "rows")
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return []
    rows: list[Mapping[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            {
                "time": _first_value(item, "timestamp_utc", "time", "created_at_utc"),
                "severity": _first_value(item, "severity", "level", "status"),
                "category": _first_value(item, "category", "type"),
                "event": _first_value(item, "event", "message", "name"),
                "details": _first_value(item, "details", "reason", "description"),
                "status": _first_value(item, "status", "result"),
            }
        )
    return rows


def _donut_gradient(rows: list[Mapping[str, Any]]) -> str:
    colors = ("#00e69a", "#ffd84a", "#b65cff", "#00c8ff", "#d7962a", "#5968d8", "#7c8ea0")
    values = [_to_float(row.get("pct")) for row in rows]
    if not values or sum(values) <= 0:
        return "conic-gradient(#7c8ea0 0 100%)"
    total = sum(values)
    cursor = 0.0
    parts: list[str] = []
    for idx, value in enumerate(values[: len(colors)]):
        start = cursor
        cursor += (value / total) * 100.0
        parts.append(f"{colors[idx]} {start:.2f}% {cursor:.2f}%")
    return "conic-gradient(" + ", ".join(parts) + ")"


def _environment(snapshot: Mapping[str, Any]) -> dict[str, str]:
    return {
        "account": _first_text(snapshot.get("account"), snapshot.get("account_name"), "PAPER-INSTITUCIONAL"),
        "environment": _first_text(snapshot.get("environment"), snapshot.get("runtime_mode"), "PAPER / SHADOW"),
        "exchange_paper": _first_text(snapshot.get("exchange_paper"), snapshot.get("exchange"), "BINANCE PAPER"),
        "region": _first_text(snapshot.get("region"), "SAO-1"),
        "dashboard_version": _first_text(snapshot.get("dashboard_version"), snapshot.get("version"), "aba02-visual-v1"),
        "snapshot": SNAPSHOT_PATH,
        "data_source": "Snapshots / Ledger / Read Replica",
    }


def _dashboard_status(snapshot: Mapping[str, Any]) -> str:
    status_summary = snapshot.get("status_summary")
    if isinstance(status_summary, Mapping):
        return _normalize_status(_first_value(status_summary, "status", "component_status"))
    return _normalize_status(_first_value(snapshot, "dashboard_status", "overall_status", "status"))


def _section(snapshot: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    sections = snapshot.get("sections")
    if isinstance(sections, Mapping):
        section = sections.get(name)
        if isinstance(section, Mapping):
            return section
    section = snapshot.get(name)
    return section if isinstance(section, Mapping) else {}


def _status_from(section: Mapping[str, Any]) -> str:
    return _normalize_status(_first_value(section, "status", "health_status", "risk_status"))


def _reconciliation_status(section: Mapping[str, Any]) -> str:
    if _truthy(_first_value(section, "reconciliation_block", "new_entries_blocked")):
        return "blocked"
    value = _first_value(section, "reconciliation_status", "status")
    text = str(value or "UNKNOWN").upper()
    if text in {"OK", "PASS", "PASSED", "VALID", "ALIGNED"}:
        return "ok"
    if text in {"BLOCKED", "ERROR", "FAILED", "DIVERGED"}:
        return "blocked"
    return _status_from(section)


def _reconciliation_label(section: Mapping[str, Any]) -> str:
    value = _first_value(section, "reconciliation_status", "status")
    if value is None and not section:
        return _UNKNOWN
    status = _reconciliation_status(section)
    if status == "ok":
        return "OK"
    if value is None:
        return status_to_label(status)
    return str(value).upper()


def _kill_switch_status(section: Mapping[str, Any]) -> str:
    active = _first_value(section, "kill_switch_active", "kill_switch", "kill_switch_enabled")
    return _status_from_bool(active, true_status="blocked", false_status="ok")


def _kill_switch_label(section: Mapping[str, Any]) -> str:
    active = _first_value(section, "kill_switch_active", "kill_switch", "kill_switch_enabled")
    if active is None:
        return _UNKNOWN
    return "ATIVO" if _truthy(active) else "DESATIVADO"


def _ledger_status(capital: Mapping[str, Any], truth: Mapping[str, Any]) -> str:
    explicit = _first_value(truth, "capital_reservation_ledger_status", "capital_ledger_status")
    if explicit is not None:
        return _text(explicit)
    reserved = _first_value(capital, "capital_reserved")
    return _money(reserved) if reserved is not None else _UNKNOWN


def _status_from_bool(value: Any, *, true_status: str, false_status: str) -> str:
    if value is None:
        return "unknown"
    return true_status if _truthy(value) else false_status

def _status_if_present(value: Any, status: str) -> str | None:
    return status if _has_value(value) else None


def _normalize_status(status: Any) -> str:
    text = str(status or "UNKNOWN").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "ok": "ok",
        "pass": "ok",
        "passed": "ok",
        "valid": "ok",
        "healthy": "ok",
        "online": "ok",
        "info": "info",
        "warning": "warning",
        "warn": "warning",
        "degraded": "warning",
        "monitoring": "monitoring",
        "stale": "stale",
        "critical": "critical",
        "error": "error",
        "failed": "error",
        "blocked": "blocked",
        "missing_required": "blocked",
        "hard_blocked": "hard_blocked",
        "hardblocked": "hard_blocked",
        "unknown": "unknown",
        "missing": "unknown",
        "planned": "planned",
        "disabled": "disabled",
        "neutral": "neutral",
        "readonly": "readonly",
        "read_only": "readonly",
        "paper": "paper",
        "shadow": "shadow",
        "purple": "purple",
    }
    return aliases.get(text, "unknown")


def _first_value(data: Any, *keys: str) -> Any:
    if isinstance(data, Mapping):
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        for value in data.values():
            found = _first_value(value, *keys)
            if found is not None:
                return found
    elif isinstance(data, Sequence) and not isinstance(data, str | bytes):
        for item in data:
            found = _first_value(item, *keys)
            if found is not None:
                return found
    return None


def _first_text(*values: Any) -> str:
    for value in values:
        if value is not None and value != "":
            return str(value)
    return _UNKNOWN


def _text(value: Any) -> str:
    if value is None or value == "":
        return _UNKNOWN
    if isinstance(value, bool):
        return "SIM" if value else "NÃO"
    return str(value)


def _money(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return _UNKNOWN
    return f"{number:,.2f}"


def _short_money(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return _UNKNOWN
    return f"{number:,.0f} USDT"


def _pct(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return _UNKNOWN
    return f"{number:,.2f}%"


def _duration(value: Any) -> str:
    if value is None or value == "":
        return _UNKNOWN
    number = _to_float(value)
    if number is None:
        return str(value)
    seconds = int(number)
    if seconds < 60:
        return f"{seconds}s"
    minutes, rem = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rem}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _yes_no_unknown(value: Any) -> str:
    if value is None:
        return _UNKNOWN
    return "SIM" if _truthy(value) else "NÃO"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "sim", "ativo", "active", "enabled", "blocked"}
    return bool(value)


def _has_value(value: Any) -> bool:
    return value is not None and value != ""


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _number_list(value: Any) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    output: list[float] = []
    for item in value:
        number = _to_float(item)
        if number is not None:
            output.append(number)
    return output


def _format_datetime_utc(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if value is None or value == _UNKNOWN:
        return _UNKNOWN
    text = str(value)
    return text.replace("T", " ").replace("+00:00", "Z")[:19]


if __name__ == "__main__":
    main()

