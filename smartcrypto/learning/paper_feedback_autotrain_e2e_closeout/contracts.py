"""Contracts for the controlled paper-feedback E2E closeout."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "paper_feedback_autotrain_e2e_closeout_v1"
CONFIRMATION_TEXT = "EXECUTAR BACKFILL CONTROLADO DE FEEDBACK PAPER"
DEFAULT_FEEDBACK_EVENTS = Path("data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl")
DEFAULT_BACKUP_DIR = Path("data/backups/paper_feedback_autotrain_e2e_closeout")
DEFAULT_REPORT_JSON = Path("data/reports/paper_feedback_autotrain_e2e_closeout_v1.json")
DEFAULT_REPORT_MARKDOWN = Path("data/reports/paper_feedback_autotrain_e2e_closeout_v1.md")
ALLOWED_REPORT_ROOT = Path("data/reports")
REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,79}$")


SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "runs_training": False,
    "promotes_model": False,
    "writes_models": False,
    "writes_registries": False,
    "writes_signals": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "creates_microbatch": False,
}


@dataclass(frozen=True)
class Authorization:
    execute_backfill: bool = False
    expected_plan_hash: str | None = None
    expected_dryrun_hash: str | None = None
    authorization_reference: str | None = None
    confirmation_text: str | None = None


@dataclass(frozen=True)
class CloseoutPaths:
    project_root: Path
    feedback_events: Path
    backup_dir: Path
    report_json: Path
    report_markdown: Path
    paper_db: Path | None = None


def authorization_errors(
    authorization: Authorization,
    *,
    plan_hash: str | None,
    dryrun_hash: str | None,
) -> list[str]:
    errors: list[str] = []
    if not authorization.execute_backfill:
        errors.append("execute_backfill_not_requested")
    if not authorization.expected_plan_hash:
        errors.append("expected_plan_hash_required")
    elif str(authorization.expected_plan_hash).casefold() != str(plan_hash or "").casefold():
        errors.append("expected_plan_hash_mismatch")
    if not authorization.expected_dryrun_hash:
        errors.append("expected_dryrun_hash_required")
    elif str(authorization.expected_dryrun_hash).casefold() != str(dryrun_hash or "").casefold():
        errors.append("expected_dryrun_hash_mismatch")
    if not authorization.authorization_reference:
        errors.append("authorization_reference_required")
    elif not REFERENCE_PATTERN.fullmatch(authorization.authorization_reference):
        errors.append("invalid_authorization_reference")
    if authorization.confirmation_text != CONFIRMATION_TEXT:
        errors.append("confirmation_text_mismatch")
    return errors


def resolve_under_root(root: Path, value: str | Path | None, default: Path) -> Path:
    candidate = Path(value) if value is not None else default
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    resolved.relative_to(root)
    return resolved


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def report_safety(*, read_only: bool) -> dict[str, bool]:
    return {**SAFETY_FLAGS, "read_only": bool(read_only)}


def sanitized_report(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema_version",
        "status",
        "reason",
        "decision",
        "operation_id",
        "authorization_reference",
        "plan_hash",
        "dryrun_hash",
        "source_fingerprint_hash",
        "pre_write_feedback_count",
        "planned_event_count",
        "post_write_feedback_count",
        "applied_event_count",
        "already_existing_count",
        "missing_after_count",
        "duplicate_count",
        "conflict_count",
        "schema_error_count",
        "backup_created",
        "rollback_performed",
        "already_applied",
        "write_performed",
        "backfill_performed",
        "manual_intervention_required",
        "continuity_status",
        "watermark_status",
        "microbatch_readiness_status",
        "qlib_backend_status",
        "walkforward_gate_status",
        "execution_cost_gate_status",
        "drift_gate_status",
        "registry_gate_status",
        "final_readiness_decision",
        "blockers",
        "warnings",
        "write_report_requested",
        "write_report_performed",
        "safety_flags",
        *SAFETY_FLAGS,
        "read_only",
    }
    return {key: value for key, value in payload.items() if key in allowed}
