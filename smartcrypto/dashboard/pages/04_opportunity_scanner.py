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


PAGE_TITLE = "04. Oportunidades"
PAGE_NUMBER = "04"
PAGE_NAME = "Oportunidades"
PAGE_SUBTITLE = "Scanner institucional somente leitura; execução real permanece hard-blocked."
ACTIVE_PAGE = "04_opportunity_scanner"
SNAPSHOT_PATH = "data/reports/dashboard_opportunity_scanner_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_opportunity_scanner_snapshot_v1"
REQUIRED_SECTIONS = (
    "status", "spread_scanner", "triangular_arbitrage", "order_flow_imbalance",
    "launch_radar", "opportunity_ranking", "events", "governance", "audit",
)
METRICS = (
    ("Opportunity Scanner", "governance", "opportunity_scanner"),
    ("Real Arbitrage", "governance", "real_arbitrage"),
    ("Real Sniper", "governance", "real_sniper"),
    ("Multi-exchange Live", "governance", "multi_exchange_live"),
    ("Dashboard Can Send Order", "governance", "dashboard_can_send_order"),
    ("Spread bps", "spread_scanner", "spread_bps"),
    ("Opportunity Score", "opportunity_ranking", "opportunity_score"),
)


def render_page(snapshot: dict[str, Any], *, ui: Any | None = None) -> None:
    target_ui = ui or get_streamlit()
    inject_smart_futuros_command_center_css(ui=target_ui)
    render_global_topbar(last_updated=snapshot.get("last_updated_utc"), ui=target_ui)
    render_sidebar(ACTIVE_PAGE, {"environment": "shadow", "snapshot": SNAPSHOT_PATH}, ui=target_ui)
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
    render_page(load_page_snapshot(DashboardPageId.opportunity_scanner, project_root=project_root), ui=ui)


if __name__ == "__main__":
    main()
