"""Contracts for fixed-threshold momentum walk-forward validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import pandas as pd


SCHEMA_VERSION: Final = "paper_momentum_fixed_threshold_walkforward_holdout_v1"
TIMEFRAME: Final = "5m"
RET12_THRESHOLD: Final = 0.004890587971048965
RET1_THRESHOLD: Final = 0.0013730468839541765
HOLDOUT_RATIO: Final = 0.20
WALKFORWARD_FOLD_COUNT: Final = 3
INITIAL_DEVELOPMENT_TRAIN_RATIO: Final = 0.40
MIN_ELIGIBLE_TRADES: Final = 50
MIN_HOLDOUT_TRADES: Final = 20
MIN_SELECTED_TRADES_PER_FOLD: Final = 5
MIN_SELECTED_TRADES_HOLDOUT: Final = 5
MIN_POSITIVE_WALKFORWARD_FOLDS: Final = 2

ARM_CONTROL: Final = "control_all_eligible"
ARM_RET12: Final = "momentum_ret12"
ARM_RET12_RET1: Final = "momentum_ret12_ret1"
FIXED_CANDIDATE_ARMS: Final[tuple[str, ...]] = (ARM_RET12, ARM_RET12_RET1)

HOLDOUT_INDEPENDENCE: Final[dict[str, Any]] = {
    "isolated_inside_this_validation": True,
    "historically_unseen_during_threshold_discovery": False,
    "reason": "fixed_thresholds_were_discovered_before_this_branch_on_prior_available_history",
    "interpretation": "chronological_frozen_evaluation_not_pristine_discovery_holdout",
}

SAFETY_FLAGS: Final[dict[str, bool]] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "operational_authority": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "changes_roi": False,
    "changes_stoploss": False,
    "uses_profit_protection": False,
    "searches_new_thresholds": False,
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
class MomentumFixedThresholdValidationResult:
    """In-memory fixed-threshold validation dataset and report."""

    dataset: pd.DataFrame
    report: dict[str, Any]
