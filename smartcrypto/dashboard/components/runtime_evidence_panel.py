from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smartcrypto.dashboard.ui.tables import render_html_table

SUMMARY_COLUMNS = [
    "runtime_evidence_status",
    "runtime_evidence_pack_status",
    "readiness_status",
    "paper_runtime_health_status",
    "container_snapshot_status",
    "gap_accounting_status",
    "continuous_valid_soak_days",
    "critical_gap_count",
    "canary_release_allowed",
    "live_release_allowed",
]

SOURCE_COLUMNS = [
    "source_id",
    "status",
    "health_status",
    "freshness_status",
    "required",
    "missing",
    "stale",
    "blocking",
    "degraded",
    "path",
    "remediation_action",
]


def render_runtime_evidence_panel(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    view = runtime_evidence_view(snapshot)
    ui.subheader("Runtime Evidence & Readiness")
    if not view:
        ui.info("UNKNOWN: runtime evidence integration ausente no snapshot.")
        return

    ui.markdown(
        render_html_table(
            [runtime_evidence_summary_row(view)],
            columns=SUMMARY_COLUMNS,
            status_columns=["runtime_evidence_status"],
            empty_message="Runtime evidence ausente.",
        ),
        unsafe_allow_html=True,
    )

    sources = runtime_evidence_source_rows(view)
    ui.markdown(
        render_html_table(
            sources,
            columns=SOURCE_COLUMNS,
            status_columns=["status"],
            empty_message="Nenhuma fonte de runtime evidence mapeada.",
        ),
        unsafe_allow_html=True,
    )

    action = view.get("operator_action") or view.get("remediation_hint")
    if action:
        ui.info(str(action))


def runtime_evidence_view(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    direct = snapshot.get("runtime_evidence_view")
    if isinstance(direct, Mapping):
        return dict(direct)
    sections = snapshot.get("sections", {})
    if isinstance(sections, Mapping):
        section = sections.get("runtime_evidence_integration", {})
        if isinstance(section, Mapping) and isinstance(section.get("runtime_evidence_view"), Mapping):
            return dict(section["runtime_evidence_view"])
    return {}


def runtime_evidence_summary_row(view: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "runtime_evidence_status": view.get("runtime_evidence_status", "UNKNOWN"),
        "runtime_evidence_pack_status": view.get("runtime_evidence_pack_status", "UNKNOWN"),
        "readiness_status": view.get("readiness_status", "UNKNOWN"),
        "paper_runtime_health_status": view.get("paper_runtime_health_status", "UNKNOWN"),
        "container_snapshot_status": view.get("container_snapshot_status", "UNKNOWN"),
        "gap_accounting_status": view.get("gap_accounting_status", "UNKNOWN"),
        "continuous_valid_soak_days": view.get("continuous_valid_soak_days", 0.0),
        "critical_gap_count": view.get("critical_gap_count", 0),
        "canary_release_allowed": bool(view.get("canary_release_allowed", False)),
        "live_release_allowed": bool(view.get("live_release_allowed", False)),
    }


def runtime_evidence_source_rows(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    sources = view.get("evidence_sources", [])
    if not isinstance(sources, list):
        return []
    rows: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        rows.append(
            {
                "source_id": source.get("source_id", "UNKNOWN"),
                "status": source.get("status", "UNKNOWN"),
                "health_status": source.get("health_status", "UNKNOWN"),
                "freshness_status": source.get("freshness_status", "UNKNOWN"),
                "required": bool(source.get("required", False)),
                "missing": bool(source.get("missing", False)),
                "stale": bool(source.get("stale", False)),
                "blocking": bool(source.get("blocking", False)),
                "degraded": bool(source.get("degraded", False)),
                "path": source.get("path", "UNKNOWN"),
                "remediation_action": source.get("remediation_action", "Consult the runbook."),
            }
        )
    return rows
