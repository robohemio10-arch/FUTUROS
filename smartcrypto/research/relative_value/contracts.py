"""Typed, point-in-time contracts for W7 Relative Value research.

This package is research/shadow only.  It performs scenario accounting and
neutrality checks; it has no order, risk, model, or exchange-private authority.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

SCHEMA_VERSION: Literal["relative_value_v1"] = "relative_value_v1"
REQUEST_SCHEMA_VERSION: Literal["relative_value_request_v1"] = "relative_value_request_v1"
SNAPSHOT_SCHEMA_VERSION: Literal["relative_value_snapshot_v1"] = "relative_value_snapshot_v1"

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


class RelativeValueStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


class CandidateStatus(str, Enum):
    RESEARCH_EVALUATED = "RESEARCH_EVALUATED"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    BLOCKED = "BLOCKED"


class RebalanceState(str, Enum):
    NO_REBALANCE = "NO_REBALANCE"
    REBALANCE_RESEARCH = "REBALANCE_RESEARCH"
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
    live: Literal[False] = False
    canary: Literal[False] = False


class PriceObservation(FrozenContract):
    observation_id: Identifier
    source_id: Identifier
    exchange: Identifier
    symbol: Identifier
    market_type: Literal["spot", "perp", "index"]
    price: float = Field(gt=0)
    event_time_utc: datetime
    available_at_utc: datetime
    source_hash: Sha256Hex

    @field_validator("event_time_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "PriceObservation":
        if self.event_time_utc > self.available_at_utc:
            raise ValueError("price_event_after_available")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.available_at_utc > decision:
            errors.append("price_available_after_decision")
        if self.event_time_utc > decision:
            errors.append("price_event_after_decision")
        return tuple(errors)


class FundingObservation(FrozenContract):
    observation_id: Identifier
    source_id: Identifier
    exchange: Identifier
    symbol: Identifier
    funding_rate: float = Field(ge=-0.05, le=0.05)
    rate_kind: Literal["predicted", "realized"]
    funding_time_utc: datetime
    observed_at_utc: datetime
    available_at_utc: datetime
    source_hash: Sha256Hex

    @field_validator("funding_time_utc", "observed_at_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "FundingObservation":
        if self.observed_at_utc > self.available_at_utc:
            raise ValueError("funding_observed_after_available")
        if self.rate_kind == "realized" and self.funding_time_utc > self.available_at_utc:
            raise ValueError("realized_funding_not_yet_available")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.available_at_utc > decision:
            errors.append("funding_available_after_decision")
        if self.observed_at_utc > decision:
            errors.append("funding_observed_after_decision")
        if self.rate_kind == "realized" and self.funding_time_utc > decision:
            errors.append("realized_funding_after_decision")
        return tuple(errors)


class CostModel(FrozenContract):
    entry_cost_bps_per_leg: float = Field(ge=0, le=500)
    exit_cost_bps_per_leg: float = Field(ge=0, le=500)
    slippage_bps_per_leg_round_trip: float = Field(ge=0, le=500)
    leg_risk_penalty_bps: float = Field(ge=0, le=1000)

    def weighted_round_trip_bps(
        self,
        *,
        leg_a_weight: float = 1.0,
        leg_b_weight: float = 1.0,
    ) -> float:
        if leg_a_weight <= 0 or leg_b_weight <= 0:
            raise ValueError("cost_leg_weights_must_be_positive")
        per_leg = (
            self.entry_cost_bps_per_leg
            + self.exit_cost_bps_per_leg
            + self.slippage_bps_per_leg_round_trip
        )
        return (leg_a_weight + leg_b_weight) * per_leg + self.leg_risk_penalty_bps

    def two_leg_round_trip_bps(self) -> float:
        return self.weighted_round_trip_bps()


class BasisScenario(FrozenContract):
    scenario_id: Identifier
    strategy_id: Identifier
    research_objective: Literal["basis_convergence", "funding_carry"] = "basis_convergence"
    spot: PriceObservation
    perp: PriceObservation
    funding: FundingObservation | None = None
    holding_hours: float = Field(gt=0, le=24 * 30)
    funding_interval_hours: float = Field(default=8.0, gt=0, le=24)
    convergence_capture_fraction: float = Field(ge=0, le=1)
    hedge_ratio: float = Field(default=1.0, gt=0, le=10)
    max_delta_residual: float = Field(default=0.05, gt=0, le=2)
    rebalance_tolerance: float = Field(default=0.05, gt=0, le=2)
    prior_hedge_ratio: float | None = Field(default=None, gt=0, le=10)
    cost_model: CostModel

    @model_validator(mode="after")
    def _validate_symbols(self) -> "BasisScenario":
        if self.spot.market_type != "spot":
            raise ValueError("basis_spot_leg_must_be_spot")
        if self.perp.market_type != "perp":
            raise ValueError("basis_perp_leg_must_be_perp")
        if self.spot.symbol != self.perp.symbol:
            raise ValueError("basis_symbol_mismatch")
        if self.funding is not None and self.funding.symbol != self.perp.symbol:
            raise ValueError("funding_symbol_mismatch")
        if self.research_objective == "funding_carry" and self.funding is None:
            raise ValueError("funding_carry_requires_funding_observation")
        return self


class PairRelativeValueScenario(FrozenContract):
    scenario_id: Identifier
    strategy_id: Identifier
    leg_a_anchor: PriceObservation
    leg_a_current: PriceObservation
    leg_b_anchor: PriceObservation
    leg_b_current: PriceObservation
    beta_a_to_b: float = Field(gt=0, le=10)
    hedge_ratio: float = Field(gt=0, le=10)
    max_beta_residual: float = Field(gt=0, le=2)
    rebalance_tolerance: float = Field(gt=0, le=2)
    prior_hedge_ratio: float | None = Field(default=None, gt=0, le=10)
    convergence_capture_fraction: float = Field(ge=0, le=1)
    cost_model: CostModel

    @model_validator(mode="after")
    def _validate_pair(self) -> "PairRelativeValueScenario":
        if self.leg_a_anchor.symbol != self.leg_a_current.symbol:
            raise ValueError("leg_a_symbol_mismatch")
        if self.leg_b_anchor.symbol != self.leg_b_current.symbol:
            raise ValueError("leg_b_symbol_mismatch")
        if self.leg_a_anchor.symbol == self.leg_b_anchor.symbol:
            raise ValueError("relative_value_requires_distinct_symbols")
        if self.leg_a_anchor.market_type != self.leg_a_current.market_type:
            raise ValueError("leg_a_market_type_mismatch")
        if self.leg_b_anchor.market_type != self.leg_b_current.market_type:
            raise ValueError("leg_b_market_type_mismatch")
        if self.leg_a_anchor.event_time_utc > self.leg_a_current.event_time_utc:
            raise ValueError("leg_a_anchor_after_current")
        if self.leg_b_anchor.event_time_utc > self.leg_b_current.event_time_utc:
            raise ValueError("leg_b_anchor_after_current")
        return self


class RelativeValueRequest(FrozenContract):
    schema_version: Literal["relative_value_request_v1"] = REQUEST_SCHEMA_VERSION
    request_id: Identifier
    decision_time_utc: datetime
    basis_scenarios: tuple[BasisScenario, ...] = ()
    pair_scenarios: tuple[PairRelativeValueScenario, ...] = ()
    safety: SafetyContract = SafetyContract()

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_decision_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_nonempty(self) -> "RelativeValueRequest":
        if not self.basis_scenarios and not self.pair_scenarios:
            raise ValueError("relative_value_request_has_no_scenarios")
        ids = [s.scenario_id for s in self.basis_scenarios] + [s.scenario_id for s in self.pair_scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_scenario_id")
        return self


class BasisEvaluation(FrozenContract):
    scenario_id: Identifier
    strategy_id: Identifier
    research_objective: Literal["basis_convergence", "funding_carry"]
    status: CandidateStatus
    reason: str
    direction: Literal["LONG_SPOT_SHORT_PERP", "SHORT_SPOT_LONG_PERP"] | None
    basis_bps: float | None
    convergence_gross_bps: float | None
    expected_funding_carry_bps: float | None
    round_trip_cost_bps: float | None
    net_scenario_edge_bps: float | None
    hedge_ratio: float
    delta_residual: float
    delta_neutral: bool
    rebalance_state: RebalanceState
    hedge_ratio_drift: float | None
    point_in_time_valid: bool
    point_in_time_errors: tuple[str, ...]


class PairEvaluation(FrozenContract):
    scenario_id: Identifier
    strategy_id: Identifier
    status: CandidateStatus
    reason: str
    direction: Literal["LONG_A_SHORT_B", "SHORT_A_LONG_B"] | None
    spread_bps: float | None
    convergence_gross_bps: float | None
    beta_a_to_b: float
    hedge_ratio: float
    beta_residual: float
    beta_neutral: bool
    target_hedge_ratio: float
    rebalance_state: RebalanceState
    hedge_ratio_drift: float | None
    round_trip_cost_bps: float | None
    net_scenario_edge_bps: float | None
    point_in_time_valid: bool
    point_in_time_errors: tuple[str, ...]


class RelativeValueSnapshot(FrozenContract):
    schema_version: Literal["relative_value_snapshot_v1"] = SNAPSHOT_SCHEMA_VERSION
    snapshot_id: Identifier
    request_id: Identifier
    decision_time_utc: datetime
    created_at_utc: datetime
    status: RelativeValueStatus
    reason: str
    basis_evaluations: tuple[BasisEvaluation, ...]
    pair_evaluations: tuple[PairEvaluation, ...]
    evaluated_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    edge_positive_count: int = Field(ge=0)
    edge_proven: Literal[False] = False
    safety: SafetyContract = SafetyContract()

    @field_validator("decision_time_utc", "created_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)
