"""Paper/shadow auto-learning foundation loop."""

from .continuous_orchestrator import run_paper_autolearning_continuous_orchestrator_v1
from .daily_foundation_runner import build_paper_autolearning_foundation_report
from .economic_challenger_scorecard import (
    build_paper_autolearning_economic_challenger_scorecard_v1,
)
from .economic_oos_validation import build_paper_autolearning_economic_oos_validation_v1
from .live_feedback_loop import run_paper_autolearning_live_feedback_loop_v1
from .outcome_schema import OUTCOME_EVENT_COLUMNS, SAFETY_FLAGS, SCHEMA_VERSION
from .runtime_source import load_authoritative_closed_paper_trades
from .scheduler import build_paper_autolearning_scheduler_report

__all__ = [
    "OUTCOME_EVENT_COLUMNS",
    "SAFETY_FLAGS",
    "SCHEMA_VERSION",
    "build_paper_autolearning_economic_challenger_scorecard_v1",
    "build_paper_autolearning_economic_oos_validation_v1",
    "build_paper_autolearning_foundation_report",
    "build_paper_autolearning_scheduler_report",
    "load_authoritative_closed_paper_trades",
    "run_paper_autolearning_continuous_orchestrator_v1",
    "run_paper_autolearning_live_feedback_loop_v1",
]
