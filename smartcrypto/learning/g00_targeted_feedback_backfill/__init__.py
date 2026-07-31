"""Target-scoped G00 paper-feedback backfill with explicit authorization."""

from .contracts import CONFIRMATION_TEXT, TARGET_TRADE_IDS
from .orchestrator import run_g00_targeted_feedback_backfill_v1

__all__ = [
    "CONFIRMATION_TEXT",
    "TARGET_TRADE_IDS",
    "run_g00_targeted_feedback_backfill_v1",
]
