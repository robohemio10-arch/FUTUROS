from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smartcrypto.dashboard.ui.tables import render_html_table


SOURCE_COLUMNS = [
    "display_name",
    "status",
    "health_status",
    "freshness_status",
    "severity",
    "age_seconds",
    "effective_timestamp_utc",
    "timestamp_source",
    "freshness_basis",
    "max_age_seconds",
    "path",
    "consumer_pages",
    "consumer_snapshots",
    "blocks_dashboard_readiness",
    "remediation_action",
]


def render_runtime_source_health(snapshot: Mapping[str, Any], *, ui: Any) -> None:
    rows = source_health_rows(snapshot)
    ui.subheader("Runtime Source Health")
    if not rows:
        ui.info("UNKNOWN: runtime source closeout ausente no snapshot.")
        return
    ui.markdown(
        render_html_table(
            rows,
            columns=SOURCE_COLUMNS,
            status_columns=["status"],
            empty_message="Nenhuma fonte runtime mapeada.",
        ),
        unsafe_allow_html=True,
    )


def source_health_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload = snapshot.get("runtime_source_health", [])
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        age = item.get("age_seconds")
        rows.append(
            {
                "display_name": item.get("display_name", item.get("source_id", "UNKNOWN")),
                "status": item.get("status", "UNKNOWN"),
                "health_status": item.get("health_status", "UNKNOWN"),
                "freshness_status": item.get("freshness_status", "UNKNOWN"),
                "severity": item.get("severity", "UNKNOWN"),
                "age_seconds": round(float(age), 1) if isinstance(age, int | float) else "N/A",
                "effective_timestamp_utc": item.get("effective_timestamp_utc") or "N/A",
                "timestamp_source": item.get("timestamp_source", "unavailable"),
                "freshness_basis": item.get("freshness_basis", "NOT_APPLICABLE"),
                "max_age_seconds": item.get("max_age_seconds") or "N/A",
                "path": item.get("canonical_path", item.get("path", "UNKNOWN")),
                "consumer_pages": ", ".join(str(value) for value in item.get("consumer_pages", [])),
                "consumer_snapshots": ", ".join(
                    str(value) for value in item.get("consumer_snapshots", [])
                ),
                "blocks_dashboard_readiness": bool(item.get("blocks_dashboard_readiness", False)),
                "remediation_action": item.get(
                    "remediation_action",
                    item.get("operator_hint", "Consult the source runbook."),
                ),
            }
        )
    return rows
