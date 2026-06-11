from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import (
    get_streamlit,
    render_disabled_control_stub,
    render_snapshot_page,
)
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "06. Controles Ativos"
SNAPSHOT_PATH = "data/reports/dashboard_active_controls_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_active_controls_snapshot_v1"
REQUIRED_SECTIONS = (
    "active_layer_status", "level1_commands", "level2_commands", "level3_commands",
    "level4_hard_blocks", "kill_switch", "grid_parameter_change", "security_state",
    "command_events", "audit",
)
LEVEL4_ALWAYS_BLOCKED = (
    "LIVE_ORDER", "MARKET_SELL_ALL_REAL", "SNIPER_REAL", "CANCEL_ALL_LIVE_ORDERS",
    "LIQUIDATE_REAL_INVENTORY", "CHANGE_LIVE_RISK", "ENABLE_LIVE_TRADING",
    "ENABLE_PRIVATE_READ_REAL", "PROMOTE_MODEL_TO_PRODUCTION", "AUTO_INCREASE_CAPITAL",
    "RELEASE_REAL_SAFETY_ORDER",
)
METRICS = (
    ("Execution Enabled", "active_layer_status", "command_execution_enabled"),
    ("Kill Switch Active", "kill_switch", "global_kill_switch_active"),
    ("RiskManager Authority", "security_state", "riskmanager_authority"),
    ("Live Authority", "security_state", "live_authority"),
)


def render_page(snapshot: dict[str, Any], *, ui: Any | None = None) -> None:
    target_ui = ui or get_streamlit()
    render_snapshot_page(
        title=PAGE_TITLE, snapshot_path=SNAPSHOT_PATH, snapshot=snapshot,
        section_order=REQUIRED_SECTIONS, metric_specs=METRICS, ui=target_ui,
    )
    target_ui.subheader("Controles governados")
    render_disabled_control_stub("N2", "DRY-RUN/STUB FUTURO", ui=target_ui)
    render_disabled_control_stub("N3", "DRY-RUN/STUB FUTURO", ui=target_ui)
    for command in LEVEL4_ALWAYS_BLOCKED:
        render_disabled_control_stub(command, "HARD_BLOCKED", ui=target_ui)


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(load_page_snapshot(DashboardPageId.active_controls, project_root=project_root), ui=ui)


if __name__ == "__main__":
    main()
