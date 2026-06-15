from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import get_streamlit, render_snapshot_page
from smartcrypto.dashboard.components.runtime_blockers_remediation import (
    render_runtime_blockers_remediation,
)
from smartcrypto.dashboard.components.runtime_blockers_operator_pack import (
    render_runtime_blockers_operator_pack,
)
from smartcrypto.dashboard.components.runtime_blockers_closeout_evidence import (
    render_runtime_blockers_closeout_evidence,
)
from smartcrypto.dashboard.components.runtime_evidence_freshness_remediation_producers import (
    render_runtime_evidence_freshness_remediation_producers,
)
from smartcrypto.dashboard.components.runtime_freshness_producer_contracts import (
    render_runtime_freshness_producer_contracts,
)
from smartcrypto.dashboard.components.runtime_freshness_producer_entrypoint_static_safety import (
    render_runtime_freshness_producer_entrypoint_static_safety,
)
from smartcrypto.dashboard.components.runtime_freshness_post_refresh_evidence_gate import (
    render_runtime_freshness_post_refresh_evidence_gate,
)
from smartcrypto.dashboard.components.runtime_evidence_panel import render_runtime_evidence_panel
from smartcrypto.dashboard.components.runtime_source_health import render_runtime_source_health
from smartcrypto.dashboard.services.page_snapshot_loader import load_page_snapshot
from smartcrypto.dashboard.ui import (
    inject_smart_futuros_command_center_css,
    render_footer_audit_bar,
    render_global_topbar,
    render_page_title,
    render_sidebar,
)
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardPageId


PAGE_TITLE = "01. Infraestrutura"
PAGE_NUMBER = "01"
PAGE_NAME = "Infraestrutura"
PAGE_SUBTITLE = "Saúde operacional, conectividade e evidências do runtime paper."
ACTIVE_PAGE = "01_infrastructure"
SNAPSHOT_PATH = "data/reports/dashboard_infrastructure_snapshot.json"
EXPECTED_SCHEMA_VERSION = "dashboard_infrastructure_snapshot_v1"
REQUIRED_SECTIONS = (
    "status_summary",
    "host",
    "docker",
    "redis",
    "latency",
    "websockets",
    "rate_limits",
    "market_data_health",
    "runtime_evidence_integration",
    "runtime_blockers_remediation",
    "runtime_blockers_operator_pack",
    "runtime_blockers_closeout_evidence",
    "runtime_evidence_freshness_remediation_producers",
    "runtime_freshness_producer_contracts",
    "runtime_freshness_producer_entrypoint_static_safety",
    "runtime_freshness_post_refresh_evidence_gate",
    "events",
    "runtime_source_health",
    "audit",
)
METRICS = (
    ("Runtime Mode", "status_summary", "component_status"),
    ("Redis Status", "redis", "status"),
    ("WebSocket Status", "websockets", "stale_ws"),
    ("Latency p50", "latency", "latency_p50_ms"),
    ("Latency p90", "latency", "latency_p90_ms"),
    ("Latency p99", "latency", "latency_p99_ms"),
    ("Rate Limit Used", "rate_limits", "api_weight_pct"),
    ("Market Data Age", "market_data_health", "data_age_seconds"),
)


def render_page(snapshot: dict[str, Any], *, ui: Any | None = None) -> None:
    target_ui = ui or get_streamlit()
    inject_smart_futuros_command_center_css(ui=target_ui)
    render_global_topbar(last_updated=snapshot.get("last_updated_utc"), ui=target_ui)
    render_sidebar(ACTIVE_PAGE, _environment(snapshot), ui=target_ui)
    render_page_title(PAGE_NUMBER, PAGE_NAME, PAGE_SUBTITLE, ui=target_ui)
    render_snapshot_page(
        title=PAGE_TITLE,
        snapshot_path=SNAPSHOT_PATH,
        snapshot=snapshot,
        section_order=REQUIRED_SECTIONS,
        metric_specs=METRICS,
        ui=target_ui,
        render_chrome=False,
    )
    render_runtime_evidence_panel(snapshot, ui=target_ui)
    render_runtime_blockers_remediation(snapshot, ui=target_ui)
    render_runtime_blockers_operator_pack(snapshot, ui=target_ui)
    render_runtime_blockers_closeout_evidence(snapshot, ui=target_ui)
    render_runtime_evidence_freshness_remediation_producers(snapshot, ui=target_ui)
    render_runtime_freshness_producer_contracts(snapshot, ui=target_ui)
    render_runtime_freshness_producer_entrypoint_static_safety(snapshot, ui=target_ui)
    render_runtime_freshness_post_refresh_evidence_gate(snapshot, ui=target_ui)
    render_runtime_source_health(snapshot, ui=target_ui)
    render_footer_audit_bar(SNAPSHOT_PATH, ui=target_ui)


def _environment(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "account": "PAPER / SHADOW",
        "environment": snapshot.get("runtime_mode", "paper"),
        "dashboard_version": "theme-v1",
        "snapshot": SNAPSHOT_PATH,
        "data_source": "read-only snapshot",
    }


def render_missing_snapshot(reason: str, *, ui: Any | None = None) -> None:
    target_ui = ui or get_streamlit()
    target_ui.info(f"UNKNOWN: {reason}")


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=PAGE_TITLE, layout="wide")
    render_page(load_page_snapshot(DashboardPageId.infrastructure, project_root=project_root), ui=ui)


if __name__ == "__main__":
    main()
