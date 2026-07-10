"""Institutional dry-run for a future paper feedback-gap backfill."""

from .dryrun import (
    DEFAULT_EXPECTED_PLAN_HASH,
    EVENT_SCHEMA_VERSION,
    SCHEMA_VERSION,
    build_dryrun_from_plan,
    build_paper_autotrain_feedback_gap_backfill_dryrun_v1,
    render_markdown,
)

__all__ = [
    "DEFAULT_EXPECTED_PLAN_HASH",
    "EVENT_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "build_dryrun_from_plan",
    "build_paper_autotrain_feedback_gap_backfill_dryrun_v1",
    "render_markdown",
]
