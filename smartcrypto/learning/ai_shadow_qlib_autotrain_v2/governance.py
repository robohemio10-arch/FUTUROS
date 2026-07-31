"""Fail-closed training eligibility and cadence governance."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .contracts import CadenceContract

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def evaluate_training_eligibility(
    state: Mapping[str, Any],
    *,
    min_training_sample_rows: int,
) -> dict[str, Any]:
    blockers: list[str] = []
    new_unique_trade_count = _nonnegative_int(
        state.get("new_unique_trade_count"),
        "new_unique_trade_count",
    )
    total_unique_sample_count = _nonnegative_int(
        state.get("total_unique_sample_count"),
        "total_unique_sample_count",
    )
    previous_watermark = _parse_utc(state.get("previous_watermark"), "previous_watermark")
    current_watermark = _parse_utc(state.get("current_watermark"), "current_watermark")
    previous_hash = _sha256(state.get("previous_dataset_hash"), "previous_dataset_hash")
    current_hash = _sha256(state.get("current_dataset_hash"), "current_dataset_hash")
    raw_seen_hashes = state.get("prior_microbatch_hashes", [])
    if not isinstance(raw_seen_hashes, list):
        raise ValueError("prior_microbatch_hashes must be a list")
    prior_hashes = {_sha256(item, "prior_microbatch_hash") for item in raw_seen_hashes}

    watermark_advanced = current_watermark > previous_watermark
    dataset_hash_changed = current_hash != previous_hash
    duplicate_microbatch = current_hash in prior_hashes
    minimum_sample_met = total_unique_sample_count >= min_training_sample_rows

    if new_unique_trade_count <= 0:
        blockers.append("new_unique_trade_count_not_positive")
    if not watermark_advanced:
        blockers.append("watermark_not_advanced")
    if not dataset_hash_changed:
        blockers.append("dataset_hash_not_changed")
    if duplicate_microbatch:
        blockers.append("duplicate_microbatch_hash_detected")
    if not minimum_sample_met:
        blockers.append("minimum_training_sample_not_met")

    eligible = not blockers
    return {
        "status": "ok" if eligible else "blocked",
        "research_training_eligible": eligible,
        "new_unique_trade_count": new_unique_trade_count,
        "total_unique_sample_count": total_unique_sample_count,
        "minimum_training_sample_rows": min_training_sample_rows,
        "minimum_training_sample_met": minimum_sample_met,
        "previous_watermark": previous_watermark.isoformat(),
        "current_watermark": current_watermark.isoformat(),
        "watermark_advanced": watermark_advanced,
        "previous_dataset_hash": previous_hash,
        "current_dataset_hash": current_hash,
        "dataset_hash_changed": dataset_hash_changed,
        "duplicate_microbatch_detected": duplicate_microbatch,
        "blockers": blockers,
        "training_requested": False,
        "training_performed": False,
        "automatic_training": False,
        "challenger_destination": "quarantine_research_only",
        "promotion_allowed": False,
        "automatic_promotion": False,
        "active_model_changed": False,
        "writes_active_registry": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
    }


def build_cadence_governance(cadence: CadenceContract) -> dict[str, Any]:
    return {
        "status": "ok",
        "cadence": cadence.as_dict(),
        "cadence_separation_enforced": True,
        "operational_check_scope": "read_only_health_and_freshness",
        "feedback_scope": "trade_closed_incremental_feedback",
        "drift_scope": "feature_label_regime_calibration_expected_value",
        "smoke_training_scope": "research_quarantine_only",
        "full_training_scope": "research_quarantine_only",
        "governance_scope": "manual_review_only",
        "scheduler_created": False,
        "scheduler_registered": False,
        "daemon_started": False,
    }


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return parsed


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a SHA-256 string")
    normalized = value.strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError(f"{field} must be a 64-character SHA-256 hex string")
    return normalized


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)
