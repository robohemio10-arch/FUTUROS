"""Immutable contracts for W4 regime routing and ensemble abstention research."""

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

from smartcrypto.research.market_intelligence.contracts import MarketIntelligenceSnapshot
from smartcrypto.research.research_council.contracts import ContextIntelligenceSnapshot

SCHEMA_VERSION: Literal["ensemble_abstention_v1"] = "ensemble_abstention_v1"

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
SignedScore = Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
UnitScore = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


class RegimeLabel(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class RegimeRouteStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    INVALID_POINT_IN_TIME = "INVALID_POINT_IN_TIME"


class RegimeAlignment(str, Enum):
    ALIGNED = "ALIGNED"
    COUNTER_TREND = "COUNTER_TREND"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ResearchAction(str, Enum):
    PROCEED_RESEARCH = "PROCEED_RESEARCH"
    DEPRIORITIZE_RESEARCH = "DEPRIORITIZE_RESEARCH"
    ABSTAIN = "ABSTAIN"


class EnsembleStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


class AIShadowDecision(str, Enum):
    ALLOW = "ALLOW"
    VETO = "VETO"
    UNKNOWN = "UNKNOWN"


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


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported_json_type:{type(value).__name__}")


class QlibDirectionalEvidence(FrozenContract):
    schema_version: Literal["qlib_directional_evidence_v1"] = "qlib_directional_evidence_v1"
    evidence_id: Identifier
    source_id: Identifier
    model_version: Identifier
    symbol: Identifier
    generated_at_utc: datetime
    available_at_utc: datetime
    valid_until_utc: datetime | None = None
    proposed_side: Literal["long", "short", "no_trade"]
    score: SignedScore
    prob_up: UnitScore | None = None
    confidence: UnitScore
    market_regime: str | None = None
    market_regime_status: Literal["fresh", "point_in_time", "stale", "unknown"] = "unknown"
    market_regime_confidence: UnitScore | None = None
    source_hash: Sha256Hex

    @field_validator("generated_at_utc", "available_at_utc", "valid_until_utc")
    @classmethod
    def _validate_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def _validate_temporal_order(self) -> "QlibDirectionalEvidence":
        if self.generated_at_utc > self.available_at_utc:
            raise ValueError("qlib_generated_at_after_available_at")
        if self.valid_until_utc is not None and self.valid_until_utc < self.available_at_utc:
            raise ValueError("qlib_valid_until_before_available_at")
        if self.prob_up is not None:
            expected_score = (2.0 * self.prob_up) - 1.0
            if abs(expected_score - self.score) > 1e-6:
                raise ValueError("qlib_score_prob_up_inconsistent")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.generated_at_utc > decision:
            errors.append("qlib_generated_at_after_decision_time")
        if self.available_at_utc > decision:
            errors.append("qlib_available_at_after_decision_time")
        if self.valid_until_utc is not None and self.valid_until_utc < decision:
            errors.append("qlib_evidence_expired")
        return tuple(errors)


class AIShadowVetoEvidence(FrozenContract):
    schema_version: Literal["ai_shadow_veto_evidence_v1"] = "ai_shadow_veto_evidence_v1"
    evidence_id: Identifier
    source_id: Identifier
    model_version: Identifier
    symbol: Identifier
    generated_at_utc: datetime
    available_at_utc: datetime
    valid_until_utc: datetime | None = None
    decision: AIShadowDecision
    veto_score: UnitScore
    confidence: UnitScore
    source_hash: Sha256Hex

    @field_validator("generated_at_utc", "available_at_utc", "valid_until_utc")
    @classmethod
    def _validate_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def _validate_temporal_order(self) -> "AIShadowVetoEvidence":
        if self.generated_at_utc > self.available_at_utc:
            raise ValueError("ai_shadow_generated_at_after_available_at")
        if self.valid_until_utc is not None and self.valid_until_utc < self.available_at_utc:
            raise ValueError("ai_shadow_valid_until_before_available_at")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.generated_at_utc > decision:
            errors.append("ai_shadow_generated_at_after_decision_time")
        if self.available_at_utc > decision:
            errors.append("ai_shadow_available_at_after_decision_time")
        if self.valid_until_utc is not None and self.valid_until_utc < decision:
            errors.append("ai_shadow_evidence_expired")
        return tuple(errors)


class EnsembleAbstentionRequest(FrozenContract):
    schema_version: Literal["ensemble_abstention_request_v1"] = "ensemble_abstention_request_v1"
    request_id: Identifier
    symbol: Identifier
    decision_time_utc: datetime
    qlib: QlibDirectionalEvidence | None = None
    ai_shadow: AIShadowVetoEvidence | None = None
    research_council_snapshot: ContextIntelligenceSnapshot | None = None
    market_intelligence_snapshot: MarketIntelligenceSnapshot | None = None

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_decision_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_symbol_alignment(self) -> "EnsembleAbstentionRequest":
        mismatches: list[str] = []
        if self.qlib is not None and self.qlib.symbol != self.symbol:
            mismatches.append("qlib")
        if self.ai_shadow is not None and self.ai_shadow.symbol != self.symbol:
            mismatches.append("ai_shadow")
        if (
            self.research_council_snapshot is not None
            and self.research_council_snapshot.symbol != self.symbol
        ):
            mismatches.append("research_council")
        if (
            self.market_intelligence_snapshot is not None
            and self.market_intelligence_snapshot.symbol != self.symbol
        ):
            mismatches.append("market_intelligence")
        if mismatches:
            raise ValueError(f"ensemble_symbol_mismatch:{','.join(mismatches)}")
        return self


class RegimeEvidencePoint(FrozenContract):
    source: Literal["qlib_market_regime", "research_council_regime"]
    regime_label: RegimeLabel
    confidence: UnitScore
    volatility_score: UnitScore
    high_volatility: bool
    evidence_id: Identifier


class RegimeRoute(FrozenContract):
    schema_version: Literal["regime_route_v1"] = "regime_route_v1"
    status: RegimeRouteStatus
    regime_label: RegimeLabel
    regime_confidence: UnitScore
    trend_score: SignedScore
    range_score: UnitScore
    volatility_score: UnitScore
    high_volatility: bool
    disagreement_score: UnitScore
    evidence_points: tuple[RegimeEvidencePoint, ...]
    evidence_ids: tuple[Identifier, ...]
    point_in_time_valid: bool
    reason: str


class DirectionalEvidencePoint(FrozenContract):
    source: Literal["qlib", "research_council", "market_intelligence"]
    score: SignedScore
    confidence: UnitScore
    uncertainty: UnitScore
    evidence_id: Identifier


class EnsembleAbstentionDecision(FrozenContract):
    schema_version: Literal["ensemble_abstention_decision_v1"] = (
        "ensemble_abstention_decision_v1"
    )
    decision_id: Identifier
    request_id: Identifier
    symbol: Identifier
    decision_time_utc: datetime
    created_at_utc: datetime
    valid_until_utc: datetime
    status: EnsembleStatus
    research_action: ResearchAction
    reasons: tuple[str, ...]
    proposed_side: Literal["long", "short", "no_trade"]
    regime_route: RegimeRoute
    regime_alignment: RegimeAlignment
    ensemble_score: SignedScore
    disagreement_score: UnitScore
    uncertainty_score: UnitScore
    evidence_coverage: UnitScore
    directional_evidence_count: int = Field(ge=0)
    directional_evidence: tuple[DirectionalEvidencePoint, ...]
    ai_shadow_decision: AIShadowDecision | None = None
    ai_shadow_veto_score: UnitScore | None = None
    ai_shadow_confidence: UnitScore | None = None
    source_hashes: tuple[Sha256Hex, ...]
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False

    @field_validator("decision_time_utc", "created_at_utc", "valid_until_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class EnsembleAbstentionConfig(FrozenContract):
    min_directional_evidence_count: int = Field(default=2, ge=1, le=4)
    high_disagreement_threshold: UnitScore = 0.45
    deprioritize_disagreement_threshold: UnitScore = 0.30
    high_uncertainty_threshold: UnitScore = 0.65
    min_context_quality: UnitScore = 0.25
    range_abstain_confidence: UnitScore = 0.60
    counter_trend_abstain_confidence: UnitScore = 0.60
    ai_shadow_veto_score_threshold: UnitScore = 0.50
    ai_shadow_veto_confidence_threshold: UnitScore = 0.50
    default_regime_confidence_when_missing: UnitScore = 0.50
    default_ttl_seconds: int = Field(default=300, gt=0, le=86400)

    @model_validator(mode="after")
    def _validate_threshold_order(self) -> "EnsembleAbstentionConfig":
        if self.deprioritize_disagreement_threshold > self.high_disagreement_threshold:
            raise ValueError("deprioritize_disagreement_threshold_above_high_threshold")
        return self


class AibotParityResearchConfig(FrozenContract):
    schema_version: Literal["aibot_parity_research_config_v1"] = (
        "aibot_parity_research_config_v1"
    )
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
    ensemble_abstention: EnsembleAbstentionConfig = Field(
        default_factory=EnsembleAbstentionConfig
    )


class EnsembleRunReport(FrozenContract):
    schema_version: Literal["ensemble_abstention_run_report_v1"] = (
        "ensemble_abstention_run_report_v1"
    )
    status: EnsembleStatus
    reason: str | None
    request_id: Identifier | None
    decision: EnsembleAbstentionDecision | None
    write_requested: bool
    write_performed: bool
    output_paths: dict[str, str] = Field(default_factory=dict)
    network_calls_executed: Literal[False] = False
    model_training_performed: Literal[False] = False
    model_promotion_performed: Literal[False] = False
    registry_write_performed: Literal[False] = False
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False
