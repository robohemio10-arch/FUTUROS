"""Immutable payload 4.2 contracts for the paper decision ledger.

This package is intentionally isolated from runtime wiring.  It defines and
validates append-only decision and trade-link records, but it does not import
Freqtrade, RiskManager, Qlib runtime components, exchange clients, or order
submission code.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from .serialization import compute_payload_sha256

SCHEMA_VERSION = "decision_ledger_payload_v4_2"

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$",
    ),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
Sha256Hex = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
NonNegativeFloat = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class Alignment(str, Enum):
    ALIGNED = "aligned"
    COUNTER = "counter"
    RANGE = "range"
    UNKNOWN = "unknown"


class AIShadowDecision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    ABSTAIN = "ABSTAIN"
    NOT_EVALUATED = "NOT_EVALUATED"


class RiskDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
    NOT_EVALUATED = "NOT_EVALUATED"


class FinalDecision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    @field_validator("feature_timestamp", "decision_timestamp", check_fields=False)
    @classmethod
    def _require_utc_timestamp(cls, value: datetime) -> datetime:
        return require_utc_timestamp(value)


class DecisionRecordBodyV42(_FrozenContract):
    """Validated decision body before the canonical SHA-256 seal is attached."""

    schema_version: Literal["decision_ledger_payload_v4_2"] = "decision_ledger_payload_v4_2"
    record_type: Literal["decision"] = "decision"

    event_id: Identifier
    parent_event_id: None = None
    signal_id: Identifier
    candidate_id: Identifier
    trade_id: None = None
    correlation_id: Identifier
    idempotency_key: Identifier

    runtime_mode: Literal["paper"] = "paper"
    pair: NonEmptyText
    symbol: Identifier
    side: Side

    feature_timestamp: datetime
    decision_timestamp: datetime
    execution_timestamp: None = None

    feature_contract_version: Identifier
    feature_hash: Sha256Hex
    model_id: Identifier
    model_version: Identifier
    model_hash: Sha256Hex

    qlib_score: FiniteFloat
    calibrated_probability: Probability | None
    expected_net_pnl: FiniteFloat | None
    fast_stop_probability: Probability | None
    regime: NonEmptyText
    alignment: Alignment

    ai_shadow_decision: AIShadowDecision
    ai_shadow_reasons: tuple[NonEmptyText, ...]
    risk_decision: RiskDecision
    risk_reasons: tuple[NonEmptyText, ...]
    approved_stake_usdt: NonNegativeFloat
    approved_leverage: NonNegativeFloat
    final_decision: FinalDecision
    final_reasons: tuple[NonEmptyText, ...]

    operational_authority: Literal[False] = False
    runtime_integration: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False

    @model_validator(mode="after")
    def _validate_decision_invariants(self) -> "DecisionRecordBodyV42":
        if self.decision_timestamp < self.feature_timestamp:
            raise ValueError("decision_timestamp_before_feature_timestamp")

        if not self.final_reasons:
            raise ValueError("final_reasons_required")

        if self.ai_shadow_decision in {
            AIShadowDecision.BLOCK,
            AIShadowDecision.ABSTAIN,
        } and not self.ai_shadow_reasons:
            raise ValueError("ai_shadow_reasons_required")

        if self.risk_decision in {
            RiskDecision.REJECTED,
            RiskDecision.ERROR,
        } and not self.risk_reasons:
            raise ValueError("risk_reasons_required")

        if self.final_decision is FinalDecision.ALLOW:
            if self.risk_decision is not RiskDecision.APPROVED:
                raise ValueError("final_allow_requires_risk_approved")
            if self.approved_stake_usdt <= 0.0:
                raise ValueError("final_allow_requires_positive_approved_stake")
            if self.approved_leverage <= 0.0:
                raise ValueError("final_allow_requires_positive_approved_leverage")
            if self.ai_shadow_decision is AIShadowDecision.BLOCK:
                raise ValueError("final_allow_conflicts_with_ai_shadow_block")

        return self


class DecisionRecordV42(DecisionRecordBodyV42):
    """Sealed decision payload with a self-verifying canonical SHA-256."""

    payload_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_payload_hash(self) -> "DecisionRecordV42":
        expected = compute_payload_sha256(self)
        if self.payload_sha256 != expected:
            raise ValueError("payload_sha256_mismatch")
        return self


class TradeLinkRecordBodyV42(_FrozenContract):
    """Append-only link between a sealed decision event and a paper trade."""

    schema_version: Literal["decision_ledger_payload_v4_2"] = "decision_ledger_payload_v4_2"
    record_type: Literal["trade_link"] = "trade_link"

    event_id: Identifier
    parent_event_id: Identifier
    signal_id: Identifier
    candidate_id: Identifier
    trade_id: PositiveInt
    correlation_id: Identifier
    idempotency_key: Identifier

    runtime_mode: Literal["paper"] = "paper"
    pair: NonEmptyText
    symbol: Identifier
    side: Side

    decision_timestamp: datetime
    execution_timestamp: datetime
    decision_payload_sha256: Sha256Hex
    link_reason: NonEmptyText

    operational_authority: Literal[False] = False
    runtime_integration: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False

    @field_validator("execution_timestamp")
    @classmethod
    def _require_execution_utc(cls, value: datetime) -> datetime:
        return require_utc_timestamp(value)

    @model_validator(mode="after")
    def _validate_trade_link_invariants(self) -> "TradeLinkRecordBodyV42":
        if self.parent_event_id == self.event_id:
            raise ValueError("trade_link_parent_must_reference_distinct_decision_event")
        if self.execution_timestamp < self.decision_timestamp:
            raise ValueError("execution_timestamp_before_decision_timestamp")
        return self


class TradeLinkRecordV42(TradeLinkRecordBodyV42):
    """Sealed trade-link payload with a self-verifying canonical SHA-256."""

    payload_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_payload_hash(self) -> "TradeLinkRecordV42":
        expected = compute_payload_sha256(self)
        if self.payload_sha256 != expected:
            raise ValueError("payload_sha256_mismatch")
        return self


PayloadRecordV42: TypeAlias = Annotated[
    DecisionRecordV42 | TradeLinkRecordV42,
    Field(discriminator="record_type"),
]
PAYLOAD_ADAPTER: TypeAdapter[PayloadRecordV42] = TypeAdapter(PayloadRecordV42)


def require_utc_timestamp(value: datetime) -> datetime:
    """Require a timezone-aware timestamp whose UTC offset is exactly zero."""

    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("timestamp_must_be_timezone_aware_utc")
    if offset.total_seconds() != 0:
        raise ValueError("timestamp_must_use_utc_offset_zero")
    return value.astimezone(timezone.utc)


def seal_decision_record(
    payload: DecisionRecordBodyV42 | dict[str, Any],
) -> DecisionRecordV42:
    """Validate a decision body and attach its deterministic payload hash."""

    body = (
        payload
        if isinstance(payload, DecisionRecordBodyV42)
        else DecisionRecordBodyV42.model_validate(payload)
    )
    data = body.model_dump(mode="python")
    data["payload_sha256"] = compute_payload_sha256(body)
    return DecisionRecordV42.model_validate(data)


def seal_trade_link_record(
    payload: TradeLinkRecordBodyV42 | dict[str, Any],
) -> TradeLinkRecordV42:
    """Validate a trade-link body and attach its deterministic payload hash."""

    body = (
        payload
        if isinstance(payload, TradeLinkRecordBodyV42)
        else TradeLinkRecordBodyV42.model_validate(payload)
    )
    data = body.model_dump(mode="python")
    data["payload_sha256"] = compute_payload_sha256(body)
    return TradeLinkRecordV42.model_validate(data)


def parse_payload_record(payload: str | bytes | bytearray | dict[str, Any]) -> PayloadRecordV42:
    """Parse and fully verify a decision or trade-link payload."""

    if isinstance(payload, (str, bytes, bytearray)):
        return PAYLOAD_ADAPTER.validate_json(payload)
    return PAYLOAD_ADAPTER.validate_python(payload)
