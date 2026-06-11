from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import get_streamlit, render_snapshot_page
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "07. Relatórios Quantitativos & TCA"
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
    render_snapshot_page(
        title=PAGE_TITLE, snapshot_path=SNAPSHOT_PATH, snapshot=snapshot,
        section_order=REQUIRED_SECTIONS, metric_specs=METRICS, ui=ui,
    )


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(load_page_snapshot(DashboardPageId.quantitative_reports, project_root=project_root), ui=ui)


if __name__ == "__main__":
    main()
