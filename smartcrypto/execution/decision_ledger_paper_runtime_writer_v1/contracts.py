"""Immutable contracts for the disabled paper decision-ledger writer profile."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

PROFILE_VERSION: Final[
    Literal["decision_ledger_paper_runtime_writer_profile_v1"]
] = "decision_ledger_paper_runtime_writer_profile_v1"
QUARANTINE_SCHEMA_VERSION: Final[
    Literal["runtime_interruption_quarantine_v1_1"]
] = "runtime_interruption_quarantine_v1_1"
CANONICAL_ALLOWED_ROOT = "data/runtime/decision_ledger_paper_v1"
CANONICAL_LEDGER_PATH = f"{CANONICAL_ALLOWED_ROOT}/decision_ledger_v4_2.jsonl"
CANONICAL_HEALTH_PATH = f"{CANONICAL_ALLOWED_ROOT}/writer_health_v1.json"

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
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class FrozenContract(BaseModel):
    """Base contract with strict Pydantic v2 validation and immutability."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class WriterDurabilityContractV1(FrozenContract):
    """Durability requirements inherited by the certified writer factory."""

    lock_mode: Literal["exclusive_create"] = "exclusive_create"
    append_mode: Literal["append_only"] = "append_only"
    health_mode: Literal["atomic_replace"] = "atomic_replace"
    lock_required: Literal[True] = True
    file_fsync_required: Literal[True] = True
    health_fsync_required: Literal[True] = True
    parent_directory_fsync_required: Literal[True] = True
    lock_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)


class WriterHealthContractV1(FrozenContract):
    """Fail-visible health expectations for append attempts."""

    schema_version: Literal[
        "decision_ledger_writer_health_contract_v1"
    ] = "decision_ledger_writer_health_contract_v1"
    health_required: Literal[True] = True
    failure_counter_monotonic: Literal[True] = True
    consecutive_failure_counter_required: Literal[True] = True
    last_error_type_required: Literal[True] = True
    raw_error_message_persistence_allowed: Literal[False] = False
    error_message_sha256_required: Literal[True] = True
    automatic_recovery_allowed: Literal[False] = False


class PaperRuntimeWriterSafetyFlagsV1(FrozenContract):
    """Structural safety invariants for every report from this package."""

    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    disabled_by_default: Literal[True] = True
    fail_closed: Literal[True] = True
    operational_authority: Literal[False] = False
    runtime_integration_allowed: Literal[False] = False
    paper_restart_authorized: Literal[False] = False
    live_trading_enabled: Literal[False] = False
    order_submission_enabled: Literal[False] = False
    real_order_submission_enabled: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    updates_freqtrade: Literal[False] = False
    updates_risk_manager: Literal[False] = False
    updates_qlib_runtime: Literal[False] = False
    updates_ai_shadow_runtime: Literal[False] = False


class PaperRuntimeWriterProfileV1(FrozenContract):
    """Versioned profile; explicit enablement still grants no runtime wiring."""

    schema_version: Literal[
        "decision_ledger_paper_runtime_writer_profile_v1"
    ] = PROFILE_VERSION
    profile_id: Identifier = "paper-decision-ledger-writer-v1"
    runtime_mode: Literal["paper"] = "paper"
    activation_state: Literal["disabled", "preflight_only"] = "disabled"
    enabled: bool = False
    runtime_write_authorized: bool = False
    fail_closed: Literal[True] = True
    allowed_root: str = CANONICAL_ALLOWED_ROOT
    ledger_path: str = CANONICAL_LEDGER_PATH
    health_path: str = CANONICAL_HEALTH_PATH
    durability: WriterDurabilityContractV1 = Field(
        default_factory=WriterDurabilityContractV1
    )
    health: WriterHealthContractV1 = Field(default_factory=WriterHealthContractV1)
    safety_flags: PaperRuntimeWriterSafetyFlagsV1 = Field(
        default_factory=PaperRuntimeWriterSafetyFlagsV1
    )

    @model_validator(mode="after")
    def _validate_activation(self) -> "PaperRuntimeWriterProfileV1":
        explicitly_enabled = self.activation_state == "preflight_only"
        if self.enabled != explicitly_enabled:
            raise ValueError("enabled_must_match_preflight_only_activation")
        if self.runtime_write_authorized != self.enabled:
            raise ValueError("runtime_write_authorized_must_match_enabled")
        return self


