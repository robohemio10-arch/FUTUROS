"""Research-only V5 quality-gated projection contract."""

from .contracts import MODEL_FEATURES, SCHEMA_VERSION
from .projection import build_quality_gated_v5_contract_report

__all__ = [
    "MODEL_FEATURES",
    "SCHEMA_VERSION",
    "build_quality_gated_v5_contract_report",
]
