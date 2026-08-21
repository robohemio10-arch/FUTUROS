"""Strict prospective candidate-to-trade lineage propagation primitives."""

from .adapter import (
    FORBIDDEN_POST_OUTCOME_FIELD_PATTERNS,
    LEGACY_REGISTRY_FALLBACK_PATTERN,
    build_research_candidate_reference,
    materialize_concrete_signal_identity,
)
from .contracts import (
    IDENTITY_SOURCE,
    RESEARCH_CANDIDATE_SOURCE,
    SCHEMA_VERSION,
    AuthoritativeCandidateSignalIdentityV1,
    ConcreteSignalIdentityBindingV1,
    ConcreteSignalOccurrenceV1,
    ResearchSignalCandidateReferenceV1,
    StrictDecisionProjectionV1,
    StrictLineageSafetyFlagsV1,
    StrictTradeLinkProjectionV1,
)
from .decision_projection import (
    StrictDecisionInMemoryOutcomeV1,
    StrictDecisionInMemoryReportV1,
    project_strict_decision_envelopes_in_memory,
)
from .mapper import (
    CandidateLineageError,
    build_authoritative_signal_identity,
    project_strict_decision,
    project_strict_trade_link,
)
from .producer_materialization import (
    SignalProducerLineageMaterializationOutcomeV1,
    SignalProducerLineageMaterializationReportV1,
    materialize_signal_batch_from_explicit_provenance,
)
from .publication import (
    ATTESTATION_KEY,
    ATTESTATION_SCHEMA,
    DECISION_LEDGER_KEY,
    PUBLICATION_SCHEMA,
    PaperLineagePublicationResultV1,
    attach_materialized_identity_to_signal,
    select_non_blocking_paper_publication_signals,
)

from .trade_projection import (
    StrictClosedPaperTradeLinkOutcomeV1,
    StrictClosedPaperTradeLinkReportV1,
    extract_explicit_decision_event_id,
    project_closed_paper_trade_link_readonly,
)

__all__ = [
    "ATTESTATION_KEY",
    "ATTESTATION_SCHEMA",
    "DECISION_LEDGER_KEY",
    "FORBIDDEN_POST_OUTCOME_FIELD_PATTERNS",
    "IDENTITY_SOURCE",
    "LEGACY_REGISTRY_FALLBACK_PATTERN",
    "PUBLICATION_SCHEMA",
    "RESEARCH_CANDIDATE_SOURCE",
    "SCHEMA_VERSION",
    "AuthoritativeCandidateSignalIdentityV1",
    "CandidateLineageError",
    "ConcreteSignalIdentityBindingV1",
    "ConcreteSignalOccurrenceV1",
    "PaperLineagePublicationResultV1",
    "ResearchSignalCandidateReferenceV1",
    "SignalProducerLineageMaterializationOutcomeV1",
    "SignalProducerLineageMaterializationReportV1",
    "StrictDecisionInMemoryOutcomeV1",
    "StrictDecisionInMemoryReportV1",
    "StrictDecisionProjectionV1",
    "StrictLineageSafetyFlagsV1",
    "StrictTradeLinkProjectionV1",
    "attach_materialized_identity_to_signal",
    "build_authoritative_signal_identity",
    "build_research_candidate_reference",
    "materialize_concrete_signal_identity",
    "materialize_signal_batch_from_explicit_provenance",
    "project_strict_decision",
    "project_strict_decision_envelopes_in_memory",
    "project_strict_trade_link",
    "select_non_blocking_paper_publication_signals",
    "StrictClosedPaperTradeLinkOutcomeV1",
    "StrictClosedPaperTradeLinkReportV1",
    "extract_explicit_decision_event_id",
    "project_closed_paper_trade_link_readonly",
]
