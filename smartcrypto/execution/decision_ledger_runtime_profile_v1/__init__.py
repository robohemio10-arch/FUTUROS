"""P0.4B sandbox-only runtime profile and mapping specification."""

from .contracts import (
    ACTIVATION_STATE,
    PROFILE_VERSION,
    RuntimeDecisionInputV1,
    RuntimeDecisionLineageV1,
    RuntimeDecisionProjectionV1,
    RuntimeProjectionRecordV1,
    RuntimeTradeLinkLineageV1,
    RuntimeTradeLinkProjectionV1,
    RuntimeTradeObservationInputV1,
)
from .field_sources import (
    ENVELOPE_EXTENSION_FIELDS,
    FIELD_SOURCE_REGISTRY,
    REQUIRED_TARGET_FIELDS,
    registry_payload,
    registry_sha256,
    validate_registry,
)
from .identifiers import (
    canonical_mapping_sha256,
    decision_idempotency_key,
    event_id_from_idempotency_key,
    normalize_symbol,
    trade_link_idempotency_key,
)
from .mapping import map_runtime_decision, map_runtime_trade_link
from .schema import build_runtime_profile_schema, write_runtime_profile_schema

__all__ = [
    "ACTIVATION_STATE",
    "ENVELOPE_EXTENSION_FIELDS",
    "FIELD_SOURCE_REGISTRY",
    "PROFILE_VERSION",
    "REQUIRED_TARGET_FIELDS",
    "RuntimeDecisionInputV1",
    "RuntimeDecisionLineageV1",
    "RuntimeDecisionProjectionV1",
    "RuntimeProjectionRecordV1",
    "RuntimeTradeLinkLineageV1",
    "RuntimeTradeLinkProjectionV1",
    "RuntimeTradeObservationInputV1",
    "build_runtime_profile_schema",
    "canonical_mapping_sha256",
    "decision_idempotency_key",
    "event_id_from_idempotency_key",
    "map_runtime_decision",
    "map_runtime_trade_link",
    "normalize_symbol",
    "registry_payload",
    "registry_sha256",
    "trade_link_idempotency_key",
    "validate_registry",
    "write_runtime_profile_schema",
]
