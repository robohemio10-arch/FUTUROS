"""Immutable contracts for paper Decision Ledger observability wiring."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from smartcrypto.execution.decision_ledger_paper_runtime_writer_v1 import (
    PaperRuntimeWriterProfileV1,
)

SCHEMA_VERSION: Literal[
    "decision_ledger_paper_observability_wiring_v1"
] = "decision_ledger_paper_observability_wiring_v1"
DEFAULT_CONFIG_PATH = "config/decision_ledger_paper_observability.yml"
CANONICAL_INDEX_PATH = (
    "data/runtime/decision_ledger_paper_v1/idempotency_projection_index_v1.json"
)

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$",
    ),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class ObservabilitySafetyFlagsV1(FrozenContract):
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    live_trading_enabled: Literal[False] = False
    order_submission_enabled: Literal[False] = False
    real_order_submission_enabled: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    operational_authority: Literal[False] = False
    updates_freqtrade_policy: Literal[False] = False
    updates_qlib_runtime: Literal[False] = False
    updates_ai_shadow_runtime: Literal[False] = False


class PaperObservabilityWiringConfigV1(FrozenContract):
    schema_version: Literal[
        "decision_ledger_paper_observability_wiring_v1"
    ] = SCHEMA_VERSION
    enabled: bool = False
    writer_enabled: bool = False
    trade_link_enabled: bool = False
    fail_closed: Literal[True] = True
    index_path: str = CANONICAL_INDEX_PATH
    feature_contract_version: Identifier = "paper-signal-observation-lineage-v1"
    model_id: Identifier | None = None
    model_hash: Sha256Hex | None = None
    writer_profile: PaperRuntimeWriterProfileV1 = Field(
        default_factory=PaperRuntimeWriterProfileV1
    )
    safety_flags: ObservabilitySafetyFlagsV1 = Field(
        default_factory=ObservabilitySafetyFlagsV1
    )

    @model_validator(mode="after")
    def _validate_activation(self) -> "PaperObservabilityWiringConfigV1":
        if not self.enabled:
            if self.writer_enabled or self.trade_link_enabled:
                raise ValueError("disabled_wiring_cannot_enable_writer_or_trade_link")
            if self.writer_profile.enabled:
                raise ValueError("disabled_wiring_requires_disabled_writer_profile")
            return self
        if not self.writer_enabled:
            raise ValueError("enabled_wiring_requires_writer_enabled")
        if not self.writer_profile.enabled:
            raise ValueError("enabled_wiring_requires_enabled_writer_profile")
        if self.model_hash is None:
            raise ValueError("enabled_wiring_requires_authoritative_model_hash")
        return self


class PreparedSignalBatchV1(FrozenContract):
    schema_version: Literal[
        "decision_ledger_paper_observability_prepared_batch_v1"
    ] = "decision_ledger_paper_observability_prepared_batch_v1"
    producer_id: Identifier
    enabled: bool
    source_signal_count: int = Field(ge=0)
    prepared_signal_count: int = Field(ge=0)
    signals: tuple[dict[str, object], ...]
    config: PaperObservabilityWiringConfigV1
    lineage_built_before_risk_manager: bool

    @model_validator(mode="after")
    def _validate_counts(self) -> "PreparedSignalBatchV1":
        if self.source_signal_count != len(self.signals):
            raise ValueError("prepared_batch_source_count_mismatch")
        if self.prepared_signal_count != len(self.signals):
            raise ValueError("prepared_batch_prepared_count_mismatch")
        return self


class SinkAppendReceiptV1(FrozenContract):
    schema_version: Literal[
        "decision_ledger_idempotent_runtime_sink_receipt_v1"
    ] = "decision_ledger_idempotent_runtime_sink_receipt_v1"
    idempotency_key: Identifier
    event_id: Identifier
    payload_sha256: Sha256Hex
    duplicate: bool
    append_performed: bool
    index_write_performed: bool


class WiringReportV1(FrozenContract):
    schema_version: Literal[
        "decision_ledger_paper_observability_wiring_report_v1"
    ] = "decision_ledger_paper_observability_wiring_report_v1"
    status: Literal["disabled", "ok", "blocked"]
    reason: str | None
    producer_id: Identifier
    enabled: bool
    writer_enabled: bool
    trade_link_enabled: bool
    source_signal_count: int = Field(ge=0)
    approved_signal_count: int = Field(ge=0)
    rejected_signal_count: int = Field(ge=0)
    projected_decision_count: int = Field(ge=0)
    persisted_decision_count: int = Field(ge=0)
    duplicate_decision_count: int = Field(ge=0)
    active_envelope_count: int = Field(ge=0)
    projection_failure_count: int = Field(ge=0)
    publication_blocked: bool
    writer_invoked: bool
    writes_runtime: bool
    paper_behavior_changed: Literal[False] = False
    runtime_integration_executed: bool
    preflight_status: str | None
    factory_status: str | None
    failures: tuple[dict[str, object], ...] = ()
    receipts: tuple[SinkAppendReceiptV1, ...] = ()
    safety_flags: ObservabilitySafetyFlagsV1 = Field(
        default_factory=ObservabilitySafetyFlagsV1
    )


class TradeLinkAdapterReportV1(FrozenContract):
    schema_version: Literal[
        "decision_ledger_phase14_trade_link_adapter_v1"
    ] = "decision_ledger_phase14_trade_link_adapter_v1"
    status: Literal["disabled", "ok", "blocked"]
    reason: str | None
    enabled: bool
    source_database_read_only: Literal[True] = True
    timestamp_only_matching_allowed: Literal[False] = False
    automatic_replay_allowed: Literal[False] = False
    source_trade_count: int = Field(ge=0)
    correlated_trade_count: int = Field(ge=0)
    projected_trade_link_count: int = Field(ge=0)
    persisted_trade_link_count: int = Field(ge=0)
    duplicate_trade_link_count: int = Field(ge=0)
    writer_invoked: bool
    writes_runtime: bool
    writes_sqlite: Literal[False] = False
    modifies_trade_or_pnl: Literal[False] = False
    failures: tuple[dict[str, object], ...] = ()
    safety_flags: ObservabilitySafetyFlagsV1 = Field(
        default_factory=ObservabilitySafetyFlagsV1
    )
