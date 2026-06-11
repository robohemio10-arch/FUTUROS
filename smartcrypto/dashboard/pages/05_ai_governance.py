from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import get_streamlit, render_snapshot_page
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "05. IA / Qlib Governance"
SNAPSHOT_PATH = "data/reports/dashboard_ai_governance_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_ai_governance_snapshot_v1"
REQUIRED_SECTIONS = (
    "model_state", "qlib_ranking", "shadow_veto", "decision_governance",
    "drift_regime", "shadow_classification_metrics", "reward_research",
    "model_governance", "audit",
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
    render_snapshot_page(
        title=PAGE_TITLE, snapshot_path=SNAPSHOT_PATH, snapshot=snapshot,
        section_order=REQUIRED_SECTIONS, metric_specs=METRICS, ui=ui,
    )


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    (ui or get_streamlit()).info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(load_page_snapshot(DashboardPageId.ai_governance, project_root=project_root), ui=ui)


if __name__ == "__main__":
    main()
