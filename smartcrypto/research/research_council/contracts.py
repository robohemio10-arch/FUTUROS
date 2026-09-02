"""Immutable point-in-time contracts for the research-only council."""

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

SCHEMA_VERSION = "research_council_shadow_v1"
CONTEXT_TYPES = ("market", "microstructure", "news", "macro", "regime")
AGENT_IDS = tuple(f"{name}_analyst_v1" for name in CONTEXT_TYPES)

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$",
    ),
]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SignedScore = Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
UnitScore = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]

_FORBIDDEN_FUTURE_KEYS = frozenset(
    {
        "future_outcome",
        "future_pnl",
        "pnl_future",
        "realized_pnl",
        "net_pnl",
        "gross_pnl",
        "trade_outcome",
        "outcome",
        "label",
        "mfe",
        "mae",
        "close_time",
        "exit_price",
        "exit_reason",
    }
)
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "exchange_key",
    "password",
    "private_key",
    "secret",
    "token",
)


class ProviderStatus(str, Enum):
    DISABLED = "DISABLED"
    READY = "READY"
    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    ERROR = "ERROR"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"


class AgentStatus(str, Enum):
    SUCCESS = "SUCCESS"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    INVALID_POINT_IN_TIME = "INVALID_POINT_IN_TIME"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


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
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
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


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(str(key).casefold())
            keys.extend(_walk_keys(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            keys.extend(_walk_keys(item))
    return keys


class StructuredEvidenceInput(FrozenContract):
    schema_version: Literal["research_council_evidence_v1"] = (
        "research_council_evidence_v1"
    )
    event_id: Identifier
    context_type: Literal["market", "microstructure", "news", "macro", "regime"]
    symbol: Identifier
    event_time_utc: datetime
    published_at_utc: datetime | None = None
    ingested_at_utc: datetime | None = None
    available_at_utc: datetime
    source_id: Identifier
    source_hash: Sha256Hex
    provenance: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any]

    @field_validator(
        "event_time_utc",
        "published_at_utc",
        "ingested_at_utc",
        "available_at_utc",
    )
    @classmethod
    def _validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)

    @model_validator(mode="after")
    def _reject_future_and_sensitive_fields(self) -> "StructuredEvidenceInput":
        keys = _walk_keys(self.payload)
        forbidden = sorted(
            key
            for key in keys
            if key in _FORBIDDEN_FUTURE_KEYS
            or key.startswith("target_")
            or key.startswith("label_")
        )
        if forbidden:
            raise ValueError(f"future_or_outcome_fields_forbidden:{','.join(forbidden)}")
        sensitive = sorted(
            key for key in keys if any(part in key for part in _SENSITIVE_KEY_PARTS)
        )
        if sensitive:
            raise ValueError(f"sensitive_fields_forbidden:{','.join(sensitive)}")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        timestamps = {
            "event_time_utc": self.event_time_utc,
            "published_at_utc": self.published_at_utc,
            "ingested_at_utc": self.ingested_at_utc,
            "available_at_utc": self.available_at_utc,
        }
        return tuple(
            f"{name}_after_decision_time"
            for name, value in timestamps.items()
            if value is not None and value > decision
        )


class CouncilRequest(FrozenContract):
    schema_version: Literal["research_council_request_v1"] = "research_council_request_v1"
    request_id: Identifier
    symbol: Identifier
    decision_time_utc: datetime
    evidence: tuple[StructuredEvidenceInput, ...]

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_decision_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_symbol_alignment(self) -> "CouncilRequest":
        mismatched = [item.event_id for item in self.evidence if item.symbol != self.symbol]
        if mismatched:
            raise ValueError("evidence_symbol_mismatch")
        return self


class MarketAnalysis(FrozenContract):
    trend_strength: SignedScore
    momentum_score: SignedScore
    volatility_state: NonEmptyText
    support_pressure: UnitScore
    resistance_pressure: UnitScore
    uncertainty: UnitScore


class MicrostructureAnalysis(FrozenContract):
    flow_pressure: SignedScore
    spread_stress: UnitScore
    liquidity_state: NonEmptyText
    basis_state: NonEmptyText
    microstructure_uncertainty: UnitScore


class NewsAnalysis(FrozenContract):
    event_type: NonEmptyText
    sentiment_score: SignedScore
    severity: UnitScore
    affected_assets: tuple[Identifier, ...]
    unexpectedness: UnitScore
    expected_duration_seconds: int = Field(gt=0)
    uncertainty: UnitScore
    ttl_seconds: int = Field(gt=0)


class MacroAnalysis(FrozenContract):
    risk_on_off_score: SignedScore
    event_shock_score: UnitScore
    macro_regime: NonEmptyText
    horizon_seconds: int = Field(gt=0)
    uncertainty: UnitScore


class RegimeAnalysis(FrozenContract):
    regime_label: NonEmptyText
    regime_confidence: UnitScore
    trend_score: UnitScore
    range_score: UnitScore
    volatility_score: UnitScore
    uncertainty: UnitScore


