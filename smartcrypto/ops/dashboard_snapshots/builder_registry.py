from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping

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
from smartcrypto.ops.dashboard_snapshots.runtime_evidence_integration import (
    build_runtime_evidence_view,
    runtime_evidence_section_status,
)
from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import (
    build_runtime_blockers_remediation,
)
from smartcrypto.ops.dashboard_snapshots.source_catalog import (
    DASHBOARD_SNAPSHOT_FILENAMES,
    GLOBAL_STATUS_SNAPSHOT_FILENAME,
    SNAPSHOT_BUILD_SUMMARY_FILENAME,
)
from smartcrypto.ops.dashboard_snapshots.source_closeout import (
    build_runtime_source_closeout,
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

    initial_page_statuses = {
        page_id.value: str(snapshot.get("status_summary", {}).get("status", "UNKNOWN"))
        for page_id, snapshot in snapshots.items()
    }
    source_closeout = build_runtime_source_closeout(
        context.project_root,
        context.now_utc,
        initial_page_statuses,
    )
    runtime_evidence_view = build_runtime_evidence_view(
        project_root=context.project_root,
        now_utc=context.now_utc,
        source_closeout=source_closeout,
    )
    runtime_blockers_remediation = build_runtime_blockers_remediation(
        now_utc=context.now_utc,
        dashboard_status=str(source_closeout["dashboard_status"]),
        global_source_health_status=str(source_closeout["global_source_health_status"]),
        runtime_evidence_integration_status=str(
            runtime_evidence_view["runtime_evidence_status"]
        ),
        global_blocking_reasons=source_closeout["global_blocking_reasons"],
        runtime_evidence_blocking_reasons=runtime_evidence_view[
            "blocking_evidence_sources"
        ],
        source_health_matrix=source_closeout["source_health_matrix"],
        runtime_evidence_sources=runtime_evidence_view["evidence_sources"],
    )
    attach_source_closeout(snapshots, source_closeout)
    attach_runtime_evidence_integration(snapshots, runtime_evidence_view)
    attach_runtime_blockers_remediation(snapshots, runtime_blockers_remediation)
    global_snapshot = build_global_status_snapshot(
        context,
        snapshots,
        source_closeout,
        runtime_evidence_view,
        runtime_blockers_remediation,
    )
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
    dashboard_status = str(source_closeout["dashboard_status"])
    status = "error" if errors else dashboard_status.lower()
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
        "dashboard_status": dashboard_status,
        "pages_total": source_closeout["pages_total"],
        "pages_ok": source_closeout["pages_ok"],
        "pages_degraded": source_closeout["pages_degraded"],
        "pages_blocked": source_closeout["pages_blocked"],
        "pages_unknown": source_closeout["pages_unknown"],
        "required_sources_total": source_closeout["required_sources_total"],
        "required_sources_ok": source_closeout["required_sources_ok"],
        "required_sources_missing": source_closeout["required_sources_missing"],
        "stale_sources_total": source_closeout["stale_sources_total"],
        "future_sources_total": source_closeout["future_sources_total"],
        "source_matrix": source_closeout["source_matrix"],
        "source_health_matrix": source_closeout["source_health_matrix"],
        "page_source_matrix": source_closeout["page_source_matrix"],
        "global_blocking_reasons": source_closeout["global_blocking_reasons"],
        "source_health_total": source_closeout["source_health_total"],
        "source_health_healthy": source_closeout["source_health_healthy"],
        "source_health_degraded": source_closeout["source_health_degraded"],
        "source_health_blocked": source_closeout["source_health_blocked"],
        "source_health_planned": source_closeout["source_health_planned"],
        "freshness_fresh_total": source_closeout["freshness_fresh_total"],
        "freshness_warning_total": source_closeout["freshness_warning_total"],
        "freshness_critical_total": source_closeout["freshness_critical_total"],
        "freshness_not_applicable_total": source_closeout[
            "freshness_not_applicable_total"
        ],
        "stale_required_sources": source_closeout["stale_required_sources"],
        "stale_optional_sources": source_closeout["stale_optional_sources"],
        "invalid_timestamp_sources": source_closeout["invalid_timestamp_sources"],
        "freshness_policy_coverage": source_closeout["freshness_policy_coverage"],
        "global_source_health_status": source_closeout["global_source_health_status"],
        "runtime_evidence_integration_status": runtime_evidence_view[
            "runtime_evidence_status"
        ],
        "runtime_evidence_view": runtime_evidence_view,
        "runtime_evidence_blocking_reasons": runtime_evidence_view[
            "blocking_evidence_sources"
        ],
        "runtime_evidence_degraded_reasons": runtime_evidence_view[
            "degraded_evidence_sources"
        ],
        "runtime_evidence_missing_sources": runtime_evidence_view[
            "missing_evidence_sources"
        ],
        "runtime_evidence_stale_sources": runtime_evidence_view[
            "stale_evidence_sources"
        ],
        "runtime_evidence_safety_flags": runtime_evidence_view[
            "runtime_evidence_safety_flags"
        ],
        "combined_blocking_reasons": runtime_blockers_remediation[
            "combined_blocking_reasons"
        ],
        "runtime_blockers_remediation": runtime_blockers_remediation,
        "generated_files": generated_files,
        "builders": builder_reports,
        "missing_required_sources": missing_required,
        "missing_optional_sources": missing_optional,
        "future_sources_pending": future_pending,
        "errors": sorted(set(errors)),
        "sections": {},
        "safety": dict(SAFETY_FLAGS),
        "safety_flags": dict(SAFETY_FLAGS),
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


def attach_source_closeout(
    snapshots: dict[DashboardPageId, dict[str, Any]],
    source_closeout: Mapping[str, Any],
) -> None:
    page_rows = {
        str(row["page_id"]): row
        for row in source_closeout.get("page_source_matrix", [])
        if isinstance(row, Mapping) and row.get("page_id")
    }
    source_rows = [
        row for row in source_closeout.get("source_matrix", []) if isinstance(row, Mapping)
    ]
    for page_id, snapshot in snapshots.items():
        page_row = dict(page_rows.get(page_id.value, {}))
        page_sources = [
            dict(row) for row in source_rows if page_id.value in row.get("consumer_pages", [])
        ]
        snapshot["runtime_source_health"] = page_sources
        snapshot["page_source_closeout"] = page_row
        sections = snapshot.setdefault("sections", {})
        if isinstance(sections, dict):
            sections["runtime_source_health"] = {
                "status": page_row.get("current_page_status", "UNKNOWN"),
                "reason": "runtime_source_closeout",
                "data": page_sources,
            }
        status_summary = snapshot.setdefault("status_summary", {})
        if isinstance(status_summary, dict):
            status_summary["status"] = page_row.get(
                "current_page_status", status_summary.get("status", "UNKNOWN")
            )


def attach_runtime_evidence_integration(
    snapshots: dict[DashboardPageId, dict[str, Any]],
    runtime_evidence_view: Mapping[str, Any],
) -> None:
    section_status = runtime_evidence_section_status(runtime_evidence_view)
    section_payload = {
        "status": section_status,
        "reason": runtime_evidence_view.get(
            "runtime_evidence_reason",
            "runtime_evidence_integration",
        ),
        "runtime_evidence_view": dict(runtime_evidence_view),
        "blocking_evidence_sources": list(
            runtime_evidence_view.get("blocking_evidence_sources", [])
        ),
        "degraded_evidence_sources": list(
            runtime_evidence_view.get("degraded_evidence_sources", [])
        ),
        "missing_evidence_sources": list(
            runtime_evidence_view.get("missing_evidence_sources", [])
        ),
        "stale_evidence_sources": list(
            runtime_evidence_view.get("stale_evidence_sources", [])
        ),
        "canary_release_allowed": False,
        "live_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
    }
    for page_id, snapshot in snapshots.items():
        snapshot["runtime_evidence_integration_status"] = section_status
        snapshot["runtime_evidence_view"] = dict(runtime_evidence_view)
        snapshot["runtime_evidence_blocking_reasons"] = list(
            runtime_evidence_view.get("blocking_evidence_sources", [])
        )
        snapshot["runtime_evidence_degraded_reasons"] = list(
            runtime_evidence_view.get("degraded_evidence_sources", [])
        )
        snapshot["runtime_evidence_missing_sources"] = list(
            runtime_evidence_view.get("missing_evidence_sources", [])
        )
        snapshot["runtime_evidence_stale_sources"] = list(
            runtime_evidence_view.get("stale_evidence_sources", [])
        )
        snapshot["runtime_evidence_safety_flags"] = dict(SAFETY_FLAGS)
        sections = snapshot.setdefault("sections", {})
        if isinstance(sections, dict) and page_id in {
            DashboardPageId.infrastructure,
            DashboardPageId.active_controls,
        }:
            sections["runtime_evidence_integration"] = dict(section_payload)


def attach_runtime_blockers_remediation(
    snapshots: dict[DashboardPageId, dict[str, Any]],
    remediation: Mapping[str, Any],
) -> None:
    section = {
        "status": str(remediation.get("status", "blocked")).upper(),
        "reason": remediation.get("reason", "runtime_blockers_remediation"),
        "data": dict(remediation),
    }
    for page_id, snapshot in snapshots.items():
        if page_id not in {DashboardPageId.infrastructure, DashboardPageId.active_controls}:
            continue
        snapshot["runtime_blockers_remediation"] = dict(remediation)
        sections = snapshot.setdefault("sections", {})
        if isinstance(sections, dict):
            sections["runtime_blockers_remediation"] = dict(section)


def build_global_status_snapshot(
    context: DashboardBuildContext,
    snapshots: dict[DashboardPageId, dict[str, Any]],
    source_closeout: Mapping[str, Any] | None = None,
    runtime_evidence_view: Mapping[str, Any] | None = None,
    runtime_blockers_remediation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    page_statuses = {
        page_id.value: snapshot.get("status_summary", {}).get("status", "UNKNOWN")
        for page_id, snapshot in snapshots.items()
    }
    statuses = [normalize_section_status(value) for value in page_statuses.values()]

    if any(
        status
        in {
            DashboardSectionStatus.ERROR,
            DashboardSectionStatus.BLOCKED,
            DashboardSectionStatus.MISSING_REQUIRED,
        }
        for status in statuses
    ):
        overall = DashboardSectionStatus.BLOCKED
    elif any(
        status
        in {
            DashboardSectionStatus.WARNING,
            DashboardSectionStatus.STALE,
            DashboardSectionStatus.DEGRADED,
            DashboardSectionStatus.MISSING_OPTIONAL,
        }
        for status in statuses
    ):
        overall = DashboardSectionStatus.DEGRADED
    elif statuses and all(status is DashboardSectionStatus.OK for status in statuses):
        overall = DashboardSectionStatus.OK
    else:
        overall = DashboardSectionStatus.UNKNOWN

    missing_required = sorted(
        {
            path
            for snapshot in snapshots.values()
            for path in snapshot.get("missing_required_sources", [])
        }
    )
    missing_optional = sorted(
        {
            path
            for snapshot in snapshots.values()
            for path in snapshot.get("missing_optional_sources", [])
        }
    )

    closeout = source_closeout or {}
    evidence_view = dict(runtime_evidence_view or {})
    remediation = dict(runtime_blockers_remediation or {})

    if closeout.get("dashboard_status"):
        overall_value = str(closeout["dashboard_status"])
    else:
        overall_value = overall.value

    runtime_evidence_status = str(
        evidence_view.get("runtime_evidence_status", "UNKNOWN")
    ).upper()
    if runtime_evidence_status == DashboardSectionStatus.BLOCKED.value:
        overall_value = DashboardSectionStatus.BLOCKED.value

    source_blocking_reasons = list(closeout.get("global_blocking_reasons", [])) or [
        f"missing_required_source:{path}" for path in missing_required
    ]
    source_blocking_reasons = sorted(
        {str(reason) for reason in source_blocking_reasons}
    )

    runtime_evidence_blocking_reasons = sorted(
        {
            str(reason)
            for reason in evidence_view.get("blocking_evidence_sources", [])
        }
    )

    combined_blocking_reasons = sorted(
        set(source_blocking_reasons) | set(runtime_evidence_blocking_reasons)
    )

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
        "overall_status": overall_value,
        "dashboard_status": overall_value,
        "global_source_health_status": closeout.get(
            "global_source_health_status", "UNKNOWN"
        ),
        "runtime_evidence_integration_status": evidence_view.get(
            "runtime_evidence_status", "UNKNOWN"
        ),
        "runtime_evidence_view": evidence_view,
        "runtime_evidence_blocking_reasons": runtime_evidence_blocking_reasons,
        "runtime_evidence_degraded_reasons": list(
            evidence_view.get("degraded_evidence_sources", [])
        ),
        "runtime_evidence_missing_sources": list(
            evidence_view.get("missing_evidence_sources", [])
        ),
        "runtime_evidence_stale_sources": list(
            evidence_view.get("stale_evidence_sources", [])
        ),
        "runtime_evidence_safety_flags": dict(SAFETY_FLAGS),
        "blocking_reasons": source_blocking_reasons,
        "global_blocking_reasons": source_blocking_reasons,
        "combined_blocking_reasons": combined_blocking_reasons,
        "runtime_blockers_remediation": remediation,
        "page_source_matrix": list(closeout.get("page_source_matrix", [])),
        "source_health_matrix": list(closeout.get("source_health_matrix", [])),
        "missing_required_sources_count": len(missing_required),
        "missing_optional_sources_count": len(missing_optional),
        "generated_snapshot_count": len(snapshots),
        "sections": {
            "pages": {
                "status": overall_value,
                "reason": "consolidated_page_status",
                "data": page_statuses,
            },
            "runtime_evidence_integration": {
                "status": evidence_view.get("runtime_evidence_status", "UNKNOWN"),
                "reason": evidence_view.get(
                    "runtime_evidence_reason",
                    "runtime_evidence_integration",
                ),
                "runtime_evidence_view": evidence_view,
            },
            "runtime_blockers_remediation": {
                "status": str(remediation.get("status", "blocked")).upper(),
                "reason": remediation.get(
                    "reason", "runtime_blockers_remediation"
                ),
                "data": remediation,
            },
        },
        "safety": dict(SAFETY_FLAGS),
        "audit": DashboardAuditContract(
            snapshot_source="dashboard_global_status"
        ).to_dict(),
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
