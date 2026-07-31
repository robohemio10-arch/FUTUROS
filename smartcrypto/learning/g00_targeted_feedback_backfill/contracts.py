"""Contracts for the target-only G00 feedback backfill."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "g00_targeted_feedback_backfill_v1"
CONFIRMATION_TEXT = "EXECUTAR BACKFILL TARGETED G00 TRADES 599 E 600"
TARGET_TRADE_IDS = ("599", "600")
TARGET_ORDER_IDS: dict[str, str] = {
    "599": "freqtrade-paper-599",
    "600": "freqtrade-paper-600",
}

DEFAULT_FEEDBACK_EVENTS = Path(
    "data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl"
)
DEFAULT_BACKUP_DIR = Path("data/backups/g00_targeted_feedback_backfill")
DEFAULT_REPORT_JSON = Path("data/reports/g00_targeted_feedback_backfill_v1.json")
DEFAULT_REPORT_MARKDOWN = Path("data/reports/g00_targeted_feedback_backfill_v1.md")
ALLOWED_REPORT_ROOT = Path("data/reports")

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,79}$")

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "target_only": True,
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
    "advances_watermark": False,
    "runtime_activation": False,
}


@dataclass(frozen=True)
class TargetedAuthorization:
    execute_targeted_backfill: bool = False
    expected_plan_hash: str | None = None
    expected_dryrun_hash: str | None = None
    expected_target_batch_hash: str | None = None
    expected_source_fingerprint_hash: str | None = None
    authorization_reference: str | None = None
    confirmation_text: str | None = None


def authorization_errors(
    authorization: TargetedAuthorization,
    *,
    plan_hash: str | None,
    dryrun_hash: str | None,
    target_batch_hash: str | None,
    source_fingerprint_hash: str | None,
) -> list[str]:
    """Validate the complete target-only authorization tuple."""

    errors: list[str] = []
    if not authorization.execute_targeted_backfill:
        errors.append("execute_targeted_backfill_not_requested")

    errors.extend(
        _hash_errors(
            "expected_plan_hash",
            authorization.expected_plan_hash,
            plan_hash,
        )
    )
    errors.extend(
        _hash_errors(
            "expected_dryrun_hash",
            authorization.expected_dryrun_hash,
            dryrun_hash,
        )
    )
    errors.extend(
        _hash_errors(
            "expected_target_batch_hash",
            authorization.expected_target_batch_hash,
            target_batch_hash,
        )
    )
    errors.extend(
        _hash_errors(
            "expected_source_fingerprint_hash",
            authorization.expected_source_fingerprint_hash,
            source_fingerprint_hash,
        )
    )

    reference = str(authorization.authorization_reference or "")
    if not reference:
        errors.append("authorization_reference_required")
    elif not REFERENCE_PATTERN.fullmatch(reference):
        errors.append("invalid_authorization_reference")

    if authorization.confirmation_text != CONFIRMATION_TEXT:
        errors.append("confirmation_text_mismatch")
    return errors


def idempotent_authorization_errors(
    authorization: TargetedAuthorization,
    *,
    target_batch_hash: str | None,
    source_fingerprint_hash: str | None,
    stored_source_plan_hashes: set[str],
) -> list[str]:
    """Validate an already-applied request without accepting regenerated hashes."""

    errors: list[str] = []
    if not authorization.execute_targeted_backfill:
        errors.append("execute_targeted_backfill_not_requested")

    expected_plan = _normalized_hash(authorization.expected_plan_hash)
    if expected_plan is None:
        errors.append("expected_plan_hash_required")
    elif stored_source_plan_hashes != {expected_plan}:
        errors.append("expected_plan_hash_not_bound_to_stored_targets")

    expected_dryrun = _normalized_hash(authorization.expected_dryrun_hash)
    if expected_dryrun is None:
        errors.append("expected_dryrun_hash_required")

    errors.extend(
        _hash_errors(
            "expected_target_batch_hash",
            authorization.expected_target_batch_hash,
            target_batch_hash,
        )
    )
    errors.extend(
        _hash_errors(
            "expected_source_fingerprint_hash",
            authorization.expected_source_fingerprint_hash,
            source_fingerprint_hash,
        )
    )

    reference = str(authorization.authorization_reference or "")
    if not reference:
        errors.append("authorization_reference_required")
    elif not REFERENCE_PATTERN.fullmatch(reference):
        errors.append("invalid_authorization_reference")

    if authorization.confirmation_text != CONFIRMATION_TEXT:
        errors.append("confirmation_text_mismatch")
    return errors


def _normalized_hash(value: str | None) -> str | None:
    normalized = str(value or "").strip().casefold()
    return normalized if SHA256_PATTERN.fullmatch(normalized) else None


def _hash_errors(
    name: str,
    supplied: str | None,
    actual: str | None,
) -> list[str]:
    normalized = _normalized_hash(supplied)
    if normalized is None:
        return [f"{name}_required"]
    actual_normalized = str(actual or "").strip().casefold()
    if normalized != actual_normalized:
        return [f"{name}_mismatch"]
    return []


def resolve_under_root(
    root: Path,
    value: str | Path | None,
    default: Path,
) -> Path:
    candidate = Path(value) if value is not None else default
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (root / candidate).resolve()
    )
    resolved.relative_to(root)
    return resolved


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def report_safety(
    *,
    read_only: bool,
    writes_feedback_store: bool = False,
) -> dict[str, bool]:
    return {
        **SAFETY_FLAGS,
        "read_only": bool(read_only),
        "writes_feedback_store": bool(writes_feedback_store),
    }


def sanitized_report(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema_version",
        "status",
        "reason",
        "decision",
        "operation_id",
        "authorization_reference",
        "diagnostics_identity_hash",
        "plan_hash",
        "dryrun_hash",
        "target_batch_hash",
        "source_fingerprint_hash",
        "full_planned_event_count",
        "target_planned_event_count",
        "other_planned_event_count",
        "target_existing_event_count",
        "target_effective_event_count",
        "target_validation",
        "pre_write_feedback_count",
        "post_write_feedback_count",
        "planned_event_count",
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
        "blockers",
        "warnings",
        "write_report_requested",
        "write_report_performed",
        "safety_flags",
        *SAFETY_FLAGS,
        "read_only",
        "writes_feedback_store",
    }
    return {key: value for key, value in payload.items() if key in allowed}
