"""Dry-run and once-only scheduler for paper auto-learning foundation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from .daily_foundation_runner import build_paper_autolearning_foundation_report
from .outcome_schema import SAFETY_FLAGS as FOUNDATION_SAFETY_FLAGS
from .outcome_schema import utc_now_iso

SCHEMA_VERSION = "paper_autolearning_daily_scheduler_v1"
SCHEDULE_CADENCE = "daily"
DEFAULT_RUN_HOUR_UTC = 3

SCHEDULER_SAFETY_FLAGS: dict[str, bool] = {
    **FOUNDATION_SAFETY_FLAGS,
    "creates_cron": False,
    "creates_systemd_timer": False,
    "creates_windows_task": False,
    "creates_service": False,
}

FoundationRunner = Callable[..., dict[str, Any]]


def build_paper_autolearning_scheduler_report(
    *,
    project_root: str | Path,
    once: bool = False,
    write_feedback: bool = False,
    train_smoke: bool = False,
    register_scheduler: bool = False,
    source_path: str | Path | None = None,
    foundation_runner: FoundationRunner = build_paper_autolearning_foundation_report,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Build a scheduler report and optionally invoke the foundation runner once."""

    root = Path(project_root).resolve()
    now = now_utc or datetime.now(UTC)
    registration_status = "blocked" if register_scheduler else "not_requested"
    foundation_report: Mapping[str, Any] = {}
    if once and not register_scheduler:
        foundation_report = foundation_runner(
            project_root=root,
            source_path=source_path,
            write_feedback=write_feedback,
            train_smoke=train_smoke,
        )
    executed_once = bool(once and not register_scheduler)
    status = "blocked" if register_scheduler else "ok"
    reason = (
        "scheduler_registration_deferred_to_deployment_branch"
        if register_scheduler
        else "foundation_runner_executed_once"
        if executed_once
        else "scheduler_dry_run_ready"
    )
    scheduler_status = "dry_run_ready" if not executed_once else "ready"
    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "scheduler_status": scheduler_status,
        "scheduler_mode": "once" if once else "dry_run",
        "schedule_cadence": SCHEDULE_CADENCE,
        "next_planned_run_utc": next_planned_run(now).isoformat(),
        "would_run_command": would_run_command(
            project_root=root,
            write_feedback=write_feedback if once else True,
            train_smoke=train_smoke if once else True,
        ),
        "executed_once": executed_once,
        "foundation_runner_invoked": bool(foundation_report),
        "foundation_runner_status": foundation_report.get("status"),
        "closed_trades_loaded_count": int(foundation_report.get("closed_trades_loaded_count") or 0),
        "new_feedback_events_count": int(foundation_report.get("new_feedback_events_count") or 0),
        "duplicate_feedback_events_count": int(foundation_report.get("duplicate_feedback_events_count") or 0),
        "microbatch_rows": int(foundation_report.get("microbatch_rows") or 0),
        "qlib_challenger_smoke_ran": foundation_report.get("qlib_challenger_smoke_ran") is True,
        "ai_shadow_challenger_smoke_ran": foundation_report.get("ai_shadow_challenger_smoke_ran") is True,
        "qlib_challenger_trained": False,
        "ai_shadow_challenger_trained": False,
        "scheduler_registration_requested": bool(register_scheduler),
        "scheduler_registration_performed": False,
        "scheduler_registration_status": registration_status,
        "master_update_requested": False,
        "master_update_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        **SCHEDULER_SAFETY_FLAGS,
        "safety_flags": dict(SCHEDULER_SAFETY_FLAGS),
        "validation_errors": [],
        "foundation_runner_summary": summarize_foundation_report(foundation_report),
    }
    report["validation_errors"] = validate_scheduler_report(report)
    return report


def next_planned_run(now_utc: datetime) -> datetime:
    now = now_utc.astimezone(UTC)
    planned = now.replace(hour=DEFAULT_RUN_HOUR_UTC, minute=0, second=0, microsecond=0)
    if planned <= now:
        planned += timedelta(days=1)
    return planned


def would_run_command(
    *,
    project_root: str | Path,
    write_feedback: bool,
    train_smoke: bool,
) -> list[str]:
    command = [
        "python",
        "scripts/run_paper_autolearning_foundation_v1.py",
        "--project-root",
        str(project_root),
    ]
    command.append("--write-feedback" if write_feedback else "--no-write")
    if train_smoke:
        command.append("--train-smoke")
    command.append("--json")
    return command


def summarize_foundation_report(report: Mapping[str, Any]) -> dict[str, Any]:
    if not report:
        return {}
    keys = (
        "status",
        "reason",
        "closed_trades_loaded_count",
        "new_feedback_events_count",
        "duplicate_feedback_events_count",
        "microbatch_rows",
        "qlib_challenger_smoke_ran",
        "ai_shadow_challenger_smoke_ran",
        "master_update_performed",
        "model_promotion_performed",
        "active_model_changed",
        "sends_orders",
        "exchange_private_access",
        "changes_risk",
        "writes_runtime",
    )
    return {key: report.get(key) for key in keys}


def validate_scheduler_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    for key, expected in SCHEDULER_SAFETY_FLAGS.items():
        if report.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
        safety = report.get("safety_flags")
        if not isinstance(safety, Mapping) or safety.get(key) is not expected:
            errors.append(f"safety_flags.{key}_must_be_{str(expected).lower()}")
    if report.get("scheduler_registration_performed") is not False:
        errors.append("scheduler_registration_performed_must_be_false")
    if report.get("qlib_challenger_trained") is not False:
        errors.append("qlib_challenger_trained_must_be_false")
    if report.get("ai_shadow_challenger_trained") is not False:
        errors.append("ai_shadow_challenger_trained_must_be_false")
    return sorted(set(errors))