class PathPolicyReportV1(FrozenContract):
    status: Literal["ok", "blocked"]
    reason: NonEmptyText
    project_root: str
    allowed_root: str
    ledger_path: str
    health_path: str
    allowed_root_exists: bool
    allowed_root_is_directory: bool
    allowed_root_writable: bool
    symlink_detected: bool
    paths_are_canonical: bool
    paths_within_allowed_root: bool


class RuntimeIdentityEvidenceV1(FrozenContract):
    source: Literal["posix_geteuid", "windows_admin_api", "unavailable", "test"]
    verified: bool
    elevated: bool | None
    effective_uid: int | None = Field(default=None, ge=0)
    reason: NonEmptyText


class PreflightCheckV1(FrozenContract):
    check_id: Identifier
    status: Literal["ok", "blocked"]
    reason: NonEmptyText


class WriterPreflightReportV1(FrozenContract):
    schema_version: Literal[
        "decision_ledger_paper_runtime_writer_preflight_v1"
    ] = "decision_ledger_paper_runtime_writer_preflight_v1"
    status: Literal["ready", "blocked"]
    reason: NonEmptyText
    profile_sha256: Sha256Hex
    profile_enabled: bool
    runtime_write_authorized: bool
    path_policy: PathPolicyReportV1
    identity: RuntimeIdentityEvidenceV1
    checks: tuple[PreflightCheckV1, ...]
    writer_creation_allowed: bool
    writer_factory_invoked: Literal[False] = False
    write_performed: Literal[False] = False
    safety_flags: PaperRuntimeWriterSafetyFlagsV1 = Field(
        default_factory=PaperRuntimeWriterSafetyFlagsV1
    )

    @model_validator(mode="after")
    def _validate_result(self) -> "WriterPreflightReportV1":
        all_checks_ok = bool(self.checks) and all(
            check.status == "ok" for check in self.checks
        )
        if self.writer_creation_allowed != all_checks_ok:
            raise ValueError("writer_creation_allowed_must_match_checks")
        if self.writer_creation_allowed != (self.status == "ready"):
            raise ValueError("preflight_status_must_match_writer_creation_allowed")
        return self


class WriterFactoryReportV1(FrozenContract):
    schema_version: Literal[
        "decision_ledger_paper_runtime_writer_factory_v1"
    ] = "decision_ledger_paper_runtime_writer_factory_v1"
    status: Literal["created", "blocked"]
    reason: NonEmptyText
    profile_sha256: Sha256Hex
    preflight_profile_sha256: Sha256Hex
    writer_factory_invoked: Literal[True] = True
    writer_created: bool
    runtime_wiring_performed: Literal[False] = False
    write_performed: Literal[False] = False
    safety_flags: PaperRuntimeWriterSafetyFlagsV1 = Field(
        default_factory=PaperRuntimeWriterSafetyFlagsV1
    )


class RuntimeInterruptionQuarantineV11(FrozenContract):
    """Sanitized evidence for a future append interruption; never auto-replayed."""

    schema_version: Literal[
        "runtime_interruption_quarantine_v1_1"
    ] = QUARANTINE_SCHEMA_VERSION
    quarantine_id: Identifier
    event_id: Identifier
    interrupted_at_utc: datetime
    interruption_stage: Literal[
        "preflight",
        "lock_acquisition",
        "append",
        "file_fsync",
        "health_update",
        "parent_directory_fsync",
    ]
    error_type: Identifier
    error_message_sha256: Sha256Hex
    payload_sha256: Sha256Hex | None = None
    quarantine_status: Literal["quarantined"] = "quarantined"
    operator_review_required: Literal[True] = True
    automatic_replay_allowed: Literal[False] = False
    writer_resume_authorized: Literal[False] = False
    runtime_integration_allowed: Literal[False] = False
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    writes_runtime: Literal[False] = False
    writes_sqlite: Literal[False] = False

    @field_validator("interrupted_at_utc")
    @classmethod
    def _require_utc(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None:
            raise ValueError("interrupted_at_utc_must_be_timezone_aware")
        if offset.total_seconds() != 0:
            raise ValueError("interrupted_at_utc_must_use_utc_offset_zero")
        return value.astimezone(timezone.utc)
