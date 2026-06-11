from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import get_streamlit, render_snapshot_page
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "03. Grid Spot Monitor"
SNAPSHOT_PATH = "data/reports/dashboard_grid_monitor_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_grid_monitor_snapshot_v1"
REQUIRED_SECTIONS = (
    "selected_grid", "grid_channel", "grid_density", "dust", "order_book",
    "heatmap", "last_executions", "grid_summary", "integrity", "audit",
)
METRICS = (
    ("Symbol", "selected_grid", "symbol"),
    ("Current Price", "selected_grid", "current_price"),
    ("Grid Center", "grid_channel", "grid_center"),
    ("Lower Price", "grid_channel", "lower_price"),
    ("Upper Price", "grid_channel", "upper_price"),
    ("Active Levels", "grid_density", "active_levels"),
    ("Missing Levels", "grid_density", "missing_levels"),
    ("Duplicate Orders", "integrity", "duplicate_orders"),
    ("Integrity Score", "integrity", "grid_integrity_score"),
    ("Spread bps", "order_book", "spread_bps"),
    ("Dust Value", "dust", "dust_value_usdt"),
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
    render_page(load_page_snapshot(DashboardPageId.grid_monitor, project_root=project_root), ui=ui)


if __name__ == "__main__":
    main()
