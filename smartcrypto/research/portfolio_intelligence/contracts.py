"""Immutable contracts for W5 opportunity book and shadow portfolio allocation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

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
UnitScore = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
CorrelationScore = Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
PositiveMoney = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
NonNegativeMoney = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
FiniteMoney = Annotated[float, Field(allow_inf_nan=False)]


class ResearchAction(str, Enum):
    PROCEED_RESEARCH = "PROCEED_RESEARCH"
    DEPRIORITIZE_RESEARCH = "DEPRIORITIZE_RESEARCH"
    ABSTAIN = "ABSTAIN"


class OpportunityStatus(str, Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


class ReplacementStatus(str, Enum):
    EVALUABLE = "EVALUABLE"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    INVALID_POINT_IN_TIME = "INVALID_POINT_IN_TIME"


class AllocationAction(str, Enum):
    SELECT_SHADOW = "SELECT_SHADOW"
    REPLACE_SHADOW = "REPLACE_SHADOW"
    SKIP = "SKIP"


class MissingCorrelationPolicy(str, Enum):
    BLOCK = "BLOCK"
    ALLOW = "ALLOW"


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
    rendered = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}-{canonical_sha256(payload)}"


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported_json_type:{type(value).__name__}")


class CandidateEVEstimate(FrozenContract):
    schema_version: Literal["candidate_ev_estimate_v1"] = "candidate_ev_estimate_v1"
    estimate_id: Identifier
    value_usdt: FiniteMoney
    semantics: Literal["EXPECTED_NET_PNL_USDT_EX_REPLACEMENT_COSTS"]
    generated_at_utc: datetime
    available_at_utc: datetime
    confidence: UnitScore
    source_hash: Sha256Hex

    @field_validator("generated_at_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "CandidateEVEstimate":
        if self.generated_at_utc > self.available_at_utc:
            raise ValueError("candidate_ev_generated_after_available")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.generated_at_utc > decision:
            errors.append("candidate_ev_generated_after_decision")
        if self.available_at_utc > decision:
            errors.append("candidate_ev_available_after_decision")
        return tuple(errors)


class RemainingEVEstimate(FrozenContract):
    schema_version: Literal["remaining_ev_estimate_v1"] = "remaining_ev_estimate_v1"
    estimate_id: Identifier
    value_usdt: FiniteMoney
    semantics: Literal["EXPECTED_REMAINING_NET_PNL_USDT"]
    generated_at_utc: datetime
    available_at_utc: datetime
    confidence: UnitScore
    source_hash: Sha256Hex

    @field_validator("generated_at_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "RemainingEVEstimate":
        if self.generated_at_utc > self.available_at_utc:
            raise ValueError("remaining_ev_generated_after_available")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.generated_at_utc > decision:
            errors.append("remaining_ev_generated_after_decision")
        if self.available_at_utc > decision:
            errors.append("remaining_ev_available_after_decision")
        return tuple(errors)


class CandidateOpportunity(FrozenContract):
    schema_version: Literal["candidate_opportunity_v2"] = "candidate_opportunity_v2"
    candidate_id: Identifier
    symbol: Identifier
    side: Literal["long", "short"]
    strategy_id: Identifier
    ensemble_decision_id: Identifier
    research_action: ResearchAction
    observed_at_utc: datetime
    available_at_utc: datetime
    valid_until_utc: datetime | None = None
    candidate_ev: CandidateEVEstimate
    capital_required_usdt: PositiveMoney
    expected_holding_seconds: float = Field(gt=0.0, allow_inf_nan=False)
    alpha_age_seconds: float = Field(ge=0.0, allow_inf_nan=False)
    regime_label: str | None = None
    regime_confidence: UnitScore | None = None
    source_hash: Sha256Hex

    @field_validator("observed_at_utc", "available_at_utc", "valid_until_utc")
    @classmethod
    def _validate_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "CandidateOpportunity":
        if self.observed_at_utc > self.available_at_utc:
            raise ValueError("candidate_observed_after_available")
        if self.valid_until_utc is not None and self.valid_until_utc < self.available_at_utc:
            raise ValueError("candidate_valid_until_before_available")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.observed_at_utc > decision:
            errors.append("candidate_observed_after_decision")
        if self.available_at_utc > decision:
            errors.append("candidate_available_after_decision")
        if self.valid_until_utc is not None and self.valid_until_utc < decision:
            errors.append("candidate_expired")
        errors.extend(self.candidate_ev.point_in_time_errors(decision))
        return tuple(errors)


class OpenPositionOpportunity(FrozenContract):
    schema_version: Literal["open_position_opportunity_v1"] = "open_position_opportunity_v1"
    position_id: Identifier
    symbol: Identifier
    side: Literal["long", "short"]
    strategy_id: Identifier
    opened_at_utc: datetime
    observed_at_utc: datetime
    available_at_utc: datetime
    capital_locked_usdt: PositiveMoney
    position_age_seconds: float = Field(ge=0.0, allow_inf_nan=False)
    remaining_ev: RemainingEVEstimate | None = None
    source_hash: Sha256Hex

    @field_validator("opened_at_utc", "observed_at_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "OpenPositionOpportunity":
        if self.opened_at_utc > self.observed_at_utc:
            raise ValueError("position_opened_after_observed")
        if self.observed_at_utc > self.available_at_utc:
            raise ValueError("position_observed_after_available")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.opened_at_utc > decision:
            errors.append("position_opened_after_decision")
        if self.observed_at_utc > decision:
            errors.append("position_observed_after_decision")
        if self.available_at_utc > decision:
            errors.append("position_available_after_decision")
        if self.remaining_ev is not None:
            errors.extend(self.remaining_ev.point_in_time_errors(decision))
        return tuple(errors)


class TransitionCostEstimate(FrozenContract):
    schema_version: Literal["transition_cost_estimate_v1"] = "transition_cost_estimate_v1"
    candidate_id: Identifier
    position_id: Identifier
    exit_cost_usdt: NonNegativeMoney
    entry_cost_usdt: NonNegativeMoney
    churn_cost_usdt: NonNegativeMoney
    generated_at_utc: datetime
    available_at_utc: datetime
    source_hash: Sha256Hex

    @field_validator("generated_at_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "TransitionCostEstimate":
        if self.generated_at_utc > self.available_at_utc:
            raise ValueError("transition_cost_generated_after_available")
        return self

    @property
    def switching_cost_usdt(self) -> float:
        return self.exit_cost_usdt + self.entry_cost_usdt + self.churn_cost_usdt

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.generated_at_utc > decision:
            errors.append("transition_cost_generated_after_decision")
        if self.available_at_utc > decision:
            errors.append("transition_cost_available_after_decision")
        return tuple(errors)


class RiskPenaltyEstimate(FrozenContract):
    schema_version: Literal["risk_penalty_estimate_v1"] = "risk_penalty_estimate_v1"
    candidate_id: Identifier
    position_id: Identifier
    value_usdt: NonNegativeMoney
    generated_at_utc: datetime
    available_at_utc: datetime
    source_hash: Sha256Hex

    @field_validator("generated_at_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "RiskPenaltyEstimate":
        if self.generated_at_utc > self.available_at_utc:
            raise ValueError("risk_penalty_generated_after_available")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.generated_at_utc > decision:
            errors.append("risk_penalty_generated_after_decision")
        if self.available_at_utc > decision:
            errors.append("risk_penalty_available_after_decision")
        return tuple(errors)


class ReplacementInput(FrozenContract):
    candidate_id: Identifier
    position_id: Identifier
    transition_cost: TransitionCostEstimate
    risk_penalty: RiskPenaltyEstimate

    @model_validator(mode="after")
    def _validate_ids(self) -> "ReplacementInput":
        if self.transition_cost.candidate_id != self.candidate_id:
            raise ValueError("transition_candidate_id_mismatch")
        if self.transition_cost.position_id != self.position_id:
            raise ValueError("transition_position_id_mismatch")
        if self.risk_penalty.candidate_id != self.candidate_id:
            raise ValueError("risk_penalty_candidate_id_mismatch")
        if self.risk_penalty.position_id != self.position_id:
            raise ValueError("risk_penalty_position_id_mismatch")
        return self


class ReplacementEvaluation(FrozenContract):
    evaluation_id: Identifier
    candidate_id: Identifier
    position_id: Identifier
    status: ReplacementStatus
    candidate_ev_usdt: float | None = Field(default=None, allow_inf_nan=False)
    remaining_ev_usdt: float | None = Field(default=None, allow_inf_nan=False)
    switching_cost_usdt: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    risk_penalty_usdt: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    replacement_delta_usdt: float | None = Field(default=None, allow_inf_nan=False)
    would_replace_shadow: bool
    reason: str
    point_in_time_valid: bool
    replacement_authorized: Literal[False] = False
    replacement_executed: Literal[False] = False


class OpportunityBookRequest(FrozenContract):
    schema_version: Literal["opportunity_book_request_v2"] = "opportunity_book_request_v2"
    request_id: Identifier
    decision_time_utc: datetime
    candidates: tuple[CandidateOpportunity, ...] = ()
    open_positions: tuple[OpenPositionOpportunity, ...] = ()
    replacement_inputs: tuple[ReplacementInput, ...] = ()

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_lineage(self) -> "OpportunityBookRequest":
        candidate_ids = [item.candidate_id for item in self.candidates]
        position_ids = [item.position_id for item in self.open_positions]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("duplicate_candidate_id")
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("duplicate_position_id")
        known_candidates = set(candidate_ids)
        known_positions = set(position_ids)
        pairs: set[tuple[str, str]] = set()
        for item in self.replacement_inputs:
            if item.candidate_id not in known_candidates:
                raise ValueError("replacement_unknown_candidate_id")
            if item.position_id not in known_positions:
                raise ValueError("replacement_unknown_position_id")
            pair = (item.candidate_id, item.position_id)
            if pair in pairs:
                raise ValueError("duplicate_replacement_pair")
            pairs.add(pair)
        return self


class OpportunityCandidateView(FrozenContract):
    candidate_id: Identifier
    symbol: Identifier
    side: Literal["long", "short"]
    strategy_id: Identifier
    research_action: ResearchAction
    candidate_ev_usdt: FiniteMoney
    capital_required_usdt: PositiveMoney
    expected_holding_seconds: float = Field(gt=0.0, allow_inf_nan=False)
    required_capital_hours: float = Field(gt=0.0, allow_inf_nan=False)
    ev_per_capital_hour: float = Field(allow_inf_nan=False)
    alpha_age_seconds: float = Field(ge=0.0, allow_inf_nan=False)
    point_in_time_valid: bool
    point_in_time_errors: tuple[str, ...]
    source_hash: Sha256Hex


class OpenPositionView(FrozenContract):
    position_id: Identifier
    symbol: Identifier
    side: Literal["long", "short"]
    strategy_id: Identifier
    remaining_ev_usdt: float | None = Field(default=None, allow_inf_nan=False)
    remaining_ev_status: Literal["AVAILABLE", "SOURCE_MISSING", "INVALID_POINT_IN_TIME"]
    capital_locked_usdt: PositiveMoney
    capital_hours_consumed: float = Field(ge=0.0, allow_inf_nan=False)
    position_age_seconds: float = Field(ge=0.0, allow_inf_nan=False)
    point_in_time_valid: bool
    point_in_time_errors: tuple[str, ...]
    source_hash: Sha256Hex


class OpportunityBookSnapshot(FrozenContract):
    schema_version: Literal["opportunity_book_v2"] = "opportunity_book_v2"
    book_id: Identifier
    request_id: Identifier
    decision_time_utc: datetime
    created_at_utc: datetime
    status: OpportunityStatus
    reason: str
    candidates: tuple[OpportunityCandidateView, ...]
    open_positions: tuple[OpenPositionView, ...]
    replacements: tuple[ReplacementEvaluation, ...]
    candidate_count: int = Field(ge=0)
    valid_candidate_count: int = Field(ge=0)
    abstained_candidate_count: int = Field(ge=0)
    invalid_candidate_count: int = Field(ge=0)
    capital_locked_usdt: NonNegativeMoney
    capital_hours_total: NonNegativeMoney
    point_in_time_valid_for_used_inputs: bool
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False

    @field_validator("decision_time_utc", "created_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class CorrelationObservation(FrozenContract):
    schema_version: Literal["correlation_observation_v1"] = "correlation_observation_v1"
    symbol_a: Identifier
    symbol_b: Identifier
    correlation: CorrelationScore
    sample_count: int = Field(ge=2)
    generated_at_utc: datetime
    available_at_utc: datetime
    source_hash: Sha256Hex

    @field_validator("generated_at_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "CorrelationObservation":
        if self.symbol_a == self.symbol_b:
            raise ValueError("correlation_pair_must_use_distinct_symbols")
        if self.generated_at_utc > self.available_at_utc:
            raise ValueError("correlation_generated_after_available")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.generated_at_utc > decision:
            errors.append("correlation_generated_after_decision")
        if self.available_at_utc > decision:
            errors.append("correlation_available_after_decision")
        return tuple(errors)


class AlphaDefinition(FrozenContract):
    schema_version: Literal["alpha_definition_v1"] = "alpha_definition_v1"
    strategy_id: Identifier
    sleeve: Identifier
    version: Identifier
    feature_set_hash: Sha256Hex
    hypothesis: str = Field(min_length=8, max_length=500)
    supported_regimes: tuple[str, ...] = ()
    research_only: Literal[True] = True


class AlphaRegistrySnapshot(FrozenContract):
    schema_version: Literal["alpha_registry_v1"] = "alpha_registry_v1"
    registry_id: Identifier
    created_at_utc: datetime
    definitions: tuple[AlphaDefinition, ...]
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False

    @field_validator("created_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class PortfolioAllocatorConfig(FrozenContract):
    schema_version: Literal["portfolio_allocator_config_v1"] = "portfolio_allocator_config_v1"
    mode: Literal["research"] = "research"
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    live_release_allowed: Literal[False] = False
    canary_release_allowed: Literal[False] = False
    writes_active_signals: Literal[False] = False
    top_n: int = Field(default=2, ge=1, le=100)
    max_positions: int = Field(default=2, ge=1, le=100)
    max_positions_per_symbol: int = Field(default=1, ge=1, le=20)
    shadow_capital_budget_usdt: PositiveMoney = 1000.0
    max_symbol_concentration_fraction: UnitScore = 0.60
    max_pairwise_correlation: UnitScore = 0.80
    min_candidate_ev_usdt: float = Field(default=0.0, allow_inf_nan=False)
    min_replacement_delta_usdt: float = Field(default=0.0, allow_inf_nan=False)
    allow_deprioritized: bool = False
    missing_correlation_policy: MissingCorrelationPolicy = MissingCorrelationPolicy.BLOCK
    require_registered_alpha: bool = True
    ranking_metric: Literal["candidate_ev", "ev_per_capital_hour"] = "ev_per_capital_hour"


class AllocationDecision(FrozenContract):
    decision_id: Identifier
    rank: int = Field(ge=1)
    candidate_id: Identifier
    symbol: Identifier
    strategy_id: Identifier
    action: AllocationAction
    allocated_capital_usdt: NonNegativeMoney
    objective_score: float = Field(allow_inf_nan=False)
    replacement_position_id: Identifier | None = None
    replacement_delta_usdt: float | None = Field(default=None, allow_inf_nan=False)
    pairwise_max_abs_correlation: float | None = Field(default=None, ge=0.0, le=1.0)
    symbol_concentration_fraction: UnitScore | None = None
    reasons: tuple[str, ...]
    riskmanager_final_authority_required: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False


class PortfolioAllocatorRequest(FrozenContract):
    schema_version: Literal["portfolio_allocator_request_v1"] = "portfolio_allocator_request_v1"
    request_id: Identifier
    decision_time_utc: datetime
    opportunity_book: OpportunityBookSnapshot
    correlations: tuple[CorrelationObservation, ...] = ()
    alpha_registry: AlphaRegistrySnapshot | None = None

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_book_time(self) -> "PortfolioAllocatorRequest":
        if self.opportunity_book.decision_time_utc != self.decision_time_utc:
            raise ValueError("allocator_book_decision_time_mismatch")
        return self


class PortfolioAllocationSnapshot(FrozenContract):
    schema_version: Literal["portfolio_allocator_shadow_v1"] = "portfolio_allocator_shadow_v1"
    allocation_id: Identifier
    request_id: Identifier
    decision_time_utc: datetime
    created_at_utc: datetime
    status: OpportunityStatus
    reason: str
    selected: tuple[AllocationDecision, ...]
    rejected: tuple[AllocationDecision, ...]
    selected_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    existing_position_count: int = Field(ge=0)
    projected_position_count: int = Field(ge=0)
    existing_capital_usdt: NonNegativeMoney
    selected_capital_usdt: NonNegativeMoney
    projected_capital_usdt: NonNegativeMoney
    shadow_capital_budget_usdt: PositiveMoney
    top_n: int = Field(ge=1)
    correlation_constraints_applied: bool
    concentration_constraints_applied: bool
    capacity_constraints_applied: bool
    replacement_evaluations_used: bool
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False
    riskmanager_final_authority: Literal[True] = True

    @field_validator("decision_time_utc", "created_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)
