"""Immutable contracts for research-only Paper Capacity Scaleout V1."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


SCHEMA_VERSION = "paper_capacity_scaleout_v1"

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "read_only_by_default": True,
    "operational_authority": False,
    "capacity_activation_allowed": False,
    "changes_max_open_trades": False,
    "changes_risk": False,
    "updates_risk_manager": False,
    "changes_strategy": False,
    "changes_stake": False,
    "changes_leverage": False,
    "changes_stop": False,
    "changes_roi": False,
    "changes_model": False,
    "promotes_model": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_active_signals": False,
    "writes_active_model": False,
    "writes_active_registry": False,
    "sends_orders": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "historical_backfill": False,
    "fuzzy_matching": False,
    "timestamp_nearest_matching": False,
}


@dataclass(frozen=True)
class CapacityScaleoutConfig:
    """Frozen policy for Branch 5 research simulation."""

    baseline_commit: str
    baseline_capacity: int
    minimum_opportunity_coverage: float = 0.80
    minimum_marginal_outcomes: int = 50
    minimum_marginal_profit_factor: float = 1.10
    bootstrap_iterations: int = 5000
    bootstrap_seed: int = 20260821
    confidence_level: float = 0.95
    incremental_cost_per_trade_usdt: float = 0.02
    latency_penalty_per_trade_usdt: float = 0.00
    c3_extra_cost_per_trade_usdt: float = 0.05
    c3_loss_multiplier: float = 1.50
    c3_cluster_loss_multiplier: float = 1.25
    c3_win_retention: float = 0.70
    initial_capital_usdt: float = 1000.0
    ruin_floor_usdt: float = 500.0
    monte_carlo_iterations: int = 2000
    risk_of_ruin_cap: float = 0.05
    max_marginal_drawdown_ratio_to_baseline: float = 0.25
    c2_max_recovered_per_symbol_regime_day: int = 1

    def __post_init__(self) -> None:
        if len(self.baseline_commit.strip()) < 7:
            raise ValueError("baseline_commit_required")
        if self.baseline_capacity < 1:
            raise ValueError("baseline_capacity_must_be_positive")
        if not 0.0 <= self.minimum_opportunity_coverage <= 1.0:
            raise ValueError("minimum_opportunity_coverage_out_of_range")
        if self.minimum_marginal_outcomes < 1:
            raise ValueError("minimum_marginal_outcomes_must_be_positive")
        if self.minimum_marginal_profit_factor <= 0:
            raise ValueError("minimum_marginal_profit_factor_must_be_positive")
        if self.bootstrap_iterations < 100:
            raise ValueError("bootstrap_iterations_must_be_at_least_100")
        if self.monte_carlo_iterations < 100:
            raise ValueError("monte_carlo_iterations_must_be_at_least_100")
        if not 0.5 < self.confidence_level < 1.0:
            raise ValueError("confidence_level_out_of_range")
        if self.initial_capital_usdt <= 0:
            raise ValueError("initial_capital_usdt_must_be_positive")
        if not 0 <= self.ruin_floor_usdt < self.initial_capital_usdt:
            raise ValueError("ruin_floor_usdt_out_of_range")
        if not 0 <= self.risk_of_ruin_cap <= 1:
            raise ValueError("risk_of_ruin_cap_out_of_range")
        if self.max_marginal_drawdown_ratio_to_baseline < 0:
            raise ValueError(
                "max_marginal_drawdown_ratio_to_baseline_must_be_non_negative"
            )
        if self.c2_max_recovered_per_symbol_regime_day < 1:
            raise ValueError(
                "c2_max_recovered_per_symbol_regime_day_must_be_positive"
            )
        for name in (
            "incremental_cost_per_trade_usdt",
            "latency_penalty_per_trade_usdt",
            "c3_extra_cost_per_trade_usdt",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name}_must_be_finite_non_negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
