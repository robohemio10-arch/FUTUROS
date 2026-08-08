"""Contracts for profit-first paper trading research."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import pandas as pd

SCHEMA_VERSION: Final = "paper_profit_maximization_trader_master_qlib_v1"
KNOWN_CORRUPT_PAPER_TRADE_IDS: Final = frozenset({141, 258, 561, 653})

ENTRY_FEATURE_COLUMNS: Final = (
    "entry_return_1",
    "entry_return_3",
    "entry_return_6",
    "entry_return_12",
    "entry_return_24",
    "entry_rolling_volatility_24",
    "entry_atr_normalized_14",
    "entry_relative_range",
    "entry_volume_relative_20",
    "entry_distance_from_ma20",
    "entry_slope_6",
    "entry_momentum_6",
    "entry_trend_regime",
    "entry_volatility_regime",
    "entry_candle_direction",
    "entry_body_ratio",
    "entry_upper_wick_ratio",
    "entry_lower_wick_ratio",
    "entry_distance_local_high_20",
    "entry_distance_local_low_20",
    "entry_hour_utc",
    "entry_day_of_week",
)

NUMERIC_ENTRY_FEATURES: Final = tuple(
    column
    for column in ENTRY_FEATURE_COLUMNS
    if column
    not in {
        "entry_trend_regime",
        "entry_volatility_regime",
        "entry_candle_direction",
        "entry_day_of_week",
        "entry_hour_utc",
    }
)

CATEGORICAL_ENTRY_FEATURES: Final = (
    "symbol",
    "side",
    "entry_trend_regime",
    "entry_volatility_regime",
    "entry_candle_direction",
    "entry_hour_utc",
    "entry_day_of_week",
)

SCORE_FIELDS: Final = ("qlib_rank_score", "ai_shadow_score", "ensemble_score")

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
}


@dataclass(frozen=True)
class ProfitMaximizationResult:
    dataset: pd.DataFrame
    report: dict[str, Any]
