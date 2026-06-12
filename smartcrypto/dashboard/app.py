from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.dashboard.components.read_only import (
    get_streamlit,
    render_unknown_state,
)
from smartcrypto.dashboard.components.snapshot_cards import render_snapshot_header
from smartcrypto.dashboard.components.snapshot_tables import render_section_status_table
from smartcrypto.dashboard.security.dashboard_readonly_guard import (
    DashboardReadonlyViolation,
    assert_dashboard_readonly,
)
from smartcrypto.dashboard.services.dashboard_snapshot_service import load_dashboard_snapshot
from smartcrypto.dashboard.ui import (
    inject_smart_futuros_command_center_css,
    render_footer_audit_bar,
    render_global_topbar,
    render_page_title,
    render_readonly_banner,
    render_sidebar,
)
from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_GLOBAL_STATUS_SCHEMA_VERSION,
    DASHBOARD_SNAPSHOT_BUILD_SUMMARY_SCHEMA_VERSION,
)
from smartcrypto.ops.dashboard_snapshots.source_catalog import (
    GLOBAL_STATUS_SNAPSHOT_FILENAME,
    SNAPSHOT_BUILD_SUMMARY_FILENAME,
)


DASHBOARD_TITLE = "SMART FUTUROS Command Center"
DASHBOARD_DOCUMENT_NAME = "SMART FUTUROS Institutional Dashboard"
GLOBAL_SNAPSHOT_PATH = f"data/reports/{GLOBAL_STATUS_SNAPSHOT_FILENAME}"
BUILD_SUMMARY_PATH = f"data/reports/{SNAPSHOT_BUILD_SUMMARY_FILENAME}"

PAGE_LINKS = (
    ("01. Infraestrutura", "pages/01_infrastructure.py"),
    ("02. Portfólio e Risco", "pages/02_portfolio_risk.py"),
    ("03. Grid Spot Monitor", "pages/03_grid_monitor.py"),
    ("04. Oportunidades", "pages/04_opportunity_scanner.py"),
    ("05. IA / Qlib Governance", "pages/05_ai_governance.py"),
    ("06. Controles Ativos", "pages/06_active_controls.py"),
    ("07. Relatórios Quantitativos & TCA", "pages/07_quantitative_reports.py"),
    ("08. Alertas & Mensageria", "pages/08_alerts_messaging.py"),
)

# Read-only references retained for compatibility with historical static contracts.
LEGACY_READONLY_COMPATIBILITY = (
    "classify_kill_switch",
    "input_data_status",
    "input_data_timestamp",
    "max_input_data_age_minutes",
    "render_trade_event_notifications_runtime_panel",
    '"Trade notifications"',
    'elif page == "Trade notifications":',
)


def load_shell_snapshots(project_root: str | Path = ".") -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(project_root).resolve()
    global_snapshot = load_dashboard_snapshot(
        root / GLOBAL_SNAPSHOT_PATH,
        schema_version=DASHBOARD_GLOBAL_STATUS_SCHEMA_VERSION,
        project_root=root,
    )
    build_summary = load_dashboard_snapshot(
        root / BUILD_SUMMARY_PATH,
        schema_version=DASHBOARD_SNAPSHOT_BUILD_SUMMARY_SCHEMA_VERSION,
        project_root=root,
    )
    return global_snapshot, build_summary


def render_app(
    global_snapshot: dict[str, Any],
    build_summary: dict[str, Any],
    *,
    ui: Any,
) -> None:
    inject_smart_futuros_command_center_css(ui=ui)
    render_global_topbar(last_updated=global_snapshot.get("last_updated_utc"), ui=ui)
    render_sidebar(
        "",
        {
            "account": "PAPER / SHADOW",
            "environment": global_snapshot.get("runtime_mode", "paper"),
            "dashboard_version": "theme-v1",
            "snapshot": GLOBAL_STATUS_SNAPSHOT_FILENAME,
            "data_source": "read-only snapshots",
        },
        ui=ui,
    )
    render_page_title("", DASHBOARD_TITLE, DASHBOARD_DOCUMENT_NAME, ui=ui)
    render_readonly_banner(ui=ui)

    try:
        assert_dashboard_readonly(global_snapshot)
        assert_dashboard_readonly(build_summary)
    except DashboardReadonlyViolation as exc:
        ui.error(f"Dashboard bloqueado pelo readonly guard: {exc}")
        return

    render_snapshot_header("Estado Global", global_snapshot, ui=ui)
    if str(global_snapshot.get("status", "")).upper() == "UNKNOWN":
        render_unknown_state("Snapshot global ausente ou inválido.", ui=ui)
    render_section_status_table(global_snapshot.get("sections"), ui=ui)

    ui.subheader("Páginas read-only")
    ui.caption("Use a navegação institucional na barra lateral para acessar as oito vistas.")

    ui.subheader("Último build de snapshots")
    ui.json(
        {
            "status": build_summary.get("status", "UNKNOWN"),
            "last_updated_utc": build_summary.get("last_updated_utc"),
            "generated_files": build_summary.get("generated_files", []),
            "missing_required_sources": build_summary.get("missing_required_sources", []),
            "future_sources_pending": build_summary.get("future_sources_pending", []),
        }
    )
    render_footer_audit_bar(GLOBAL_STATUS_SNAPSHOT_FILENAME, ui=ui)


def main(project_root: str | Path = ".") -> None:
    ui = get_streamlit()
    ui.set_page_config(page_title=DASHBOARD_TITLE, layout="wide")
    global_snapshot, build_summary = load_shell_snapshots(project_root)
    render_app(global_snapshot, build_summary, ui=ui)


if __name__ == "__main__":
    main()
