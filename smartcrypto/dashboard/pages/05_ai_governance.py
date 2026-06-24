from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.ai_training_research_command_center import (
    render_ai_training_research_command_center,
)
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


PAGE_TITLE = "05. IA / Qlib Governance"
PAGE_NUMBER = "05"
PAGE_NAME = "IA / Qlib Governance"
PAGE_SUBTITLE = "MLOps, challenger governance, drift e atribuição IA Shadow."
ACTIVE_PAGE = "05_ai_governance"
SNAPSHOT_PATH = "data/reports/dashboard_ai_governance_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_ai_governance_snapshot_v1"
REQUIRED_SECTIONS = (
    "model_state", "qlib_ranking", "shadow_veto", "decision_governance",
    "drift_regime", "shadow_classification_metrics", "reward_research",
    "model_governance", "ai_training_research_command_center", "audit",
)
METRICS = (
    ("Qlib Status", "model_state", "qlib_status"),
    ("IA Shadow Status", "model_state", "ai_shadow_status"),
    ("Drift Status", "drift_regime", "drift_status"),
    ("AI Accept Rate", "decision_governance", "ai_accept_rate_pct"),
    ("AI Reject Rate", "decision_governance", "ai_reject_rate_pct"),
    ("Expected Trade Value", "reward_research", "expected_trade_value"),
    ("Promotion Allowed", "model_governance", "promotion_allowed"),
    ("Auto Promotion Allowed", "model_governance", "auto_promotion_allowed"),
    ("RiskManager Authority", "model_governance", "riskmanager_authority"),
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
    render_ai_training_research_command_center(snapshot, ui=target_ui)
    render_footer_audit_bar(SNAPSHOT_PATH, ["Auto-promotion disabled"], ui=target_ui)


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(load_page_snapshot(DashboardPageId.ai_governance, project_root=project_root), ui=ui)


if __name__ == "__main__":
    main()
