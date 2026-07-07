"""Incremental watermark guard for paper autotrain quarantine microbatches."""

from .watermark import (
    build_paper_autotrain_incremental_watermark_fix_v1,
    evaluate_activation_incremental_watermark_gate,
    evaluate_incremental_watermark_gate,
    write_watermark_state,
)

__all__ = [
    "build_paper_autotrain_incremental_watermark_fix_v1",
    "evaluate_activation_incremental_watermark_gate",
    "evaluate_incremental_watermark_gate",
    "write_watermark_state",
]
