from __future__ import annotations

from .contracts import (
    DashboardSemanticPageContract,
    DashboardSemanticRequirement,
    SemanticRequirementSeverity,
)


OFFICIAL_PAGE_CONTRACTS: tuple[DashboardSemanticPageContract, ...] = (
    DashboardSemanticPageContract(
        "01",
        "infrastructure",
        "Infraestrutura",
        "smartcrypto/dashboard/pages/01_infrastructure.py",
        "dashboard_infrastructure_snapshot.json",
        ("Infraestrutura",),
    ),
    DashboardSemanticPageContract(
        "02",
        "portfolio_risk",
        "Portfólio e Risco",
        "smartcrypto/dashboard/pages/02_portfolio_risk.py",
        "dashboard_portfolio_risk_snapshot.json",
        ("Portfólio", "Risco"),
    ),
    DashboardSemanticPageContract(
        "03",
        "grid_monitor",
        "Grid Spot Monitor",
        "smartcrypto/dashboard/pages/03_grid_monitor.py",
        "dashboard_grid_monitor_snapshot.json",
        ("Grid", "Monitor"),
    ),
    DashboardSemanticPageContract(
        "04",
        "opportunity_scanner",
        "Oportunidades",
        "smartcrypto/dashboard/pages/04_opportunity_scanner.py",
        "dashboard_opportunity_scanner_snapshot.json",
        ("Oportunidades",),
    ),
    DashboardSemanticPageContract(
        "05",
        "ai_governance",
        "IA / Qlib Governance",
        "smartcrypto/dashboard/pages/05_ai_governance.py",
        "dashboard_ai_governance_snapshot.json",
        ("IA", "Qlib"),
    ),
    DashboardSemanticPageContract(
        "06",
        "active_controls",
        "Controles Ativos",
        "smartcrypto/dashboard/pages/06_active_controls.py",
        "dashboard_active_controls_snapshot.json",
        ("render_readiness_gates_snapshot_view", "render_n4_hard_block_panel"),
    ),
    DashboardSemanticPageContract(
        "07",
        "quantitative_reports",
        "Relatórios Quantitativos & TCA",
        "smartcrypto/dashboard/pages/07_quantitative_reports.py",
        "dashboard_quantitative_reports_snapshot.json",
        (
            "render_financial_event_log_decision_trace",
            "render_dataset_ocr_training_pipeline_status",
        ),
    ),
    DashboardSemanticPageContract(
        "08",
        "alerts_messaging",
        "Alertas & Mensageria",
        "smartcrypto/dashboard/pages/08_alerts_messaging.py",
        "dashboard_alerts_messaging_snapshot.json",
        ("render_notification_stub_only_banner", "evaluate_notification_intent"),
    ),
)

_COMMON_PAGE_UI_TERMS = (
    "inject_smart_futuros_command_center_css",
    "render_global_topbar",
    "render_sidebar",
    "render_page_title",
    "render_footer_audit_bar",
)

REQUIRED_UI_FILES: tuple[str, ...] = (
    "smartcrypto/dashboard/assets/futuros_command_center.css",
    "smartcrypto/dashboard/ui/__init__.py",
    "smartcrypto/dashboard/ui/badges.py",
    "smartcrypto/dashboard/ui/cards.py",
    "smartcrypto/dashboard/ui/charts.py",
    "smartcrypto/dashboard/ui/footer.py",
    "smartcrypto/dashboard/ui/layout.py",
    "smartcrypto/dashboard/ui/sections.py",
    "smartcrypto/dashboard/ui/sidebar.py",
    "smartcrypto/dashboard/ui/states.py",
    "smartcrypto/dashboard/ui/status.py",
    "smartcrypto/dashboard/ui/tables.py",
    "smartcrypto/dashboard/ui/theme.py",
    "smartcrypto/dashboard/ui/tokens.py",
)

REQUIRED_STUB_FILES: tuple[str, ...] = (
    "smartcrypto/dashboard/controls/contracts.py",
    "smartcrypto/dashboard/controls/command_classifier.py",
    "smartcrypto/dashboard/controls/command_stub_adapter.py",
    "smartcrypto/dashboard/controls/policies.py",
    "smartcrypto/dashboard/alerts/contracts.py",
    "smartcrypto/dashboard/alerts/notification_stub_dispatcher.py",
    "smartcrypto/dashboard/alerts/routing.py",
    "smartcrypto/dashboard/components/readiness_gates.py",
    "smartcrypto/dashboard/components/decision_trace.py",
    "smartcrypto/dashboard/components/dataset_pipeline.py",
)

