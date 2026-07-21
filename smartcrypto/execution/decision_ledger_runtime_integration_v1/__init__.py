"""P0.4C sandbox-only integration harness."""

from .contracts import (
    ACTIVATION_STATE,
    INTEGRATION_PROFILE_VERSION,
    ActiveSignalDecisionEnvelopeV1,
    IntegrationPreviewResultV1,
    ProjectionFailureV1,
    SandboxIntegrationConfigV1,
    TradeLinkPreviewRequestV1,
    TradeLinkPreviewResultV1,
)
from .envelope import ActiveSignalEnvelopeError, attach_decision_envelope
from .health import classify_sink_health
from .migration_guard import (
    LegacyWriterGuardError,
    inspect_legacy_strategy_writer,
    validate_migration_mode,
)
from .orchestrator import preview_after_risk_manager
from .sinks import (
    DisabledProjectionSink,
    InMemoryProjectionSink,
    ProjectionReceipt,
    ProjectionWriteDisabledError,
    SandboxFileProjectionSink,
    build_default_projection_sink,
)
from .source_adapter import (
    SignalSourceValidationError,
    build_runtime_decision_input,
    canonical_signal_sha256,
)
from .trade_link import build_decision_index, preview_trade_link

__all__ = [
    "ACTIVATION_STATE",
    "INTEGRATION_PROFILE_VERSION",
    "ActiveSignalDecisionEnvelopeV1",
    "ActiveSignalEnvelopeError",
    "DisabledProjectionSink",
    "InMemoryProjectionSink",
    "IntegrationPreviewResultV1",
    "LegacyWriterGuardError",
    "ProjectionFailureV1",
    "ProjectionReceipt",
    "ProjectionWriteDisabledError",
    "SandboxFileProjectionSink",
    "SandboxIntegrationConfigV1",
    "SignalSourceValidationError",
    "TradeLinkPreviewRequestV1",
    "TradeLinkPreviewResultV1",
    "attach_decision_envelope",
    "build_decision_index",
    "build_default_projection_sink",
    "build_runtime_decision_input",
    "canonical_signal_sha256",
    "classify_sink_health",
    "inspect_legacy_strategy_writer",
    "preview_after_risk_manager",
    "preview_trade_link",
    "validate_migration_mode",
]
