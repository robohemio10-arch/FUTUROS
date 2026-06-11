"""Read-only snapshot contracts for the SMART FUTUROS Command Center."""

from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_ACTIVE_CONTROLS_SCHEMA_VERSION,
    DASHBOARD_AI_GOVERNANCE_SCHEMA_VERSION,
    DASHBOARD_ALERTS_MESSAGING_SCHEMA_VERSION,
    DASHBOARD_GLOBAL_STATUS_SCHEMA_VERSION,
    DASHBOARD_GRID_MONITOR_SCHEMA_VERSION,
    DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION,
    DASHBOARD_OPPORTUNITY_SCANNER_SCHEMA_VERSION,
    DASHBOARD_PORTFOLIO_RISK_SCHEMA_VERSION,
    DASHBOARD_QUANTITATIVE_REPORTS_SCHEMA_VERSION,
    DASHBOARD_SNAPSHOT_BUILD_SUMMARY_SCHEMA_VERSION,
    DashboardAuditContract,
    DashboardPageId,
    DashboardSectionContract,
    DashboardSectionStatus,
    DashboardSnapshotBase,
    HardBlockStatus,
    RuntimeMode,
    SourceKind,
)

__all__ = [
    "DASHBOARD_ACTIVE_CONTROLS_SCHEMA_VERSION",
    "DASHBOARD_AI_GOVERNANCE_SCHEMA_VERSION",
    "DASHBOARD_ALERTS_MESSAGING_SCHEMA_VERSION",
    "DASHBOARD_GLOBAL_STATUS_SCHEMA_VERSION",
    "DASHBOARD_GRID_MONITOR_SCHEMA_VERSION",
    "DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION",
    "DASHBOARD_OPPORTUNITY_SCANNER_SCHEMA_VERSION",
    "DASHBOARD_PORTFOLIO_RISK_SCHEMA_VERSION",
    "DASHBOARD_QUANTITATIVE_REPORTS_SCHEMA_VERSION",
    "DASHBOARD_SNAPSHOT_BUILD_SUMMARY_SCHEMA_VERSION",
    "DashboardAuditContract",
    "DashboardPageId",
    "DashboardSectionContract",
    "DashboardSectionStatus",
    "DashboardSnapshotBase",
    "HardBlockStatus",
    "RuntimeMode",
    "SourceKind",
]


def build_all_dashboard_snapshots(context):
    from smartcrypto.ops.dashboard_snapshots.builder_registry import (
        build_all_dashboard_snapshots as _build,
    )

    return _build(context)


__all__.append("build_all_dashboard_snapshots")
