"""Profit-first financial objective for the daily paper auto-training loop."""

from .contracts import (
    FINANCIAL_OBJECTIVES,
    KNOWN_FINANCIAL_SAMPLE_INVALID_IDS,
)
from .objective import build_financial_objective, build_profit_aware_daily_autotrain
from .trainer import FinancialObjectiveTrainerBackend

__all__ = [
    "FINANCIAL_OBJECTIVES",
    "KNOWN_FINANCIAL_SAMPLE_INVALID_IDS",
    "FinancialObjectiveTrainerBackend",
    "build_financial_objective",
    "build_profit_aware_daily_autotrain",
]
