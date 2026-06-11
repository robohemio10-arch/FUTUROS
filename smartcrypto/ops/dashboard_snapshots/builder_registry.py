from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.active_controls_snapshot_builder import (
    build_active_controls_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.ai_governance_snapshot_builder import (
    build_ai_governance_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.alerts_messaging_snapshot_builder import (
    build_alerts_messaging_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.build_context import DashboardBuildContext
from smartcrypto.ops.dashboard_snapshots.builder_common import SAFETY_FLAGS, iso_utc, json_safe
from smartcrypto.ops.dashboard_snapshots.contracts import (
    DASHBOARD_GLOBAL_STATUS_SCHEMA_VERSION,
    DASHBOARD_SNAPSHOT_BUILD_SUMMARY_SCHEMA_VERSION,
    DashboardAuditContract,
    DashboardPageId,
    DashboardSectionStatus,
)
from smartcrypto.ops.dashboard_snapshots.grid_monitor_snapshot_builder import (
    build_grid_monitor_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.infrastructure_snapshot_builder import (
    build_infrastructure_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.opportunity_scanner_snapshot_builder import (
    build_opportunity_scanner_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.portfolio_risk_snapshot_builder import (
    build_portfolio_risk_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.quantitative_reports_snapshot_builder import (
    build_quantitative_reports_snapshot,
)
from smartcrypto.ops.dashboard_snapshots.source_catalog import (
    DASHBOARD_SNAPSHOT_FILENAMES,
    GLOBAL_STATUS_SNAPSHOT_FILENAME,
    SNAPSHOT_BUILD_SUMMARY_FILENAME,
)
from smartcrypto.ops.dashboard_snapshots.status import normalize_section_status


SnapshotBuilder = Callable[[DashboardBuildContext], dict[str, Any]]

BUILDER_REGISTRY: dict[DashboardPageId, SnapshotBuilder] = {
    DashboardPageId.infrastructure: build_infrastructure_snapshot,
    DashboardPageId.portfolio_risk: build_portfolio_risk_snapshot,
    DashboardPageId.grid_monitor: build_grid_monitor_snapshot,
    DashboardPageId.opportunity_scanner: build_opportunity_scanner_snapshot,
    DashboardPageId.ai_governance: build_ai_governance_snapshot,
    DashboardPageId.active_controls: build_active_controls_snapshot,
    DashboardPageId.quantitative_reports: build_quantitative_reports_snapshot,
    DashboardPageId.alerts_messaging: build_alerts_messaging_snapshot,
}


def build_all_dashboard_snapshots(context: DashboardBuildContext) -> dict[str, Any]:
    started = time.perf_counter()
    started_at = iso_utc(context.now_utc)
    snapshots: dict[DashboardPageId, dict[str, Any]] = {}
    builder_reports: dict[str, Any] = {}
    errors: list[str] = []

    for page_id, builder in BUILDER_REGISTRY.items():
        try:
            snapshot = builder(context)
        except Exception as exc:
            errors.append(f"{page_id.value}:{type(exc).__name__}:{exc}")
            snapshot = _error_snapshot(context, page_id, str(exc))
        snapshots[page_id] = snapshot
        builder_reports[page_id.value] = {
            "status": snapshot.get("status_summary", {}).get("status", "ERROR"),
            "schema_version": snapshot.get("schema_version"),
            "missing_required_sources": snapshot.get("missing_required_sources", []),
            "missing_optional_sources": snapshot.get("missing_optional_sources", []),
            "future_sources_pending": snapshot.get("future_sources_pending", []),
            "errors": snapshot.get("errors", []),
        }

    global_snapshot = build_global_status_snapshot(context, snapshots)
    missing_required = sorted(
        {path for snapshot in snapshots.values() for path in snapshot.get("missing_required_sources", [])}
    )
    missing_optional = sorted(
        {path for snapshot in snapshots.values() for path in snapshot.get("missing_optional_sources", [])}
    )
    future_pending = sorted(
        {path for snapshot in snapshots.values() for path in snapshot.get("future_sources_pending", [])}
    )
    errors.extend(
        error for snapshot in snapshots.values() for error in snapshot.get("errors", [])
    )
    generated_files = [
        DASHBOARD_SNAPSHOT_FILENAMES[page_id] for page_id in DashboardPageId
    ] + [GLOBAL_STATUS_SNAPSHOT_FILENAME, SNAPSHOT_BUILD_SUMMARY_FILENAME]
    status = "error" if errors else "blocked" if context.strict and missing_required else "degraded" if missing_required or missing_optional else "ok"
    summary = {
        "schema_version": DASHBOARD_SNAPSHOT_BUILD_SUMMARY_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "dashboard_name": "SMART FUTUROS Command Center",
        "runtime_mode": context.runtime_mode.value,
        "dashboard_readonly": True,
        "paper_only": True,
        "shadow_only": True,
        "live_locked": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "strict": context.strict,
        "output_dir": context.output_dir.as_posix(),
        "build_started_utc": started_at,
        "build_finished_utc": iso_utc(context.now_utc),
        "last_updated_utc": iso_utc(context.now_utc),
        "elapsed_seconds": max(time.perf_counter() - started, 0.0),
        "status": status,
        "generated_files": generated_files,
        "builders": builder_reports,
        "missing_required_sources": missing_required,
        "missing_optional_sources": missing_optional,
        "future_sources_pending": future_pending,
        "errors": sorted(set(errors)),
        "sections": {},
        "safety": dict(SAFETY_FLAGS),
        "audit": DashboardAuditContract(snapshot_source="dashboard_snapshot_build_summary").to_dict(),
    }

    outputs = {
        DASHBOARD_SNAPSHOT_FILENAMES[page_id]: snapshot
        for page_id, snapshot in snapshots.items()
    }
    outputs[GLOBAL_STATUS_SNAPSHOT_FILENAME] = global_snapshot
    outputs[SNAPSHOT_BUILD_SUMMARY_FILENAME] = summary
    if context.allow_writes_to_output_dir:
        write_snapshot_files(context, outputs)
    return {"summary": summary, "snapshots": outputs, "exit_code": exit_code_for_summary(summary)}


def build_global_status_snapshot(
    context: DashboardBuildContext,
    snapshots: dict[DashboardPageId, dict[str, Any]],
) -> dict[str, Any]:
    page_statuses = {
        page_id.value: snapshot.get("status_summary", {}).get("status", "UNKNOWN")
        for page_id, snapshot in snapshots.items()
    }
    statuses = [normalize_section_status(value) for value in page_statuses.values()]
    if any(status in {DashboardSectionStatus.ERROR, DashboardSectionStatus.BLOCKED, DashboardSectionStatus.MISSING_REQUIRED} for status in statuses):
        overall = DashboardSectionStatus.BLOCKED
    elif any(status in {DashboardSectionStatus.WARNING, DashboardSectionStatus.STALE, DashboardSectionStatus.DEGRADED, DashboardSectionStatus.MISSING_OPTIONAL} for status in statuses):
        overall = DashboardSectionStatus.DEGRADED
    elif statuses and all(status is DashboardSectionStatus.OK for status in statuses):
        overall = DashboardSectionStatus.OK
    else:
        overall = DashboardSectionStatus.UNKNOWN
    missing_required = sorted({path for snapshot in snapshots.values() for path in snapshot.get("missing_required_sources", [])})
    missing_optional = sorted({path for snapshot in snapshots.values() for path in snapshot.get("missing_optional_sources", [])})
    return {
        "schema_version": DASHBOARD_GLOBAL_STATUS_SCHEMA_VERSION,
        "runtime_mode": context.runtime_mode.value,
        "dashboard_readonly": True,
        "paper_only": True,
        "shadow_only": True,
        "live_locked": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "last_updated_utc": iso_utc(context.now_utc),
        "project_name": "SMART FUTUROS",
        "dashboard_name": "SMART FUTUROS Command Center",
        "page_statuses": page_statuses,
        "overall_status": overall.value,
        "blocking_reasons": [f"missing_required_source:{path}" for path in missing_required],
        "missing_required_sources_count": len(missing_required),
        "missing_optional_sources_count": len(missing_optional),
        "generated_snapshot_count": len(snapshots),
        "sections": {"pages": {"status": overall.value, "reason": "consolidated_page_status", "data": page_statuses}},
        "safety": dict(SAFETY_FLAGS),
        "audit": DashboardAuditContract(snapshot_source="dashboard_global_status").to_dict(),
    }


def write_snapshot_files(
    context: DashboardBuildContext,
    outputs: dict[str, dict[str, Any]],
) -> None:
    context.output_dir.mkdir(parents=True, exist_ok=True)
    output_root = context.output_dir.resolve()
    allowed = set(DASHBOARD_SNAPSHOT_FILENAMES.values()) | {
        GLOBAL_STATUS_SNAPSHOT_FILENAME,
        SNAPSHOT_BUILD_SUMMARY_FILENAME,
    }
    for filename, payload in outputs.items():
        if filename not in allowed or Path(filename).name != filename:
            raise ValueError(f"unauthorized_snapshot_filename:{filename}")
        target = (output_root / filename).resolve()
        if target.parent != output_root:
            raise ValueError(f"snapshot_path_outside_output_dir:{target}")
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(json_safe(payload), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)


def exit_code_for_summary(summary: dict[str, Any]) -> int:
    if summary.get("errors"):
        return 3
    if summary.get("strict") and summary.get("missing_required_sources"):
        return 2
    return 0


def _error_snapshot(
    context: DashboardBuildContext,
    page_id: DashboardPageId,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": "invalid",
        "runtime_mode": context.runtime_mode.value,
        "dashboard_readonly": True,
        "paper_only": True,
        "shadow_only": True,
        "live_locked": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "last_updated_utc": iso_utc(context.now_utc),
        "page_id": page_id.value,
        "status_summary": {"status": DashboardSectionStatus.ERROR.value},
        "sections": {},
        "missing_required_sources": [],
        "missing_optional_sources": [],
        "future_sources_pending": [],
        "errors": [reason],
        "safety": dict(SAFETY_FLAGS),
        "audit": DashboardAuditContract(snapshot_source=page_id.value).to_dict(),
    }
