"""Closed Paper trade -> reconciled outcome -> feedback -> microbatch loop.

Each iteration resolves the freshest authoritative Paper SQLite source read-only,
reconciles legacy outcomes by deterministic order identity, appends only unseen
closed trades, and optionally materializes canonical feedback/microbatch files.
The loop never writes SQLite, submits orders, changes risk, or promotes models.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from .feedback_store import (
    build_feedback_events,
    clean_text,
    normalize_identity,
    read_existing_outcome_events,
    write_feedback_outputs,
)
from .lineage_reconciliation import build_lineage_reconciliation
from .microbatch_builder import build_daily_microbatch
from .outcome_schema import (
    DEFAULT_FEEDBACK_STORE,
    DEFAULT_MICROBATCH_DIR,
    DEFAULT_OUTCOME_EVENTS,
    SAFETY_FLAGS,
)
from .runtime_source import load_authoritative_closed_paper_trades

SCHEMA_VERSION = "paper_autolearning_live_feedback_loop_v2"


def run_paper_autolearning_live_feedback_loop_v1(
    *,
    project_root: str | Path,
    explicit_paper_db_path: str | Path | None = None,
    write: bool = False,
) -> dict[str, Any]:
    """Run one idempotent, economically reconciled Paper feedback iteration."""

    root = Path(project_root).resolve()
    selection = load_authoritative_closed_paper_trades(
        project_root=root,
        explicit_path=explicit_paper_db_path,
    )
    if selection.status != "ok" or selection.selected_path is None:
        return _blocked_report(
            reason=selection.reason,
            selected_path=selection.selected_path,
            source_rows=len(selection.rows),
            candidates=selection.candidates,
            write=write,
        )

    outcome_path = root / DEFAULT_OUTCOME_EVENTS
    feedback_path = root / DEFAULT_FEEDBACK_STORE
    microbatch_dir = root / DEFAULT_MICROBATCH_DIR
    existing_events = read_existing_outcome_events(outcome_path)

    reconciliation = build_lineage_reconciliation(
        existing_events=existing_events,
        source_rows=selection.rows,
    )
    if reconciliation.status != "ok":
        return _blocked_reconciliation_report(
            selection=selection,
            reconciliation=reconciliation,
            existing_events=existing_events,
            write=write,
        )

    feedback = build_feedback_events(
        project_root=root,
        closed_trade_rows=selection.rows,
        existing_outcome_path=outcome_path,
    )
    projected_events = [
        *[dict(event) for event in reconciliation.reconciled_events],
        *[dict(event) for event in feedback.new_events],
    ]

    identity_blockers = _projected_identity_blockers(projected_events)
    if identity_blockers:
        return _blocked_identity_report(
            selection=selection,
            reconciliation=reconciliation,
            feedback=feedback,
            existing_events=existing_events,
            projected_events=projected_events,
            blockers=identity_blockers,
            write=write,
        )

    microbatch = build_daily_microbatch(
        feedback.new_events,
        output_dir=microbatch_dir,
        write=bool(write and feedback.new_events),
    )

    should_write_feedback = bool(
        write and (feedback.new_events or reconciliation.update_count > 0)
    )
    write_result: dict[str, Any] = {
        "outcome_events_rows": len(existing_events),
        "feedback_rows": len(existing_events),
    }
    if should_write_feedback:
        write_result = write_feedback_outputs(
            feedback_store_path=feedback_path,
            outcome_events_path=outcome_path,
            existing_events=reconciliation.reconciled_events,
            new_events=feedback.new_events,
        )

    if feedback.new_events:
        reason = "incremental_feedback_materialized"
    elif reconciliation.update_count > 0:
        reason = "legacy_outcomes_reconciled"
    else:
        reason = "no_new_closed_paper_trades"

    coverage = _coverage_payload(projected_events)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason": reason,
        "paper_source_status": selection.status,
        "paper_source_reason": selection.reason,
        "paper_source_path": str(selection.selected_path),
        "paper_source_rows": len(selection.rows),
        "valid_outcome_event_count": len(feedback.valid_events),
        "rejected_outcome_event_count": len(feedback.rejected_rows),
        "existing_outcome_event_count": len(existing_events),
        "lineage_status": reconciliation.status,
        "lineage_reason": reconciliation.reason,
        "lineage_matched_count": reconciliation.matched_count,
        "lineage_update_count": reconciliation.update_count,
        "lineage_unchanged_count": reconciliation.unchanged_count,
        "lineage_unmatched_existing_count": reconciliation.unmatched_existing_count,
        "lineage_unmatched_source_count": reconciliation.unmatched_source_count,
        "lineage_conflict_count": reconciliation.conflict_count,
        "new_outcome_event_count": len(feedback.new_events),
        "duplicate_outcome_event_count": len(feedback.duplicate_events),
        "projected_outcome_event_count": len(projected_events),
        "projected_duplicate_order_id_count": _duplicate_identity_count(
            projected_events,
            "order_id",
        ),
        "projected_duplicate_trade_id_count": _duplicate_identity_count(
            projected_events,
            "trade_id",
        ),
        **coverage,
        "microbatch_status": microbatch["status"],
        "microbatch_reason": microbatch["reason"],
        "microbatch_rows": microbatch["microbatch_rows"],
        "microbatch_output_path": microbatch["microbatch_output_path"],
        "feature_columns": microbatch["feature_columns"],
        "label_columns": microbatch["label_columns"],
        "feedback_store_path": str(feedback_path),
        "outcome_events_path": str(outcome_path),
        "write_requested": bool(write),
        "write_performed": should_write_feedback,
        "write_result": write_result,
        "source_candidates": [_candidate_to_dict(item) for item in selection.candidates],
        **SAFETY_FLAGS,
    }


def _blocked_report(
    *,
    reason: str,
    selected_path: Path | None,
    source_rows: int,
    candidates: tuple[Any, ...],
    write: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": reason,
        "paper_source_status": "blocked",
        "paper_source_path": str(selected_path) if selected_path else None,
        "paper_source_rows": source_rows,
        "valid_outcome_event_count": 0,
        "rejected_outcome_event_count": 0,
        "existing_outcome_event_count": 0,
        "lineage_status": "blocked",
        "lineage_reason": "paper_source_not_ready",
        "lineage_matched_count": 0,
        "lineage_update_count": 0,
        "lineage_unchanged_count": 0,
        "lineage_unmatched_existing_count": 0,
        "lineage_unmatched_source_count": 0,
        "lineage_conflict_count": 0,
        "new_outcome_event_count": 0,
        "duplicate_outcome_event_count": 0,
        "projected_outcome_event_count": 0,
        "projected_duplicate_order_id_count": 0,
        "projected_duplicate_trade_id_count": 0,
        **_empty_coverage_payload(),
        "microbatch_status": "blocked",
        "microbatch_reason": "paper_source_not_ready",
        "microbatch_rows": 0,
        "microbatch_output_path": None,
        "feature_columns": [],
        "label_columns": [],
        "write_requested": bool(write),
        "write_performed": False,
        "source_candidates": [_candidate_to_dict(item) for item in candidates],
        **SAFETY_FLAGS,
    }


def _blocked_reconciliation_report(
    *,
    selection: Any,
    reconciliation: Any,
    existing_events: Sequence[Mapping[str, Any]],
    write: bool,
) -> dict[str, Any]:
    return {
        **_blocked_report(
            reason=reconciliation.reason,
            selected_path=selection.selected_path,
            source_rows=len(selection.rows),
            candidates=selection.candidates,
            write=write,
        ),
        "paper_source_status": selection.status,
        "existing_outcome_event_count": len(existing_events),
        "lineage_status": reconciliation.status,
        "lineage_reason": reconciliation.reason,
        "lineage_matched_count": reconciliation.matched_count,
        "lineage_update_count": reconciliation.update_count,
        "lineage_unchanged_count": reconciliation.unchanged_count,
        "lineage_unmatched_existing_count": reconciliation.unmatched_existing_count,
        "lineage_unmatched_source_count": reconciliation.unmatched_source_count,
        "lineage_conflict_count": reconciliation.conflict_count,
        "lineage_conflicts": reconciliation.conflicts[:50],
    }


def _blocked_identity_report(
    *,
    selection: Any,
    reconciliation: Any,
    feedback: Any,
    existing_events: Sequence[Mapping[str, Any]],
    projected_events: Sequence[Mapping[str, Any]],
    blockers: list[str],
    write: bool,
) -> dict[str, Any]:
    return {
        **_blocked_report(
            reason="projected_outcome_identity_collision",
            selected_path=selection.selected_path,
            source_rows=len(selection.rows),
            candidates=selection.candidates,
            write=write,
        ),
        "paper_source_status": selection.status,
        "existing_outcome_event_count": len(existing_events),
        "valid_outcome_event_count": len(feedback.valid_events),
        "rejected_outcome_event_count": len(feedback.rejected_rows),
        "lineage_status": reconciliation.status,
        "lineage_reason": reconciliation.reason,
        "lineage_matched_count": reconciliation.matched_count,
        "lineage_update_count": reconciliation.update_count,
        "lineage_unchanged_count": reconciliation.unchanged_count,
        "lineage_unmatched_existing_count": reconciliation.unmatched_existing_count,
        "lineage_unmatched_source_count": reconciliation.unmatched_source_count,
        "lineage_conflict_count": reconciliation.conflict_count,
        "new_outcome_event_count": len(feedback.new_events),
        "duplicate_outcome_event_count": len(feedback.duplicate_events),
        "projected_outcome_event_count": len(projected_events),
        "projected_duplicate_order_id_count": _duplicate_identity_count(
            projected_events,
            "order_id",
        ),
        "projected_duplicate_trade_id_count": _duplicate_identity_count(
            projected_events,
            "trade_id",
        ),
        **_coverage_payload(projected_events),
        "blockers": blockers,
    }


def _coverage_payload(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "projected_trade_id_coverage": _coverage(events, "trade_id"),
        "projected_quantity_coverage": _coverage(events, "quantity"),
        "projected_notional_coverage": _coverage(events, "notional"),
        "projected_trading_fee_coverage": _coverage(events, "trading_fee"),
        "projected_funding_fee_coverage": _coverage(events, "funding_fee"),
        "projected_net_pnl_coverage": _coverage(events, "net_pnl"),
    }


def _empty_coverage_payload() -> dict[str, float]:
    return {
        "projected_trade_id_coverage": 0.0,
        "projected_quantity_coverage": 0.0,
        "projected_notional_coverage": 0.0,
        "projected_trading_fee_coverage": 0.0,
        "projected_funding_fee_coverage": 0.0,
        "projected_net_pnl_coverage": 0.0,
    }


def _coverage(events: Sequence[Mapping[str, Any]], field: str) -> float:
    if not events:
        return 0.0
    present = sum(1 for event in events if not _missing(event.get(field)))
    return round(present / len(events), 10)


def _projected_identity_blockers(
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    duplicate_orders = _duplicate_identity_count(events, "order_id")
    duplicate_trades = _duplicate_identity_count(events, "trade_id")
    if duplicate_orders:
        blockers.append(f"duplicate_order_id_count:{duplicate_orders}")
    if duplicate_trades:
        blockers.append(f"duplicate_trade_id_count:{duplicate_trades}")
    return blockers


def _duplicate_identity_count(
    events: Sequence[Mapping[str, Any]],
    field: str,
) -> int:
    values = [
        normalize_identity(event.get(field))
        for event in events
        if normalize_identity(event.get(field))
    ]
    counts = Counter(values)
    return sum(count - 1 for count in counts.values() if count > 1)


def _missing(value: object) -> bool:
    return clean_text(value) is None


def _candidate_to_dict(candidate: Any) -> dict[str, Any]:
    return {
        "path": str(candidate.path),
        "status": candidate.status,
        "closed_trade_count": candidate.closed_trade_count,
        "max_close_time_utc": candidate.max_close_time_utc,
        "mtime_utc": candidate.mtime_utc,
        "reason": candidate.reason,
    }
