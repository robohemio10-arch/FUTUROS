"""B06 preparation and initialization of the 30-day paper/shadow soak."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .contracts import DECISION_READY, mapping
from .io import positive_int
from .writer import ReportWriter

SOAK_SCHEMA_VERSION = "paper_shadow_soak_state_v2"
DEFAULT_SOAK_STATE_PATH = Path(
    "data/reports/soak/paper_shadow_soak_state_v2.json"
)
MANDATORY_SOAK_METRICS = (
    "uptime",
    "freshness",
    "trades",
    "gaps",
    "duplicates",
    "missed_signals",
    "feedback_completeness",
    "drift",
    "drawdown",
    "containers",
    "notifications",
)


def _parse_timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def build_soak_plan(config: Mapping[str, Any]) -> dict[str, Any]:
    soak_config = mapping(config.get("soak"))
    required_days = positive_int(soak_config.get("required_days"), 30)
    configured_metrics = tuple(
        str(item)
        for item in (
            soak_config.get("required_metrics")
            or MANDATORY_SOAK_METRICS
        )
    )
    missing_metrics = sorted(
        set(MANDATORY_SOAK_METRICS) - set(configured_metrics)
    )
    return {
        "schema_version": SOAK_SCHEMA_VERSION,
        "required_days": required_days,
        "required_metrics": list(configured_metrics),
        "missing_mandatory_metrics": missing_metrics,
        "alerts_required": True,
        "unresolved_p0_p1_allowed": False,
        "runtime_activation_performed": False,
        "scheduler_created": False,
        "starts_service": False,
    }


def build_initial_soak_state(
    *,
    readiness_report: Mapping[str, Any],
    config: Mapping[str, Any],
    started_at_utc: str | None = None,
) -> dict[str, Any]:
    """Create the canonical initial state only after all B06 gates pass."""

    plan = build_soak_plan(config)
    readiness_allowed = (
        readiness_report.get("status") == "ok"
        and readiness_report.get("decision") == DECISION_READY
        and readiness_report.get("ready_for_30_day_soak") is True
        and not plan["missing_mandatory_metrics"]
    )
    started_at = _parse_timestamp(started_at_utc)
    target_end = started_at + timedelta(days=int(plan["required_days"]))
    return {
        "schema_version": SOAK_SCHEMA_VERSION,
        "status": "running" if readiness_allowed else "blocked",
        "reason": (
            "soak_observation_window_initialized"
            if readiness_allowed
            else "b06_readiness_required_before_soak"
        ),
        "started_at_utc": started_at.isoformat(),
        "target_end_at_utc": target_end.isoformat(),
        "required_days": plan["required_days"],
        "required_metrics": plan["required_metrics"],
        "sample_count": 0,
        "observed_calendar_days": 0.0,
        "continuous_valid_soak_days": 0.0,
        "unresolved_p0_count": 0,
        "unresolved_p1_count": 0,
        "alerts": [],
        "readiness_report_sha256": readiness_report.get("evidence_sha256"),
        "paper_only": True,
        "shadow_only": True,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "sends_orders": False,
        "changes_risk": False,
        "writes_runtime": False,
        "starts_service": False,
        "scheduler_created": False,
        "initialization_allowed": readiness_allowed,
    }


def initialize_soak_state(
    *,
    readiness_report: Mapping[str, Any],
    config: Mapping[str, Any],
    output_path: Path,
    writer: ReportWriter,
    started_at_utc: str | None = None,
) -> dict[str, Any]:
    """Persist one advisory soak state; never start a runtime service."""

    state = build_initial_soak_state(
        readiness_report=readiness_report,
        config=config,
        started_at_utc=started_at_utc,
    )
    if state["initialization_allowed"] is not True:
        return {
            "status": "blocked",
            "reason": state["reason"],
            "write_performed": False,
            "state": state,
        }
    writer.write_json(output_path, state)
    return {
        "status": "ok",
        "reason": "soak_state_initialized",
        "write_performed": True,
        "state": state,
    }