SEMANTIC_REQUIREMENTS: tuple[DashboardSemanticRequirement, ...] = (
    DashboardSemanticRequirement(
        "official_eight_pages",
        "The dashboard must keep exactly the eight canonical SMART FUTUROS pages.",
        paths=tuple(page.page_path for page in OFFICIAL_PAGE_CONTRACTS),
    ),
    DashboardSemanticRequirement(
        "app_brand_and_navigation",
        "The Streamlit shell must expose SMART FUTUROS Command Center and all eight page links.",
        paths=("smartcrypto/dashboard/app.py",),
        required_terms=(
            "SMART FUTUROS Command Center",
            "SMART FUTUROS Institutional Dashboard",
            "01_infrastructure.py",
            "02_portfolio_risk.py",
            "03_grid_monitor.py",
            "04_opportunity_scanner.py",
            "05_ai_governance.py",
            "06_active_controls.py",
            "07_quantitative_reports.py",
            "08_alerts_messaging.py",
        ),
    ),
    DashboardSemanticRequirement(
        "snapshot_catalog_alignment",
        "The source catalog must declare the eight canonical dashboard snapshots.",
        paths=("smartcrypto/ops/dashboard_snapshots/source_catalog.py",),
        required_terms=tuple(page.snapshot_filename for page in OFFICIAL_PAGE_CONTRACTS),
    ),
    DashboardSemanticRequirement(
        "institutional_theme_assets",
        "The visual theme must be local-only and available to all pages.",
        paths=REQUIRED_UI_FILES,
        required_terms=("SMART FUTUROS", "Command Center"),
    ),
    DashboardSemanticRequirement(
        "controls_and_alerts_stubs",
        "Branch 04 governance stubs must remain present after visual theming.",
        paths=REQUIRED_STUB_FILES,
        required_terms=(
            "N4_HARD_BLOCKED",
            "DRY_RUN_ACCEPTED",
            "DRY_RUN_REJECTED",
            "NotificationDispatchStatus",
            "render_readiness_gates_snapshot_view",
            "render_financial_event_log_decision_trace",
            "render_dataset_ocr_training_pipeline_status",
        ),
    ),
    DashboardSemanticRequirement(
        "active_controls_readiness_gates",
        "Aba 6 must explicitly render Readiness & Gates and hard-blocked control semantics.",
        paths=(
            "smartcrypto/dashboard/pages/06_active_controls.py",
            "smartcrypto/dashboard/components/readiness_gates.py",
        ),
        required_terms=(
            "Readiness & Gates",
            "canary_release_allowed",
            "live_release_allowed",
            "manual_go_no_go_required",
            "render_n4_hard_block_panel",
        ),
    ),
    DashboardSemanticRequirement(
        "quant_reports_decision_trace_dataset_pipeline",
        "Aba 7 must explicitly render Decision Trace and Dataset/OCR/Training Pipeline status.",
        paths=(
            "smartcrypto/dashboard/pages/07_quantitative_reports.py",
            "smartcrypto/dashboard/components/decision_trace.py",
            "smartcrypto/dashboard/components/dataset_pipeline.py",
        ),
        required_terms=(
            "Financial Event Log / Decision Trace",
            "Dataset / OCR / Training Pipeline Status",
            "correlation_id",
            "reconciliation_status",
            "quality_gated_rows",
            "sqlite_missing_count",
            "sqlite_extra_count",
        ),
    ),
    DashboardSemanticRequirement(
        "alerts_messaging_stub_only",
        "Aba 8 must render alert routing and notification dispatch as dry-run stubs only.",
        paths=(
            "smartcrypto/dashboard/pages/08_alerts_messaging.py",
            "smartcrypto/dashboard/alerts/notification_stub_dispatcher.py",
            "smartcrypto/dashboard/alerts/routing.py",
        ),
        required_terms=(
            "dashboard_alerts_messaging_snapshot.json",
            "render_notification_stub_only_banner",
            "delivery_attempted",
            "False",
            "NotificationChannel.NTFY",
            "NotificationChannel.OPERATOR_REQUIRED",
        ),
    ),
    DashboardSemanticRequirement(
        "safety_language_and_flags",
        "Dashboard safety language must keep paper/shadow, read-only, live-locked and disabled-order semantics visible.",
        paths=(
            "smartcrypto/dashboard/components/read_only.py",
            "smartcrypto/dashboard/ui/badges.py",
            "smartcrypto/dashboard/ui/footer.py",
        ),
        required_terms=(
            "PAPER / SHADOW ONLY",
            "LIVE LOCKED",
            "ORDER SUBMISSION DISABLED",
            "RISKMANAGER AUTHORITY",
            "Dashboard Read-only",
        ),
    ),
    DashboardSemanticRequirement(
        "documentation_closeout",
        "Dashboard docs must document the five completed dashboard branches and visual/stub constraints.",
        paths=(
            "docs/SMART_FUTUROS_COMMAND_CENTER_V2_CONTROLS_ALERTS_STUBS.md",
            "docs/SMART_FUTUROS_COMMAND_CENTER_THEME_V1.md",
        ),
        required_terms=(
            "SMART FUTUROS",
            "N4",
            "HARD_BLOCKED",
            "Telegram",
            "NTFY",
            "visual",
            "read-only",
        ),
    ),
)

PAGE_UI_TERMS_BY_PATH = {
    page.page_path: (*_COMMON_PAGE_UI_TERMS, page.snapshot_filename, *page.required_terms)
    for page in OFFICIAL_PAGE_CONTRACTS
}

REQUIRED_ABSENT_TEXT_TERMS: tuple[str, ...] = (
    "dashboard_alerts_" + "queue_snapshot.json",
)

HISTORICAL_EXTERNAL_BRAND = "Black" + "Rock"
