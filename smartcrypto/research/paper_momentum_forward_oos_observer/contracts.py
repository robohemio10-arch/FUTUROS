"""Contracts for pristine forward OOS observation of the frozen momentum filter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import pandas as pd

from smartcrypto.research.paper_momentum_fixed_threshold_walkforward_holdout.contracts import (
    RET1_THRESHOLD,
    RET12_THRESHOLD,
)

SCHEMA_VERSION: Final = "paper_momentum_forward_oos_observer_v1"
TIMEFRAME: Final = "5m"
FORWARD_FREEZE_COMMIT: Final = "ed4efef093120786bd2b417ecb8d068373879679"
FORWARD_START_UTC_TEXT: Final = "2026-08-10T00:51:10Z"
FORWARD_START_UTC: Final = pd.Timestamp(FORWARD_START_UTC_TEXT)
FROZEN_FILTER_ID: Final = "momentum_ret12_ret1"
MIN_FORWARD_CANDIDATE_TRADES: Final = 30
MIN_FEATURE_COVERAGE_RATIO: Final = 1.0

FROZEN_FILTER_CONDITION: Final[dict[str, Any]] = {
    "operator": "and",
    "conditions": [
        {
            "field": "entry_return_12",
            "operator": "gte",
            "value": RET12_THRESHOLD,
        },
        {
            "field": "entry_return_1",
            "operator": "gte",
            "value": RET1_THRESHOLD,
        },
    ],
}

SAFETY_FLAGS: Final[dict[str, bool]] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "operational_authority": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "blocks_entries": False,
    "uses_profit_protection": False,
    "searches_new_thresholds": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_roi": False,
    "changes_stoploss": False,
    "writes_runtime": False,
    "writes_master": False,
    "writes_sqlite": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "model_promotion_performed": False,
    "deploy_performed": False,
    "pr_created": False,
    "merge_performed": False,
}


@dataclass(frozen=True)
class MomentumForwardOOSResult:
    """In-memory forward OOS observation dataset and immutable report."""

    dataset: pd.DataFrame
    report: dict[str, Any]
