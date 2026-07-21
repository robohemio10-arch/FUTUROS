"""Sandbox-only runtime mapping profile for decision-ledger payload 4.2.

The P0.3B payload contract remains immutable and design-only.  This module
defines a separately versioned projection envelope that maps authoritative
candidate, model, AI Shadow and RiskManager inputs into sealed P0.3B decision
and trade-link records without writing a ledger or integrating with runtime.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Final, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from smartcrypto.execution.decision_ledger_v4_2 import (
    AIShadowDecision,
    Alignment,
    DecisionRecordV42,
    FinalDecision,
    Side,
    TradeLinkRecordV42,
)

PROFILE_VERSION: Final[
    Literal["decision_ledger_runtime_observability_profile_v1"]
] = "decision_ledger_runtime_observability_profile_v1"
ACTIVATION_STATE: Final[
    Literal["sandbox_mapping_only"]
] = "sandbox_mapping_only"

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
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
NonNegativeFloat = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]


class _FrozenModel(BaseModel):
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
        raise ValueError("timestamp_must_be_timezone_aware_utc")
    if offset.total_seconds() != 0:
        raise ValueError("timestamp_must_use_utc_offset_zero")
    return value.astimezone(timezone.utc)


class RuntimeDecisionInputV1(_FrozenModel):
    """Authoritative inputs required to project one final paper decision."""

    runtime_mode: Literal["paper"] = "paper"

    signal_id: Identifier
    candidate_id: Identifier
    correlation_id: Identifier

    pair: NonEmptyText
    symbol: Identifier
    side: Side

    feature_timestamp: datetime
    decision_timestamp: datetime
    risk_checked_at_utc: datetime

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

    risk_approved: StrictBool
    risk_reasons: tuple[NonEmptyText, ...]
    risk_policy_id: Identifier
    risk_config_hash: Sha256Hex

    approved_stake_usdt: NonNegativeFloat
    approved_leverage: NonNegativeFloat

    final_decision: FinalDecision
    final_reasons: tuple[NonEmptyText, ...]

    source_signal_sha256: Sha256Hex

    operational_authority: Literal[False] = False
    runtime_integration: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False

    @field_validator(
        "feature_timestamp",
        "decision_timestamp",
        "risk_checked_at_utc",
    )
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_invariants(self) -> "RuntimeDecisionInputV1":
        if self.risk_checked_at_utc < self.feature_timestamp:
            raise ValueError("risk_checked_before_feature_timestamp")
        if self.decision_timestamp < self.risk_checked_at_utc:
            raise ValueError("decision_timestamp_before_risk_check")
        if not self.final_reasons:
            raise ValueError("final_reasons_required")

        if self.ai_shadow_decision in {
            AIShadowDecision.BLOCK,
            AIShadowDecision.ABSTAIN,
        } and not self.ai_shadow_reasons:
            raise ValueError("ai_shadow_reasons_required")

        if self.risk_approved:
            if self.final_decision is FinalDecision.ALLOW:
                if self.approved_stake_usdt <= 0.0:
                    raise ValueError("allow_requires_positive_stake")
                if self.approved_leverage <= 0.0:
                    raise ValueError("allow_requires_positive_leverage")
                if self.ai_shadow_decision is AIShadowDecision.BLOCK:
                    raise ValueError("allow_conflicts_with_ai_shadow_block")
        else:
            if not self.risk_reasons:
                raise ValueError("risk_reasons_required_when_rejected")
            if self.final_decision is not FinalDecision.BLOCK:
                raise ValueError("risk_rejected_requires_final_block")
            if self.approved_stake_usdt != 0.0:
                raise ValueError("blocked_decision_requires_zero_stake")
            if self.approved_leverage != 0.0:
                raise ValueError("blocked_decision_requires_zero_leverage")

        return self


class RuntimeTradeObservationInputV1(_FrozenModel):
    """Authoritative post-execution observation used for append-only trade link."""

    trade_id: PositiveInt
    execution_timestamp: datetime
    observed_pair: NonEmptyText
    observed_symbol: Identifier
    observed_side: Side
    source_database_sha256: Sha256Hex
    source_table: Identifier
    source_row_fingerprint: Sha256Hex
    link_reason: NonEmptyText

    operational_authority: Literal[False] = False
    runtime_integration: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False

    @field_validator("execution_timestamp")
    @classmethod
    def _validate_execution_timestamp(cls, value: datetime) -> datetime:
        return require_utc(value)


class RuntimeDecisionLineageV1(_FrozenModel):
    """Fields preserved by the runtime envelope but absent from payload 4.2."""

    profile_version: Literal[
        "decision_ledger_runtime_observability_profile_v1"
    ] = PROFILE_VERSION
    activation_state: Literal["sandbox_mapping_only"] = ACTIVATION_STATE

    risk_checked_at_utc: datetime
    risk_policy_id: Identifier
    risk_config_hash: Sha256Hex
    source_signal_sha256: Sha256Hex
    field_source_registry_sha256: Sha256Hex
    mapping_input_sha256: Sha256Hex

    writer_invoked: Literal[False] = False
    runtime_integration: Literal[False] = False
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False

    @field_validator("risk_checked_at_utc")
    @classmethod
    def _validate_risk_timestamp(cls, value: datetime) -> datetime:
        return require_utc(value)


class RuntimeTradeLinkLineageV1(_FrozenModel):
    profile_version: Literal[
        "decision_ledger_runtime_observability_profile_v1"
    ] = PROFILE_VERSION
    activation_state: Literal["sandbox_mapping_only"] = ACTIVATION_STATE

    source_database_sha256: Sha256Hex
    source_table: Identifier
    source_row_fingerprint: Sha256Hex
    field_source_registry_sha256: Sha256Hex
    mapping_input_sha256: Sha256Hex

    writer_invoked: Literal[False] = False
    runtime_integration: Literal[False] = False
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False


class RuntimeDecisionProjectionV1(_FrozenModel):
    profile_version: Literal[
        "decision_ledger_runtime_observability_profile_v1"
    ] = PROFILE_VERSION
    activation_state: Literal["sandbox_mapping_only"] = ACTIVATION_STATE
    projection_type: Literal["decision_projection"] = "decision_projection"
    lineage: RuntimeDecisionLineageV1
    target_payload: DecisionRecordV42

    @model_validator(mode="after")
    def _validate_target(self) -> "RuntimeDecisionProjectionV1":
        if self.target_payload.runtime_integration is not False:
            raise ValueError("target_payload_runtime_integration_must_be_false")
        if self.target_payload.operational_authority is not False:
            raise ValueError("target_payload_operational_authority_must_be_false")
        return self


class RuntimeTradeLinkProjectionV1(_FrozenModel):
    profile_version: Literal[
        "decision_ledger_runtime_observability_profile_v1"
    ] = PROFILE_VERSION
    activation_state: Literal["sandbox_mapping_only"] = ACTIVATION_STATE
    projection_type: Literal["trade_link_projection"] = "trade_link_projection"
    lineage: RuntimeTradeLinkLineageV1
    target_payload: TradeLinkRecordV42

    @model_validator(mode="after")
    def _validate_target(self) -> "RuntimeTradeLinkProjectionV1":
        if self.target_payload.runtime_integration is not False:
            raise ValueError("target_payload_runtime_integration_must_be_false")
        if self.target_payload.operational_authority is not False:
            raise ValueError("target_payload_operational_authority_must_be_false")
        return self


RuntimeProjectionRecordV1: TypeAlias = (
    RuntimeDecisionProjectionV1 | RuntimeTradeLinkProjectionV1
)
