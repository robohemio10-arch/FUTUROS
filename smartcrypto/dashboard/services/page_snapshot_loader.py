from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smartcrypto.dashboard.security.dashboard_readonly_guard import (
    DashboardReadonlyViolation,
    assert_dashboard_readonly,
)
from smartcrypto.dashboard.services.dashboard_snapshot_service import (
    build_unknown_snapshot,
    load_dashboard_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_ACTIVE_CONTROLS_SCHEMA_VERSION,
    DASHBOARD_AI_GOVERNANCE_SCHEMA_VERSION,
    DASHBOARD_ALERTS_MESSAGING_SCHEMA_VERSION,
    DASHBOARD_GRID_MONITOR_SCHEMA_VERSION,
    DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION,
    DASHBOARD_OPPORTUNITY_SCANNER_SCHEMA_VERSION,
    DASHBOARD_PORTFOLIO_RISK_SCHEMA_VERSION,
    DASHBOARD_QUANTITATIVE_REPORTS_SCHEMA_VERSION,
    DashboardPageId,
)
from smartcrypto.ops.dashboard_snapshots.source_catalog import DASHBOARD_SNAPSHOT_FILENAMES


@dataclass(frozen=True)
class PageSnapshotSpec:
    page_id: DashboardPageId
    filename: str
    schema_version: str

    def relative_path(self) -> Path:
        return Path("data") / "reports" / self.filename


PAGE_SNAPSHOT_SPECS = {
    DashboardPageId.infrastructure: PageSnapshotSpec(
        DashboardPageId.infrastructure,
        DASHBOARD_SNAPSHOT_FILENAMES[DashboardPageId.infrastructure],
        DASHBOARD_INFRASTRUCTURE_SCHEMA_VERSION,
    ),
    DashboardPageId.portfolio_risk: PageSnapshotSpec(
        DashboardPageId.portfolio_risk,
        DASHBOARD_SNAPSHOT_FILENAMES[DashboardPageId.portfolio_risk],
        DASHBOARD_PORTFOLIO_RISK_SCHEMA_VERSION,
    ),
    DashboardPageId.grid_monitor: PageSnapshotSpec(
        DashboardPageId.grid_monitor,
        DASHBOARD_SNAPSHOT_FILENAMES[DashboardPageId.grid_monitor],
        DASHBOARD_GRID_MONITOR_SCHEMA_VERSION,
    ),
    DashboardPageId.opportunity_scanner: PageSnapshotSpec(
        DashboardPageId.opportunity_scanner,
        DASHBOARD_SNAPSHOT_FILENAMES[DashboardPageId.opportunity_scanner],
        DASHBOARD_OPPORTUNITY_SCANNER_SCHEMA_VERSION,
    ),
    DashboardPageId.ai_governance: PageSnapshotSpec(
        DashboardPageId.ai_governance,
        DASHBOARD_SNAPSHOT_FILENAMES[DashboardPageId.ai_governance],
        DASHBOARD_AI_GOVERNANCE_SCHEMA_VERSION,
    ),
    DashboardPageId.active_controls: PageSnapshotSpec(
        DashboardPageId.active_controls,
        DASHBOARD_SNAPSHOT_FILENAMES[DashboardPageId.active_controls],
        DASHBOARD_ACTIVE_CONTROLS_SCHEMA_VERSION,
    ),
    DashboardPageId.quantitative_reports: PageSnapshotSpec(
        DashboardPageId.quantitative_reports,
        DASHBOARD_SNAPSHOT_FILENAMES[DashboardPageId.quantitative_reports],
        DASHBOARD_QUANTITATIVE_REPORTS_SCHEMA_VERSION,
    ),
    DashboardPageId.alerts_messaging: PageSnapshotSpec(
        DashboardPageId.alerts_messaging,
        DASHBOARD_SNAPSHOT_FILENAMES[DashboardPageId.alerts_messaging],
        DASHBOARD_ALERTS_MESSAGING_SCHEMA_VERSION,
    ),
}


def load_page_snapshot(
    page_id: DashboardPageId | str,
    *,
    project_root: str | Path = ".",
) -> dict[str, Any]:
    normalized = page_id if isinstance(page_id, DashboardPageId) else DashboardPageId(page_id)
    spec = PAGE_SNAPSHOT_SPECS[normalized]
    target = Path(project_root).resolve() / spec.relative_path()
    snapshot = load_dashboard_snapshot(
        target,
        schema_version=spec.schema_version,
        project_root=project_root,
    )
    try:
        assert_dashboard_readonly(snapshot)
    except DashboardReadonlyViolation as exc:
        return build_unknown_snapshot(
            spec.filename,
            f"readonly_guard_blocked:{exc}",
            schema_version=spec.schema_version,
        )
    return snapshot