class ProviderAudit(FrozenContract):
    provider_id: Identifier
    provider_type: Identifier
    model_id: Identifier
    model_version: Identifier
    request_id: Identifier
    request_started_at: datetime
    request_completed_at: datetime
    latency_ms: int = Field(ge=0)
    timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    attempt_count: int = Field(ge=0)
    cache_hit: bool
    status: ProviderStatus
    response_hash: Sha256Hex | None = None
    error_reason: str | None = None

    @field_validator("request_started_at", "request_completed_at")
    @classmethod
    def _validate_audit_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class AgentResult(FrozenContract):
    agent_id: Identifier
    context_type: Literal["market", "microstructure", "news", "macro", "regime"]
    status: AgentStatus
    evidence_ids: tuple[Identifier, ...] = ()
    errors: tuple[str, ...] = ()
    context_payload: dict[str, Any] | None = None
    provider_audit: ProviderAudit | None = None


class DebateCase(FrozenContract):
    stance: Literal["BULL", "BEAR", "NEUTRAL"]
    score: UnitScore
    evidence_ids: tuple[Identifier, ...]
    reasoning_summary: NonEmptyText


class ConsensusResult(FrozenContract):
    status: Literal["SUCCESS", "PARTIAL", "BLOCKED_NO_VALID_CONTEXT"]
    consensus_score: SignedScore
    disagreement_score: UnitScore
    uncertainty_score: UnitScore
    context_quality: UnitScore
    ttl_seconds: int = Field(gt=0)
    valid_until_utc: datetime
    evidence_ids: tuple[Identifier, ...]
    agent_statuses: dict[str, AgentStatus]

    @field_validator("valid_until_utc")
    @classmethod
    def _validate_valid_until(cls, value: datetime) -> datetime:
        return require_utc(value)


class SourceProvenance(FrozenContract):
    event_id: Identifier
    context_type: Literal["market", "microstructure", "news", "macro", "regime"]
    source_id: Identifier
    source_hash: Sha256Hex
    available_at_utc: datetime
    point_in_time_valid: bool
    validation_errors: tuple[str, ...] = ()

    @field_validator("available_at_utc")
    @classmethod
    def _validate_available_at(cls, value: datetime) -> datetime:
        return require_utc(value)


class ContextIntelligenceSnapshot(FrozenContract):
    snapshot_id: Identifier
    schema_version: Literal["context_intelligence_snapshot_v1"] = (
        "context_intelligence_snapshot_v1"
    )
    status: Literal["SUCCESS", "PARTIAL", "BLOCKED_NO_VALID_CONTEXT"]
    reason: str | None
    symbol: Identifier
    decision_time_utc: datetime
    created_at_utc: datetime
    available_at_utc: datetime
    valid_until_utc: datetime
    ttl_seconds: int = Field(gt=0)
    market_context: MarketAnalysis | None
    microstructure_context: MicrostructureAnalysis | None
    news_context: NewsAnalysis | None
    macro_context: MacroAnalysis | None
    regime_context: RegimeAnalysis | None
    bull_case: DebateCase
    bear_case: DebateCase
    neutral_case: DebateCase
    consensus_score: SignedScore
    disagreement_score: UnitScore
    uncertainty_score: UnitScore
    context_quality: UnitScore
    provider_provenance: tuple[ProviderAudit, ...]
    source_provenance: tuple[SourceProvenance, ...]
    evidence_ids: tuple[Identifier, ...]
    agent_statuses: dict[str, AgentStatus]
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False

    @field_validator(
        "decision_time_utc", "created_at_utc", "available_at_utc", "valid_until_utc"
    )
    @classmethod
    def _validate_snapshot_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class ResearchCouncilConfig(FrozenContract):
    schema_version: Literal["research_council_config_v1"] = "research_council_config_v1"
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
    default_ttl_seconds: int = Field(default=900, gt=0)
    provider_timeout_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    max_retry_attempts: int = Field(default=1, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.0, ge=0, le=60, allow_inf_nan=False)
    circuit_breaker_failure_threshold: int = Field(default=3, gt=0, le=20)
    circuit_breaker_cooldown_seconds: int = Field(default=60, gt=0)
    cache_ttl_seconds: int = Field(default=300, gt=0)
    min_valid_agents: int = Field(default=3, ge=1, le=5)
    score_range: tuple[float, float] = (-1.0, 1.0)
    provider_id: Identifier = "deterministic_offline"
    provider_enabled: bool = True

    @model_validator(mode="after")
    def _validate_score_range(self) -> "ResearchCouncilConfig":
        low, high = self.score_range
        if low != -1.0 or high != 1.0:
            raise ValueError("research_council_score_range_must_be_minus_one_to_one")
        return self


class CouncilRunReport(FrozenContract):
    schema_version: Literal["research_council_run_report_v1"] = (
        "research_council_run_report_v1"
    )
    status: Literal["SUCCESS", "PARTIAL", "BLOCKED_NO_VALID_CONTEXT", "BLOCKED"]
    reason: str | None
    request_id: Identifier | None
    input_evidence_count: int = Field(ge=0)
    valid_point_in_time_evidence_count: int = Field(ge=0)
    invalid_point_in_time_evidence_count: int = Field(ge=0)
    snapshot: ContextIntelligenceSnapshot | None
    write_requested: bool
    write_performed: bool
    output_paths: dict[str, str] = Field(default_factory=dict)
    network_calls_executed: Literal[False] = False
    provider_secrets_required: Literal[False] = False
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False
