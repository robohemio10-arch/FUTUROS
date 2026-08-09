"""Contracts for causal path-faithful paper profit-protection validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import pandas as pd


SCHEMA_VERSION: Final = "paper_profit_protection_path_faithful_walkforward_v1"
HOLDOUT_RATIO: Final = 0.20
WALKFORWARD_FOLD_COUNT: Final = 3
INITIAL_DEVELOPMENT_TRAIN_RATIO: Final = 0.40
TIMEFRAME_PREFERENCE: Final[tuple[str, ...]] = ("1m", "5m")
EXIT_SLIPPAGE_BPS: Final = 10.0
MIN_ELIGIBLE_TRADES: Final = 50
MIN_HOLDOUT_TRADES: Final = 20
MIN_WALKFORWARD_POSITIVE_FOLDS: Final = 2

FIXED_PROTECTION_CANDIDATES: Final[tuple[dict[str, float | str], ...]] = (
    {
        "candidate_id": "trigger_10bps__retain_75pct_mfe",
        "trigger_mfe_pct": 0.0010,
        "retention_fraction_of_mfe": 0.75,
    },
    {
        "candidate_id": "trigger_10bps__net_breakeven",
        "trigger_mfe_pct": 0.0010,
        "retention_fraction_of_mfe": 0.00,
    },
    {
        "candidate_id": "trigger_10bps__retain_50pct_mfe",
        "trigger_mfe_pct": 0.0010,
        "retention_fraction_of_mfe": 0.50,
    },
    {
        "candidate_id": "trigger_25bps__retain_75pct_mfe",
        "trigger_mfe_pct": 0.0025,
        "retention_fraction_of_mfe": 0.75,
    },
)

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
class PathFaithfulValidationResult:
    """In-memory validation dataset and immutable report."""

    dataset: pd.DataFrame
    report: dict[str, Any]
