"""Canonical data foundation V2 for research-only lineage and manifests."""

from .candles import (
    CandleRecoveryResult,
    CandleSourceSpec,
    PublicCandleRequestPolicy,
    recover_blocked_candles,
)
from .contracts import (
    CANONICAL_DATA_FOUNDATION_SCHEMA_VERSION,
    DATASET_CONTRACTS,
    SAFETY_FLAGS,
    DatasetBoundary,
    DatasetBoundaryError,
    FieldEvidence,
    build_dataset_manifest,
    validate_dataset_write,
)
from .lineage import LineageResult, build_trader_master_lineage
from .manifest import (
    ExecutionManifest,
    ManifestValidationError,
    build_execution_manifest,
    write_execution_manifest,
)
from .pipeline import build_canonical_data_foundation_report

__all__ = [
    "CANONICAL_DATA_FOUNDATION_SCHEMA_VERSION",
    "DATASET_CONTRACTS",
    "SAFETY_FLAGS",
    "CandleRecoveryResult",
    "CandleSourceSpec",
    "DatasetBoundary",
    "DatasetBoundaryError",
    "ExecutionManifest",
    "FieldEvidence",
    "LineageResult",
    "ManifestValidationError",
    "PublicCandleRequestPolicy",
    "build_canonical_data_foundation_report",
    "build_dataset_manifest",
    "build_execution_manifest",
    "build_trader_master_lineage",
    "recover_blocked_candles",
    "validate_dataset_write",
    "write_execution_manifest",
]
