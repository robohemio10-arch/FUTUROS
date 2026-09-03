"""Read-only projection of the W13 AIBOT-Parity snapshot into W12 dashboard pages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from smartcrypto.ops.dashboard_snapshots.builder_common import first_payload, section
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardSectionStatus

AIBOT_PARITY_REPORT_PATH = (
    "data/reports/aibot_parity/aibot_parity_e2e_snapshot_v1.json"
)
AIBOT_PARITY_SOURCE_KEY = "aibot_parity_e2e_snapshot_v1"
ProjectionKey = Literal[
    "opportunity_scanner",
    "ai_governance",
    "quantitative_reports",
]


def build_aibot_parity_dashboard_section(
    sources: Mapping[str, Any],
    projection_key: ProjectionKey,
) -> dict[str, Any]:
    payload = first_payload(sources, AIBOT_PARITY_SOURCE_KEY)
    if not isinstance(payload, Mapping) or not payload:
        return section(
            DashboardSectionStatus.UNKNOWN,
            "aibot_parity_e2e_snapshot_missing",
            cycle_id=None,
            projection_key=projection_key,
            operational_authority=False,
            writes_active_signals=False,
            signal_published=False,
            riskmanager_final_authority=True,
        )

    dashboard = payload.get("dashboard")
    projection = (
        dashboard.get(projection_key)
        if isinstance(dashboard, Mapping)
        else None
    )
    if not isinstance(projection, Mapping):
        return section(
            DashboardSectionStatus.UNKNOWN,
            "aibot_parity_projection_missing",
            cycle_id=payload.get("cycle_id"),
            projection_key=projection_key,
            operational_authority=False,
            writes_active_signals=False,
            signal_published=False,
            riskmanager_final_authority=True,
        )

    normalized = dict(projection)
    projection_status = normalized.pop("status", None)
    normalized["projection_status"] = projection_status
    normalized["cycle_id"] = payload.get("cycle_id", normalized.get("cycle_id"))
    normalized["pipeline_status"] = payload.get("status", projection_status)
    normalized["pipeline_reason"] = payload.get("reason")
    normalized["qlib_status"] = payload.get(
        "qlib_status", normalized.get("qlib_status", "BLOCKED_EXTERNAL")
    )
    normalized["operational_authority"] = False
    normalized["writes_active_signals"] = False
    normalized["signal_published"] = False
    normalized["riskmanager_final_authority"] = True
    return section(
        _dashboard_status(projection_status or payload.get("status")),
        "aibot_parity_e2e_readonly_projection",
        **normalized,
    )


def _dashboard_status(value: Any) -> DashboardSectionStatus:
    normalized = str(value or "UNKNOWN").upper()
    if normalized in {"READY_SHADOW", "READY", "SUCCESS", "OK"}:
        return DashboardSectionStatus.OK
    if normalized in {"ABSTAIN", "PARTIAL", "WARNING", "DEGRADED"}:
        return DashboardSectionStatus.WARNING
    if normalized in {"BLOCKED", "FAILED", "ERROR", "HARD_BLOCKED"}:
        return DashboardSectionStatus.BLOCKED
    return DashboardSectionStatus.UNKNOWN
