"""Strict prospective candidate-to-trade lineage contracts.

This package is intentionally isolated from publishers and runtime writers.
It validates the exact identity fields required by the existing Decision Ledger
field-source registry and binds them to the existing runtime projections.

Stage 2 adds an explicit research-candidate -> concrete-signal materialization
contract. The research candidate identity may be promoted to the runtime
candidate_id only after it is proven against the registry candidate that
originated it. Concrete signal_id/correlation_id are produced only from an
explicit signal occurrence key at the signal materialization boundary; they
are never repaired from trade IDs, timestamps, symbol/side heuristics, random
values, or post-outcome fields.
"""

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

from smartcrypto.execution.decision_ledger_runtime_profile_v1.contracts import (
    RuntimeDecisionProjectionV1,
    RuntimeTradeLinkProjectionV1,
)


SCHEMA_VERSION: Final[
    Literal["paper_candidate_trade_lineage_propagation_v1"]
] = "paper_candidate_trade_lineage_propagation_v1"

IDENTITY_SOURCE: Final[
    Literal["signal_producer_candidate"]
] = "signal_producer_candidate"

RESEARCH_CANDIDATE_SOURCE: Final[
    Literal["paper_ai_signal_candidate_producer_v1"]
] = "paper_ai_signal_candidate_producer_v1"

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$",
    ),
]
Sha256Hex = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]


