"""Idempotent Phase14-to-autolearning lineage reconciliation.

The reconciliation is preview-only by default. It enriches existing outcome rows
by canonical ``order_id`` without changing event identity, economic outcomes,
row count, runtime state, models, risk, orders, or Trader Master.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .feedback_store import (
    clean_text,
    event_dedup_key,
    normalize_closed_trade_row,
    normalize_identity,
    normalize_side,
    normalize_symbol,
    normalize_time,
    read_existing_outcome_events,
    read_rows,
    safe_float,
    write_feedback_outputs,
)
from .outcome_schema import (
    DEFAULT_CLOSED_TRADES_CSV,
    DEFAULT_FEEDBACK_STORE,
    DEFAULT_OUTCOME_EVENTS,
)

SCHEMA_VERSION = "phase14_feedback_lineage_reconciliation_v1"
EXIT_CLASSIFICATION_FIELDS = ("roi_hit", "stoploss_hit", "forced_exit", "liquidation_flag")

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "master_update_requested": False,
    "master_update_performed": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
}


@dataclass(frozen=True)
class ReconciliationResult:
    status: str
    reason: str
    reconciled_events: list[dict[str, Any]]
    matched_count: int
    update_count: int
    unchanged_count: int
    unmatched_existing_count: int
    unmatched_source_count: int
    conflict_count: int
    conflicts: list[dict[str, Any]]
    updated_order_ids: list[str]


def build_lineage_reconciliation(
    *,
    existing_events: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
) -> ReconciliationResult:
    """Build an idempotent reconciliation plan without writing files."""

    source_events = [
        normalize_closed_trade_row(
            row,
            source_file="<phase14_lineage_reconciliation>",
            source_sha256=None,
            ingestion_run_id="lineage_reconciliation_preview",
            source_row_index=index,
            created_at_utc="1970-01-01T00:00:00+00:00",
        )
        for index, row in enumerate(source_rows, start=1)
    ]

    source_map, source_duplicates = _index_by_order_id(source_events)
    _, existing_duplicates = _index_by_order_id(existing_events)
    conflicts: list[dict[str, Any]] = []

    for order_id in sorted(source_duplicates):
        conflicts.append(
            {"order_id": order_id, "field": "order_id", "reason": "duplicate_source_order_id"}
        )
    for order_id in sorted(existing_duplicates):
        conflicts.append(
            {
                "order_id": order_id,
                "field": "order_id",
                "reason": "duplicate_existing_order_id",
            }
        )

    if conflicts:
        return _blocked_result(
            reason="duplicate_order_identity_detected",
            existing_events=existing_events,
            conflicts=conflicts,
        )

    reconciled: list[dict[str, Any]] = []
    updated_order_ids: list[str] = []
    matched_count = 0
    unchanged_count = 0
    matched_source_ids: set[str] = set()

    for raw_existing in existing_events:
        existing = dict(raw_existing)
        order_id = normalize_identity(existing.get("order_id"))
        source = source_map.get(order_id) if order_id else None
        if source is None:
            reconciled.append(existing)
            unchanged_count += 1
            continue

        matched_count += 1
        matched_source_ids.add(order_id)
        row_conflicts = _find_conflicts(existing, source)
        if row_conflicts:
            conflicts.extend({"order_id": order_id, **conflict} for conflict in row_conflicts)
            reconciled.append(existing)
            continue

        enriched, changed = _enrich_event(existing, source)
        reconciled.append(enriched)
        if changed:
            updated_order_ids.append(order_id)
        else:
            unchanged_count += 1

    unmatched_existing_count = len(existing_events) - matched_count
    unmatched_source_count = len(source_map) - len(matched_source_ids)

    if conflicts:
        return ReconciliationResult(
            status="blocked",
            reason="economic_or_identity_conflict_detected",
            reconciled_events=[dict(event) for event in existing_events],
            matched_count=matched_count,
            update_count=0,
            unchanged_count=len(existing_events),
            unmatched_existing_count=unmatched_existing_count,
            unmatched_source_count=unmatched_source_count,
            conflict_count=len(conflicts),
            conflicts=conflicts,
            updated_order_ids=[],
        )

    dedup_keys = [event_dedup_key(event) for event in reconciled]
    if len(dedup_keys) != len(set(dedup_keys)):
        return ReconciliationResult(
            status="blocked",
            reason="dedup_identity_collision_after_reconciliation",
            reconciled_events=[dict(event) for event in existing_events],
            matched_count=matched_count,
            update_count=0,
            unchanged_count=len(existing_events),
            unmatched_existing_count=unmatched_existing_count,
            unmatched_source_count=unmatched_source_count,
            conflict_count=1,
            conflicts=[{"field": "dedup_key", "reason": "collision_after_reconciliation"}],
            updated_order_ids=[],
        )

    return ReconciliationResult(
        status="ok",
        reason="lineage_reconciliation_ready",
        reconciled_events=reconciled,
        matched_count=matched_count,
        update_count=len(updated_order_ids),
        unchanged_count=unchanged_count,
        unmatched_existing_count=unmatched_existing_count,
        unmatched_source_count=unmatched_source_count,
        conflict_count=0,
        conflicts=[],
        updated_order_ids=updated_order_ids,
    )


def reconcile_feedback_lineage_files(
    *,
    project_root: str | Path,
    source_path: str | Path | None = None,
    outcome_events_path: str | Path | None = None,
    feedback_store_path: str | Path | None = None,
    write: bool = False,
) -> dict[str, Any]:
    """Preview or explicitly persist lineage reconciliation under data/feedback."""

    root = Path(project_root).resolve()
    source = _resolve(root, source_path, DEFAULT_CLOSED_TRADES_CSV)
    outcome_path = _resolve(root, outcome_events_path, DEFAULT_OUTCOME_EVENTS)
    feedback_path = _resolve(root, feedback_store_path, DEFAULT_FEEDBACK_STORE)

    blockers: list[str] = []
    if not source.exists() or not source.is_file():
        blockers.append(f"source_missing:{source}")
    if not outcome_path.exists() or not outcome_path.is_file():
        blockers.append(f"outcome_events_missing:{outcome_path}")
    if write:
        blockers.extend(_validate_feedback_write_paths(root, outcome_path, feedback_path))

    source_rows = read_rows(source) if not blockers else []
    existing_events = read_existing_outcome_events(outcome_path) if not blockers else []
    result = (
        build_lineage_reconciliation(existing_events=existing_events, source_rows=source_rows)
        if not blockers
        else _blocked_result(
            reason="input_or_write_contract_blocked",
            existing_events=[],
            conflicts=[],
        )
    )

    status = "blocked" if blockers or result.status == "blocked" else "ok"
    reason = blockers[0] if blockers else result.reason
    write_performed = False
    write_summary: dict[str, Any] = {}

    if write and status == "ok" and result.update_count > 0:
        write_summary = write_feedback_outputs(
            feedback_store_path=feedback_path,
            outcome_events_path=outcome_path,
            existing_events=result.reconciled_events,
            new_events=[],
        )
        write_performed = True

    row_count_invariant = not blockers and len(result.reconciled_events) == len(existing_events)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "mode": "write" if write else "preview",
        "source_path": str(source),
        "outcome_events_path": str(outcome_path),
        "feedback_store_path": str(feedback_path),
        "source_row_count": len(source_rows),
        "existing_event_count": len(existing_events),
        "reconciled_event_count": len(result.reconciled_events),
        "row_count_invariant": row_count_invariant,
        "matched_count": result.matched_count,
        "update_count": result.update_count,
        "unchanged_count": result.unchanged_count,
        "unmatched_existing_count": result.unmatched_existing_count,
        "unmatched_source_count": result.unmatched_source_count,
        "conflict_count": result.conflict_count,
        "conflicts": result.conflicts[:50],
        "updated_order_ids_sample": result.updated_order_ids[:50],
        "write_requested": bool(write),
        "write_performed": write_performed,
        "writes_parquet": write_performed,
        "write_summary": write_summary,
        "blockers": blockers,
        **SAFETY_FLAGS,
        "safety_flags": {**SAFETY_FLAGS, "writes_parquet": write_performed},
    }
    return report


def _blocked_result(
    *,
    reason: str,
    existing_events: Sequence[Mapping[str, Any]],
    conflicts: list[dict[str, Any]],
) -> ReconciliationResult:
    return ReconciliationResult(
        status="blocked",
        reason=reason,
        reconciled_events=[dict(event) for event in existing_events],
        matched_count=0,
        update_count=0,
        unchanged_count=len(existing_events),
        unmatched_existing_count=0,
        unmatched_source_count=0,
        conflict_count=len(conflicts),
        conflicts=conflicts,
        updated_order_ids=[],
    )


def _index_by_order_id(
    events: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    index: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for raw in events:
        event = dict(raw)
        order_id = normalize_identity(event.get("order_id"))
        if not order_id:
            continue
        if order_id in index:
            duplicates.add(order_id)
            continue
        index[order_id] = event
    return index, duplicates


def _find_conflicts(existing: Mapping[str, Any], source: Mapping[str, Any]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []

    existing_trade_id = normalize_identity(existing.get("trade_id"))
    source_trade_id = normalize_identity(source.get("trade_id"))
    if existing_trade_id and source_trade_id and existing_trade_id != source_trade_id:
        conflicts.append(
            {
                "field": "trade_id",
                "existing": existing_trade_id,
                "source": source_trade_id,
                "reason": "identity_mismatch",
            }
        )

    existing_reason = clean_text(existing.get("exit_reason"))
    source_reason = clean_text(source.get("exit_reason"))
    if existing_reason and source_reason and existing_reason != source_reason:
        conflicts.append(
            {
                "field": "exit_reason",
                "existing": existing_reason,
                "source": source_reason,
                "reason": "exit_reason_mismatch",
            }
        )

    comparisons = (
        (
            "symbol_norm",
            normalize_symbol(existing.get("symbol_norm") or existing.get("symbol")),
            normalize_symbol(source.get("symbol_norm") or source.get("symbol")),
        ),
        ("side", normalize_side(existing.get("side")), normalize_side(source.get("side"))),
        (
            "open_time_utc",
            normalize_time(existing.get("open_time_utc")),
            normalize_time(source.get("open_time_utc")),
        ),
        (
            "close_time_utc",
            normalize_time(existing.get("close_time_utc")),
            normalize_time(source.get("close_time_utc")),
        ),
    )
    for field, left, right in comparisons:
        if left and right and left != right:
            conflicts.append(
                {
                    "field": field,
                    "existing": left,
                    "source": right,
                    "reason": "economic_identity_mismatch",
                }
            )

    for field in ("net_pnl", "profit_ratio"):
        left = safe_float(existing.get(field))
        right = safe_float(source.get(field))
        if left is not None and right is not None and not _numbers_close(left, right):
            conflicts.append(
                {
                    "field": field,
                    "existing": left,
                    "source": right,
                    "reason": "economic_value_mismatch",
                }
            )

    return conflicts


def _enrich_event(existing: Mapping[str, Any], source: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    enriched = dict(existing)
    changed = False

    source_trade_id = normalize_identity(source.get("trade_id"))
    if source_trade_id and not normalize_identity(enriched.get("trade_id")):
        enriched["trade_id"] = source_trade_id
        changed = True

    source_reason = clean_text(source.get("exit_reason"))
    if source_reason and not clean_text(enriched.get("exit_reason")):
        enriched["exit_reason"] = source_reason
        changed = True

    if source_reason:
        for field in EXIT_CLASSIFICATION_FIELDS:
            expected = bool(source.get(field) is True)
            if _as_bool(enriched.get(field)) != expected:
                enriched[field] = expected
                changed = True

    return enriched, changed


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    try:
        return bool(value) if value is not None else False
    except (TypeError, ValueError):
        return False


def _numbers_close(left: float, right: float) -> bool:
    tolerance = max(1e-10, 1e-8 * max(abs(left), abs(right), 1.0))
    return abs(left - right) <= tolerance


def _validate_feedback_write_paths(root: Path, outcome_path: Path, feedback_path: Path) -> list[str]:
    allowed_root = (root / "data" / "feedback").resolve()
    blockers: list[str] = []
    for path in (outcome_path, feedback_path):
        try:
            path.resolve().relative_to(allowed_root)
        except ValueError:
            blockers.append(f"write_path_outside_data_feedback:{path}")
    return blockers


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path.resolve() if path.is_absolute() else (root / path).resolve()
