"""Paper/shadow auto-learning foundation loop."""

from .daily_foundation_runner import build_paper_autolearning_foundation_report
from .outcome_schema import OUTCOME_EVENT_COLUMNS, SAFETY_FLAGS, SCHEMA_VERSION

__all__ = [
    "OUTCOME_EVENT_COLUMNS",
    "SAFETY_FLAGS",
    "SCHEMA_VERSION",
    "build_paper_autolearning_foundation_report",
]