class FrozenContract(BaseModel):
    """Strict immutable Pydantic base contract."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class StrictLineageSafetyFlagsV1(FrozenContract):
    """Safety invariants for isolated prospective lineage stages."""

    prospective_only: Literal[True] = True
    historical_backfill_allowed: Literal[False] = False
    fuzzy_linkage_allowed: Literal[False] = False
    timestamp_only_matching_allowed: Literal[False] = False
    symbol_side_only_matching_allowed: Literal[False] = False
    trade_id_as_candidate_id_allowed: Literal[False] = False

    synthetic_candidate_id_allowed: Literal[False] = False
    synthetic_signal_id_allowed: Literal[False] = False
    synthetic_correlation_id_allowed: Literal[False] = False
    fallback_identity_generation_allowed: Literal[False] = False
    research_alias_silent_reinterpretation_allowed: Literal[False] = False
    post_outcome_identity_inputs_allowed: Literal[False] = False

    deterministic_signal_materialization_allowed: Literal[True] = True
    explicit_signal_instance_key_required: Literal[True] = True
    registry_candidate_proof_required: Literal[True] = True

    publisher_touched: Literal[False] = False
    writer_invoked: Literal[False] = False
    writes_runtime: Literal[False] = False
    writes_sqlite: Literal[False] = False

    changes_strategy: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_stake: Literal[False] = False
    changes_leverage: Literal[False] = False
    changes_model: Literal[False] = False

    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    live_release_allowed: Literal[False] = False
    canary_release_allowed: Literal[False] = False


class AuthoritativeCandidateSignalIdentityV1(FrozenContract):
    """Exact identity fields emitted by an authoritative concrete signal."""

    schema_version: Literal[
        "paper_candidate_trade_lineage_propagation_v1"
    ] = SCHEMA_VERSION

    candidate_id: Identifier
    signal_id: Identifier
    correlation_id: Identifier

    identity_source: Literal["signal_producer_candidate"] = IDENTITY_SOURCE
    source_signal_sha256: Sha256Hex

    candidate_id_origin: Literal["candidate_id"] = "candidate_id"
    signal_id_origin: Literal["signal_id"] = "signal_id"
    correlation_id_origin: Literal["correlation_id"] = "correlation_id"

    authoritative_candidate_id: Literal[True] = True
    authoritative_signal_id: Literal[True] = True
    authoritative_correlation_id: Literal[True] = True

    synthetic_candidate_id: Literal[False] = False
    synthetic_signal_id: Literal[False] = False
    synthetic_correlation_id: Literal[False] = False
    trade_id_used_as_candidate_id: Literal[False] = False


class ResearchSignalCandidateReferenceV1(FrozenContract):
    """Verified provenance of one research signal-candidate row.

    The research ``source_candidate_id`` is not silently treated as a runtime
    candidate ID. It becomes eligible for explicit promotion only after exact
    equality with the authoritative registry candidate_id is proved.
    """

    schema_version: Literal[
        "paper_candidate_trade_lineage_research_candidate_reference_v1"
    ] = "paper_candidate_trade_lineage_research_candidate_reference_v1"

    source_system: Literal[
        "paper_ai_signal_candidate_producer_v1"
    ] = RESEARCH_CANDIDATE_SOURCE

    producer_id: Identifier
    source_candidate_id: Identifier
    signal_candidate_id: Identifier
    registry_candidate_id: Identifier
    signal_actionability: Literal["research_observation_only", "blocked"]

    research_candidate_sha256: Sha256Hex
    registry_candidate_sha256: Sha256Hex

    registry_candidate_identity_verified: Literal[True] = True
    signal_candidate_id_integrity_verified: Literal[True] = True
    legacy_registry_fallback_rejected: Literal[True] = True
    runtime_candidate_id_materialized: Literal[False] = False
    runtime_signal_id_materialized: Literal[False] = False

    @model_validator(mode="after")
    def _validate_registry_binding(self) -> "ResearchSignalCandidateReferenceV1":
        if self.source_candidate_id != self.registry_candidate_id:
            raise ValueError("research_candidate_registry_identity_mismatch")
        return self


class ConcreteSignalOccurrenceV1(FrozenContract):
    """Explicit pre-execution signal occurrence used to materialize identity.

    ``signal_instance_id`` must come from the concrete signal production
    boundary. It is intentionally required so a static research candidate row
    cannot create an arbitrary number of runtime signal identities by itself.
    """

    schema_version: Literal[
        "paper_candidate_trade_lineage_concrete_signal_occurrence_v1"
    ] = "paper_candidate_trade_lineage_concrete_signal_occurrence_v1"

    producer_id: Identifier
    signal_instance_id: Identifier
    signal_timestamp_utc: datetime

    pair: NonEmptyText
    symbol: Identifier
    side: Literal["long", "short"]
    regime: NonEmptyText

    occurrence_source_sha256: Sha256Hex

    operational_authority: Literal[False] = False
    runtime_integration: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False

    @field_validator("signal_timestamp_utc")
    @classmethod
    def _require_utc(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None:
            raise ValueError("signal_timestamp_must_be_timezone_aware_utc")
        if offset.total_seconds() != 0:
            raise ValueError("signal_timestamp_must_use_utc_offset_zero")
        return value.astimezone(timezone.utc)


class ConcreteSignalIdentityBindingV1(FrozenContract):
    """Auditable binding from research candidate to concrete signal identity."""

    schema_version: Literal[
        "paper_candidate_trade_lineage_concrete_signal_identity_binding_v1"
    ] = "paper_candidate_trade_lineage_concrete_signal_identity_binding_v1"

    research_reference: ResearchSignalCandidateReferenceV1
    occurrence: ConcreteSignalOccurrenceV1
    identity: AuthoritativeCandidateSignalIdentityV1
    materialization_sha256: Sha256Hex

    candidate_id_propagated_unchanged: Literal[True] = True
    signal_id_generated_at_authoritative_materialization_boundary: Literal[True] = True
    correlation_id_generated_at_authoritative_materialization_boundary: Literal[True] = True
    signal_candidate_id_reused_as_signal_id: Literal[False] = False
    timestamp_used_as_sole_identity: Literal[False] = False
    random_identity_used: Literal[False] = False

    safety_flags: StrictLineageSafetyFlagsV1 = Field(
        default_factory=StrictLineageSafetyFlagsV1
    )

    @model_validator(mode="after")
    def _validate_binding(self) -> "ConcreteSignalIdentityBindingV1":
        if self.research_reference.producer_id != self.occurrence.producer_id:
            raise ValueError("materialization_producer_id_mismatch")
        if self.identity.candidate_id != self.research_reference.registry_candidate_id:
            raise ValueError("materialized_candidate_id_not_registry_identity")
        if self.identity.signal_id == self.research_reference.signal_candidate_id:
            raise ValueError("research_signal_candidate_id_cannot_be_runtime_signal_id")
        return self


class StrictDecisionProjectionV1(FrozenContract):
    """Decision projection proven to originate from exact authoritative IDs."""

    schema_version: Literal[
        "paper_candidate_trade_lineage_strict_decision_projection_v1"
    ] = "paper_candidate_trade_lineage_strict_decision_projection_v1"

    identity: AuthoritativeCandidateSignalIdentityV1
    decision_projection: RuntimeDecisionProjectionV1
    safety_flags: StrictLineageSafetyFlagsV1 = Field(
        default_factory=StrictLineageSafetyFlagsV1
    )

    @model_validator(mode="after")
    def _validate_identity_binding(self) -> "StrictDecisionProjectionV1":
        target = self.decision_projection.target_payload
        if target.candidate_id != self.identity.candidate_id:
            raise ValueError("decision_candidate_id_mismatch")
        if target.signal_id != self.identity.signal_id:
            raise ValueError("decision_signal_id_mismatch")
        if target.correlation_id != self.identity.correlation_id:
            raise ValueError("decision_correlation_id_mismatch")
        if target.trade_id is not None:
            raise ValueError("decision_must_not_contain_trade_id")
        return self


class StrictTradeLinkProjectionV1(FrozenContract):
    """Prospective trade link bound to a previously strict decision projection."""

    schema_version: Literal[
        "paper_candidate_trade_lineage_strict_trade_link_projection_v1"
    ] = "paper_candidate_trade_lineage_strict_trade_link_projection_v1"

    identity: AuthoritativeCandidateSignalIdentityV1
    decision_event_id: Identifier
    trade_link_projection: RuntimeTradeLinkProjectionV1
    safety_flags: StrictLineageSafetyFlagsV1 = Field(
        default_factory=StrictLineageSafetyFlagsV1
    )

    @model_validator(mode="after")
    def _validate_identity_binding(self) -> "StrictTradeLinkProjectionV1":
        target = self.trade_link_projection.target_payload
        if target.parent_event_id != self.decision_event_id:
            raise ValueError("trade_link_parent_event_mismatch")
        if target.candidate_id != self.identity.candidate_id:
            raise ValueError("trade_link_candidate_id_mismatch")
        if target.signal_id != self.identity.signal_id:
            raise ValueError("trade_link_signal_id_mismatch")
        if target.correlation_id != self.identity.correlation_id:
            raise ValueError("trade_link_correlation_id_mismatch")
        return self
