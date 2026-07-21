"""Contracts for P0.4C sandbox-only integration harness."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from smartcrypto.execution.decision_ledger_runtime_profile_v1 import (
    RuntimeDecisionProjectionV1,
    RuntimeTradeLinkProjectionV1,
)

INTEGRATION_PROFILE_VERSION: Final[
    Literal["decision_ledger_runtime_integration_harness_v1"]
] = "decision_ledger_runtime_integration_harness_v1"
ACTIVATION_STATE: Final[Literal["sandbox_only"]] = "sandbox_only"

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


class SandboxIntegrationConfigV1(_FrozenModel):
    """P0.4C can project and validate, but cannot write runtime."""

    profile_version: Literal[
        "decision_ledger_runtime_integration_harness_v1"
    ] = INTEGRATION_PROFILE_VERSION
    activation_state: Literal["sandbox_only"] = ACTIVATION_STATE
    mode: Literal["disabled", "preview"] = "disabled"
    enabled: bool = False
    fail_closed: Literal[True] = True
    writer_enabled: Literal[False] = False
    trade_link_writer_enabled: Literal[False] = False
    runtime_integration: Literal[False] = False
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    legacy_writer_mode: Literal["legacy_only", "shadow_compare"] = "legacy_only"

    @model_validator(mode="after")
    def _validate_mode(self) -> "SandboxIntegrationConfigV1":
        if self.enabled != (self.mode == "preview"):
            raise ValueError("enabled_must_match_preview_mode")
        return self


class ProjectionFailureV1(_FrozenModel):
    source_index: int = Field(ge=0)
    pair: str | None
    symbol: str | None
    side: str | None
    risk_approved: bool | None
    error_type: NonEmptyText
    error_message_sha256: Sha256Hex
    missing_fields: tuple[NonEmptyText, ...] = ()


class IntegrationPreviewResultV1(_FrozenModel):
    profile_version: Literal[
        "decision_ledger_runtime_integration_harness_v1"
    ] = INTEGRATION_PROFILE_VERSION
    activation_state: Literal["sandbox_only"] = ACTIVATION_STATE
    status: Literal["disabled", "ok", "blocked"]
    reason: NonEmptyText | None
    source_signal_count: int = Field(ge=0)
    approved_source_count: int = Field(ge=0)
    rejected_source_count: int = Field(ge=0)
    projected_decision_count: int = Field(ge=0)
    active_envelope_count: int = Field(ge=0)
    projection_failure_count: int = Field(ge=0)
    active_signals: tuple[dict[str, object], ...]
    decision_projections: tuple[RuntimeDecisionProjectionV1, ...]
    failures: tuple[ProjectionFailureV1, ...]
    writer_invoked: Literal[False] = False
    runtime_integration: Literal[False] = False
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False

    @model_validator(mode="after")
    def _validate_counts(self) -> "IntegrationPreviewResultV1":
        if self.source_signal_count != self.approved_source_count + self.rejected_source_count:
            raise ValueError("source_signal_count_mismatch")
        if self.projected_decision_count != len(self.decision_projections):
            raise ValueError("projected_decision_count_mismatch")
        if self.active_envelope_count != len(self.active_signals):
            raise ValueError("active_envelope_count_mismatch")
        if self.projection_failure_count != len(self.failures):
            raise ValueError("projection_failure_count_mismatch")
        return self


class ActiveSignalDecisionEnvelopeV1(_FrozenModel):
    schema_version: Literal[
        "active_signal_decision_envelope_v1"
    ] = "active_signal_decision_envelope_v1"
    profile_version: Literal[
        "decision_ledger_runtime_integration_harness_v1"
    ] = INTEGRATION_PROFILE_VERSION
    activation_state: Literal["sandbox_only"] = ACTIVATION_STATE
    decision_event_id: Identifier
    decision_payload_sha256: Sha256Hex
    signal_id: Identifier
    candidate_id: Identifier
    correlation_id: Identifier
    decision_timestamp: datetime
    final_decision: Literal["ALLOW"] = "ALLOW"
    risk_approved: Literal[True] = True
    writer_invoked: Literal[False] = False
    runtime_integration: Literal[False] = False
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False

    @field_validator("decision_timestamp")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return require_utc(value)


class TradeLinkPreviewRequestV1(_FrozenModel):
    decision_event_id: Identifier
    trade_observation: dict[str, object]


class TradeLinkPreviewResultV1(_FrozenModel):
    status: Literal["ok", "blocked"]
    reason: NonEmptyText | None
    projection: RuntimeTradeLinkProjectionV1 | None
    writer_invoked: Literal[False] = False
    runtime_integration: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
