"""Typed contracts for W9 Risk Intelligence + Treasury research.

This package is an offline, shadow-only risk simulation layer.  It has no
operational authority over RiskManager, Freqtrade, order submission, active
signals, private exchange APIs, or model promotion.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

REQUEST_SCHEMA_VERSION: Literal["risk_intelligence_request_v1"] = (
    "risk_intelligence_request_v1"
)
SNAPSHOT_SCHEMA_VERSION: Literal["risk_intelligence_snapshot_v1"] = (
    "risk_intelligence_snapshot_v1"
)

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=180,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,179}$",
    ),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ProtectionState(str, Enum):
    NORMAL = "NORMAL"
    CONSERVATIVE = "CONSERVATIVE"
    PROTECTION = "PROTECTION"
    REDUCE_ONLY = "REDUCE_ONLY"
    PAUSED = "PAUSED"
    PANIC = "PANIC"


class SnapshotStatus(str, Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


class FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        allow_inf_nan=False,
    )


def require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    if offset.total_seconds() != 0:
        raise ValueError("timestamp_must_use_utc_offset_zero")
    return value.astimezone(timezone.utc)


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported_json_type:{type(value).__name__}")


class SafetyContract(FrozenContract):
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False
    network_required: Literal[False] = False
    network_calls_executed: Literal[False] = False
    risk_budget_operationally_applied: Literal[False] = False
    treasury_operationally_applied: Literal[False] = False
    riskmanager_final_authority: Literal[True] = True
    live: Literal[False] = False
    canary: Literal[False] = False


class HistoricalReturnObservation(FrozenContract):
    observation_id: Identifier
    event_time_utc: datetime
    available_at_utc: datetime
    net_return_bps: float = Field(ge=-10_000, le=100_000)
    source_hash: Sha256Hex

    @field_validator("event_time_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_ordering(self) -> "HistoricalReturnObservation":
        if self.event_time_utc > self.available_at_utc:
            raise ValueError("return_event_after_available")
        return self


class CurrentRiskObservation(FrozenContract):
    available_at_utc: datetime
    day_pnl_bps: float = Field(ge=-100_000, le=100_000)
    current_drawdown_bps: float = Field(ge=0, le=100_000)
    gross_exposure_ratio: float = Field(ge=0, le=100)
    concentration_ratio: float = Field(ge=0, le=1)
    open_positions: int = Field(ge=0, le=100_000)
    source_hash: Sha256Hex

    @field_validator("available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class StressConfig(FrozenContract):
    seed: int = Field(default=1337, ge=0, le=2_147_483_647)
    simulation_count: int = Field(default=1000, ge=100, le=100_000)
    horizon_observations: int | None = Field(default=None, ge=10, le=100_000)
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1.0)
    ruin_equity_ratio: float = Field(default=0.70, gt=0, lt=1)
    fee_slippage_stress_bps: float = Field(default=2.0, ge=0, le=500)
    fat_tail_loss_multiplier: float = Field(default=1.75, ge=1, le=10)
    fat_tail_gain_multiplier: float = Field(default=0.85, gt=0, le=1)
    low_liquidity_loss_multiplier: float = Field(default=1.35, ge=1, le=10)
    low_liquidity_gain_multiplier: float = Field(default=0.80, gt=0, le=1)


class DailyBudgetConfig(FrozenContract):
    base_budget_bps: float = Field(default=100.0, gt=0, le=10_000)
    target_cvar_bps: float = Field(default=150.0, gt=0, le=10_000)
    target_mc_p95_drawdown_bps: float = Field(default=500.0, gt=0, le=50_000)
    max_gross_exposure_ratio: float = Field(default=1.0, gt=0, le=100)
    max_concentration_ratio: float = Field(default=0.60, gt=0, le=1)
    min_history_observations: int = Field(default=30, ge=10, le=100_000)
    conservative_multiplier: float = Field(default=0.75, ge=0, le=1)
    protection_multiplier: float = Field(default=0.50, ge=0, le=1)
    reduce_only_multiplier: float = Field(default=0.25, ge=0, le=1)


class CircuitConfig(FrozenContract):
    conservative_pressure: float = Field(default=0.50, gt=0)
    protection_pressure: float = Field(default=0.75, gt=0)
    reduce_only_pressure: float = Field(default=1.00, gt=0)
    paused_pressure: float = Field(default=1.25, gt=0)
    panic_pressure: float = Field(default=1.50, gt=0)
    recovery_pressure: float = Field(default=0.35, ge=0)

    @model_validator(mode="after")
    def _validate_monotonic(self) -> "CircuitConfig":
        ordered = (
            self.conservative_pressure,
            self.protection_pressure,
            self.reduce_only_pressure,
            self.paused_pressure,
            self.panic_pressure,
        )
        if any(right <= left for left, right in zip(ordered, ordered[1:])):
            raise ValueError("circuit_thresholds_must_be_strictly_increasing")
        if self.recovery_pressure >= self.conservative_pressure:
            raise ValueError("recovery_pressure_must_be_below_conservative_pressure")
        return self


class TreasuryScenario(FrozenContract):
    strategy_start_equity_usdt: float = Field(gt=0)
    reserve_start_usdt: float = Field(ge=0)
    strategy_pnl_usdt: float
    liquidity_floor_usdt: float = Field(ge=0)
    max_transfer_usdt: float = Field(default=0.0, ge=0)
    min_reserve_remaining_usdt: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _validate_treasury(self) -> "TreasuryScenario":
        if self.min_reserve_remaining_usdt > self.reserve_start_usdt:
            raise ValueError("minimum_reserve_exceeds_start_reserve")
        return self


class RiskIntelligenceRequest(FrozenContract):
    schema_version: Literal["risk_intelligence_request_v1"] = REQUEST_SCHEMA_VERSION
    request_id: Identifier
    decision_time_utc: datetime
    current_state: ProtectionState = ProtectionState.NORMAL
    historical_returns: tuple[HistoricalReturnObservation, ...] = Field(
        min_length=10,
        max_length=100_000,
    )
    current_risk: CurrentRiskObservation
    stress: StressConfig = StressConfig()
    budget: DailyBudgetConfig = DailyBudgetConfig()
    circuit: CircuitConfig = CircuitConfig()
    treasury: TreasuryScenario | None = None

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_request(self) -> "RiskIntelligenceRequest":
        seen: set[str] = set()
        previous: tuple[datetime, str] | None = None
        for item in self.historical_returns:
            if item.observation_id in seen:
                raise ValueError("duplicate_return_observation_id")
            seen.add(item.observation_id)
            key = (item.available_at_utc, item.observation_id)
            if previous is not None and key < previous:
                raise ValueError("historical_returns_not_available_time_ordered")
            previous = key
        return self


class StressScenarioMetrics(FrozenContract):
    scenario: Identifier
    observation_count: int = Field(ge=1)
    empirical_var_bps: float
    empirical_cvar_bps: float
    monte_carlo_p95_max_drawdown_bps: float = Field(ge=0)
    monte_carlo_loss_probability: float = Field(ge=0, le=1)
    monte_carlo_ruin_probability: float = Field(ge=0, le=1)
    monte_carlo_terminal_cvar_bps: float


class StressReport(FrozenContract):
    status: SnapshotStatus
    reason: str = Field(min_length=1, max_length=240)
    point_in_time_valid: bool
    valid_observation_count: int = Field(ge=0)
    future_observation_count: int = Field(ge=0)
    scenario_metrics: tuple[StressScenarioMetrics, ...]
    worst_scenario: Identifier | None
    worst_cvar_bps: float | None
    worst_p95_drawdown_bps: float | None = Field(default=None, ge=0)
    max_ruin_probability: float | None = Field(default=None, ge=0, le=1)


class CircuitDecision(FrozenContract):
    prior_state: ProtectionState
    required_state: ProtectionState
    next_state: ProtectionState
    risk_pressure: float = Field(ge=0)
    escalation: bool
    deescalation: bool
    deescalation_limited_to_one_step: Literal[True] = True
    reason: str = Field(min_length=1, max_length=240)


class DailyBudgetDecision(FrozenContract):
    protection_state: ProtectionState
    base_budget_bps: float = Field(ge=0)
    stress_multiplier: float = Field(ge=0, le=1)
    state_multiplier: float = Field(ge=0, le=1)
    calibrated_budget_bps: float = Field(ge=0)
    remaining_daily_budget_bps: float = Field(ge=0)
    new_risk_allowed: bool
    reduce_only: bool
    calibration_method: Literal["stress_mc_cvar_v1"] = "stress_mc_cvar_v1"
    operationally_applied: Literal[False] = False


class TreasurySimulation(FrozenContract):
    strategy_equity_before_transfer_usdt: float
    reserve_before_transfer_usdt: float = Field(ge=0)
    reserve_transfer_usdt: float = Field(ge=0)
    strategy_equity_after_transfer_usdt: float
    reserve_after_transfer_usdt: float = Field(ge=0)
    total_economic_equity_usdt: float
    strategy_pnl_for_performance_usdt: float
    reserve_transfer_included_in_strategy_pnl: Literal[False] = False
    reserve_can_mask_negative_expectancy: Literal[False] = False
    operationally_applied: Literal[False] = False
    reason: str = Field(min_length=1, max_length=240)


class RiskIntelligenceSnapshot(FrozenContract):
    schema_version: Literal["risk_intelligence_snapshot_v1"] = SNAPSHOT_SCHEMA_VERSION
    snapshot_id: Identifier
    request_id: Identifier
    decision_time_utc: datetime
    created_at_utc: datetime
    status: SnapshotStatus
    reason: str = Field(min_length=1, max_length=240)
    stress_report: StressReport
    circuit_decision: CircuitDecision | None
    daily_budget: DailyBudgetDecision | None
    treasury: TreasurySimulation | None
    riskmanager_final_authority: Literal[True] = True
    safety: SafetyContract = SafetyContract()

    @field_validator("decision_time_utc", "created_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)
