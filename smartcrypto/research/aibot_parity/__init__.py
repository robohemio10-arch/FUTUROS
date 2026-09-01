"""Institutional research-only AIBOT Trader Master benchmark."""

from .benchmark_engine import build_aibot_benchmark, build_cli_payload
from .contracts import (
    BENCHMARK_SCHEMA_VERSION,
    LOADER_VERSION,
    SOURCE_INVESTMENT_ID,
    SOURCE_REGISTRY_SCHEMA_VERSION,
    TRADE_SCHEMA_VERSION,
    CanonicalFieldSpec,
    FieldClassification,
    SourceArtifactRecord,
    TraderMasterLoadResult,
    safety_flags,
)
from .performance_reconciliation import build_performance_reconciliation
from .source_registry import build_source_record, source_batch_id_for_sha256
from .trade_behavior_fingerprint import (
    build_behavior_fingerprint,
    build_rolling_behavior,
    compute_behavior_metrics,
)
from .trader_master_loader import (
    build_quality_audit,
    canonicalize_trader_master_frame,
    load_trader_master_readonly,
)

__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "LOADER_VERSION",
    "SOURCE_INVESTMENT_ID",
    "SOURCE_REGISTRY_SCHEMA_VERSION",
    "TRADE_SCHEMA_VERSION",
    "CanonicalFieldSpec",
    "FieldClassification",
    "SourceArtifactRecord",
    "TraderMasterLoadResult",
    "build_aibot_benchmark",
    "build_behavior_fingerprint",
    "build_cli_payload",
    "build_performance_reconciliation",
    "build_quality_audit",
    "build_rolling_behavior",
    "build_source_record",
    "canonicalize_trader_master_frame",
    "compute_behavior_metrics",
    "load_trader_master_readonly",
    "safety_flags",
    "source_batch_id_for_sha256",
]
