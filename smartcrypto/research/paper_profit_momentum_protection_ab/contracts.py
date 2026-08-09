"""Versioned contracts for profit-first momentum/protection A/B research."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import pandas as pd


SCHEMA_VERSION: Final = "paper_profit_momentum_protection_ab_v1"
RET12_THRESHOLD: Final = 0.004890587971048965
RET1_THRESHOLD: Final = 0.0013730468839541765
TEMPORAL_TRAIN_RATIO: Final = 0.70

PROTECTION_TRIGGER_PCTS: Final = (
    0.0010,
    0.0025,
    0.0050,
    0.0075,
    0.0100,
)
PROTECTION_RETENTION_FRACTIONS: Final = (
    0.00,
    0.25,
    0.50,
    0.75,
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
}


@dataclass(frozen=True)
class MomentumProtectionABResult:
    """In-memory A/B dataset and immutable research report."""

    dataset: pd.DataFrame
    report: dict[str, Any]
