"""Paper/shadow auto-learning foundation loop."""

from .daily_foundation_runner import build_paper_autolearning_foundation_report
from .live_feedback_loop import run_paper_autolearning_live_feedback_loop_v1
from .outcome_schema import OUTCOME_EVENT_COLUMNS, SAFETY_FLAGS, SCHEMA_VERSION
from .runtime_source import load_authoritative_closed_paper_trades
from .scheduler import build_paper_autolearning_scheduler_report

__all__ = [
    "OUTCOME_EVENT_COLUMNS",
    "SAFETY_FLAGS",
    "SCHEMA_VERSION",
    "build_paper_autolearning_foundation_report",
    "build_paper_autolearning_scheduler_report",
    "load_authoritative_closed_paper_trades",
    "run_paper_autolearning_live_feedback_loop_v1",
]
