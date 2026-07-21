"""Disabled-by-default Decision Ledger paper observability wiring."""

from .config import load_observability_config
from .contracts import (
    CANONICAL_INDEX_PATH,
    DEFAULT_CONFIG_PATH,
    SCHEMA_VERSION,
    ObservabilitySafetyFlagsV1,
    PaperObservabilityWiringConfigV1,
    PreparedSignalBatchV1,
    SinkAppendReceiptV1,
    TradeLinkAdapterReportV1,
    WiringReportV1,
)
from .coordinator import (
    PaperObservabilityOutcomeV1,
    finalize_after_risk_manager,
    prepare_before_risk_manager,
)
from .lineage import canonical_observation_sha256, complete_after_risk_manager
from .sink import (
    CriticalIdempotencyConflict,
    IdempotentDecisionLedgerRuntimeSink,
    PersistentIndexError,
    RuntimeSinkError,
)
from .trade_link import sync_phase14_trade_links_readonly

__all__ = [
    "CANONICAL_INDEX_PATH",
    "DEFAULT_CONFIG_PATH",
    "SCHEMA_VERSION",
    "CriticalIdempotencyConflict",
    "IdempotentDecisionLedgerRuntimeSink",
    "ObservabilitySafetyFlagsV1",
    "PaperObservabilityOutcomeV1",
    "PaperObservabilityWiringConfigV1",
    "PersistentIndexError",
    "PreparedSignalBatchV1",
    "RuntimeSinkError",
    "SinkAppendReceiptV1",
    "TradeLinkAdapterReportV1",
    "WiringReportV1",
    "canonical_observation_sha256",
    "complete_after_risk_manager",
    "finalize_after_risk_manager",
    "load_observability_config",
    "prepare_before_risk_manager",
    "sync_phase14_trade_links_readonly",
]
