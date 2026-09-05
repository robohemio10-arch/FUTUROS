"""Closed-paper-trade -> outcome -> feedback -> microbatch integration loop.

The loop is intentionally Paper-only and fail-closed.  It resolves the freshest
read-only Paper SQLite source, reuses the canonical feedback normalization and
deduplication contract, and materializes only feedback/microbatch artifacts when
explicitly requested.  It never writes SQLite, submits orders, changes risk, or
promotes a model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .feedback_store import (
    build_feedback_events,
    read_existing_outcome_events,
    write_feedback_outputs,
)
from .microbatch_builder import build_daily_microbatch
from .outcome_schema import (
    DEFAULT_FEEDBACK_STORE,
    DEFAULT_MICROBATCH_DIR,
    DEFAULT_OUTCOME_EVENTS,
    SAFETY_FLAGS,
)
from .runtime_source import load_authoritative_closed_paper_trades

SCHEMA_VERSION = "paper_autolearning_live_feedback_loop_v1"


def run_paper_autolearning_live_feedback_loop_v1(
    *,
    project_root: str | Path,
    explicit_paper_db_path: str | Path | None = None,
    write: bool = False,
) -> dict[str, Any]:
    """Run one incremental Paper feedback iteration.

    ``write=False`` is a complete dry-run.  ``write=True`` may only write the
    canonical feedback/outcome Parquets and a training microbatch Parquet.
    """

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

    feedback = build_feedback_events(
        project_root=root,
        closed_trade_rows=selection.rows,
        existing_outcome_path=outcome_path,
    )
    existing_events = read_existing_outcome_events(outcome_path)
    microbatch = build_daily_microbatch(
        feedback.new_events,
        output_dir=microbatch_dir,
        write=write,
    )

    write_result: dict[str, Any] = {
        "outcome_events_rows": len(existing_events),
        "feedback_rows": len(existing_events),
    }
    if write and feedback.new_events:
        write_result = write_feedback_outputs(
            feedback_store_path=feedback_path,
            outcome_events_path=outcome_path,
            existing_events=existing_events,
            new_events=feedback.new_events,
        )

    status = "ok"
    reason = "incremental_feedback_materialized" if feedback.new_events else "no_new_closed_paper_trades"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "paper_source_status": selection.status,
        "paper_source_reason": selection.reason,
        "paper_source_path": str(selection.selected_path),
        "paper_source_rows": len(selection.rows),
        "valid_outcome_event_count": len(feedback.valid_events),
        "rejected_outcome_event_count": len(feedback.rejected_rows),
        "new_outcome_event_count": len(feedback.new_events),
        "duplicate_outcome_event_count": len(feedback.duplicate_events),
        "microbatch_status": microbatch["status"],
        "microbatch_reason": microbatch["reason"],
        "microbatch_rows": microbatch["microbatch_rows"],
        "microbatch_output_path": microbatch["microbatch_output_path"],
        "feature_columns": microbatch["feature_columns"],
        "label_columns": microbatch["label_columns"],
        "feedback_store_path": str(feedback_path),
        "outcome_events_path": str(outcome_path),
        "write_requested": bool(write),
        "write_performed": bool(write and feedback.new_events),
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
        "new_outcome_event_count": 0,
        "duplicate_outcome_event_count": 0,
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


def _candidate_to_dict(candidate: Any) -> dict[str, Any]:
    return {
        "path": str(candidate.path),
        "status": candidate.status,
        "closed_trade_count": candidate.closed_trade_count,
        "max_close_time_utc": candidate.max_close_time_utc,
        "mtime_utc": candidate.mtime_utc,
        "reason": candidate.reason,
    }
