from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import get_streamlit, render_snapshot_page
from smartcrypto.dashboard.components.dataset_pipeline import (
    render_dataset_ocr_training_pipeline_status,
)
from smartcrypto.dashboard.components.decision_trace import (
    render_financial_event_log_decision_trace,
)
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.dashboard.ui import (
    inject_smart_futuros_command_center_css,
    render_chart_placeholder,
    render_footer_audit_bar,
    render_global_topbar,
    render_page_title,
    render_sidebar,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "07. Relatórios Quantitativos & TCA"
PAGE_NUMBER = "07"
PAGE_NAME = "Relatórios Quantitativos & TCA"
PAGE_SUBTITLE = "Performance, risco ajustado, custos de execução e trilha decisória."
ACTIVE_PAGE = "07_quantitative_reports"
SNAPSHOT_PATH = "data/reports/dashboard_quantitative_reports_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_quantitative_reports_snapshot_v1"
REQUIRED_SECTIONS = (
    "periods", "performance", "risk_adjusted_metrics", "operational_metrics", "tca",
    "regime_comparison", "asset_comparison", "exports", "institutional_score", "audit",
)
METRICS = (
    ("Net PnL", "performance", "net_pnl_usdt"),
    ("Sharpe", "risk_adjusted_metrics", "sharpe"),
    ("Sortino", "risk_adjusted_metrics", "sortino"),
    ("Calmar", "risk_adjusted_metrics", "calmar"),
    ("Max Drawdown", "performance", "max_drawdown_usdt"),
    ("Profit Factor", "performance", "profit_factor"),
    ("Expectancy Net", "performance", "expectancy_net"),
    ("VaR 95", "risk_adjusted_metrics", "var_95"),
    ("CVaR 95", "risk_adjusted_metrics", "cvar_95"),
    ("Total TCA Cost", "tca", "total_tca_cost_usdt"),
    ("Cost to Alpha Ratio", "tca", "cost_to_alpha_ratio"),
    ("Institutional Score", "institutional_score", "score"),
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
    target_ui.markdown(
        render_chart_placeholder("Equity Curve / Drawdown", "Série temporal indisponível no snapshot"),
        unsafe_allow_html=True,
    )
    render_financial_event_log_decision_trace(snapshot, ui=target_ui)
    render_dataset_ocr_training_pipeline_status(snapshot, ui=target_ui)
    render_footer_audit_bar(SNAPSHOT_PATH, ["TCA read-only"], ui=target_ui)


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(load_page_snapshot(DashboardPageId.quantitative_reports, project_root=project_root), ui=ui)


if __name__ == "__main__":
    main()
