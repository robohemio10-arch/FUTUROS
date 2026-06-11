from __future__ import annotations

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
    ("Estimated Equity", "capital_summary", "estimated_equity_usdt"),
    ("Cash Available", "capital_summary", "cash_available_usdt"),
    ("Cash Locked", "capital_summary", "cash_locked_usdt"),
    ("Inventory Value", "capital_summary", "inventory_value_usdt"),
    ("Capital Deployed", "allocation", "capital_deployed_usdt"),
    ("Free Capital", "allocation", "free_capital_for_entries_usdt"),
    ("Net PnL", "pnl", "net_pnl_usdt"),
    ("Max Drawdown", "drawdown_risk", "max_drawdown_usdt"),
    ("VaR 95", "tail_risk", "var_95_usdt"),
    ("CVaR 95", "tail_risk", "cvar_95_usdt"),
    ("Reconciliation", "financial_truth", "reconciliation_status"),
)


def render_page(snapshot: dict[str, Any], *, ui: Any | None = None) -> None:
    target_ui = ui or get_streamlit()
    inject_smart_futuros_command_center_css(ui=target_ui)
    render_global_topbar(last_updated=snapshot.get("last_updated_utc"), ui=target_ui)
    render_sidebar(ACTIVE_PAGE, {"environment": "paper", "snapshot": SNAPSHOT_PATH}, ui=target_ui)
    render_page_title(PAGE_NUMBER, PAGE_NAME, PAGE_SUBTITLE, ui=target_ui)
    render_snapshot_page(
        title=PAGE_TITLE, snapshot_path=SNAPSHOT_PATH, snapshot=snapshot,
        section_order=REQUIRED_SECTIONS, metric_specs=METRICS, ui=target_ui,
        render_chrome=False,
    )
    render_footer_audit_bar(SNAPSHOT_PATH, ui=target_ui)


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(load_page_snapshot(DashboardPageId.portfolio_risk, project_root=project_root), ui=ui)


if __name__ == "__main__":
    main()
