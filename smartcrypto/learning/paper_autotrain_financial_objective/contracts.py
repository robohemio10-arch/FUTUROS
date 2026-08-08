"""Contracts and immutable objectives for profit-aware daily auto-training."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

FINANCIAL_OBJECTIVES = (
    "MAXIMIZAR_LUCRO_PAPER",
    "MAXIMIZAR_RETORNO_DOS_WINNERS",
    "REDUZIR_MAGNITUDE_E_FREQUENCIA_DOS_LOSSES",
)
KNOWN_FINANCIAL_SAMPLE_INVALID_IDS = frozenset({141, 258, 561, 653})
SCHEMA_VERSION = "paper_autotrain_financial_objective_v1"
DEFAULT_TRADER_MASTER = Path("data/trades/trades_master.parquet")
DEFAULT_SCORE_SOURCES = (
    Path("data/reports/financial_label_target_store_v1.json"),
    Path("data/reports/ai_shadow_quality_veto_trainer_v1.json"),
    Path("data/reports/qlib_shadow_ensemble_threshold_calibration_v1.json"),
    Path("data/features/incremental_training_microbatch.parquet"),
)
CATEGORICAL_DIMENSIONS = (
    "symbol",
    "side",
    "hour_utc",
    "day_of_week",
    "duration_bucket",
    "regime",
)
NUMERIC_DIMENSIONS = (
    "pre_entry_rsi",
    "pre_entry_atr_pct",
    "pre_entry_trend_score",
    "pre_entry_return_5",
    "pre_entry_volume_rel_30",
    "qlib_score",
    "ai_shadow_probability",
)
NO_RUNTIME_CHANGE_FLAGS: dict[str, bool] = {
    "freqtrade_runtime_changed": False,
    "roi_changed": False,
    "stoploss_changed": False,
    "risk_changed": False,
    "model_active_changed": False,
    "order_submission_changed": False,
    "containers_changed": False,
    "canary_enabled": False,
    "live_enabled": False,
    "sends_orders": False,
}


@dataclass(frozen=True)
class FinancialObjectiveResult:
    microbatch: pd.DataFrame
    summary: dict[str, Any]
