"""Snapshot-first W12/W13 orchestration for SMART FUTUROS AIBOT-Parity."""

from .contracts import (
    ALLOWED_SOURCE_NAMES,
    OPTIONAL_SOURCE_NAMES,
    PIPELINE_SCHEMA_VERSION,
    REQUIRED_SOURCE_NAMES,
    SAFETY_FLAGS,
    AibotParityPipelineRequest,
    AibotParityPipelineSnapshot,
    PipelineSourceView,
    PipelineStatus,
    PointInTimeStatus,
)
from .orchestrator import build_aibot_parity_pipeline
from .persistence import (
    AibotParityPipelinePersistenceError,
    persist_pipeline_snapshot,
)

__all__ = [
    "ALLOWED_SOURCE_NAMES",
    "OPTIONAL_SOURCE_NAMES",
    "PIPELINE_SCHEMA_VERSION",
    "REQUIRED_SOURCE_NAMES",
    "SAFETY_FLAGS",
    "AibotParityPipelinePersistenceError",
    "AibotParityPipelineRequest",
    "AibotParityPipelineSnapshot",
    "PipelineSourceView",
    "PipelineStatus",
    "PointInTimeStatus",
    "build_aibot_parity_pipeline",
    "persist_pipeline_snapshot",
]
