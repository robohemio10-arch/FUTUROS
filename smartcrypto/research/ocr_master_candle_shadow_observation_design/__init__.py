"""Research-only shadow observation design for OCR Master + candle OOS survivors."""

from smartcrypto.research.ocr_master_candle_shadow_observation_design.observation_design import (
    OBSERVATION_CONTRACT_VERSION,
    SCHEMA_VERSION,
    build_observation_records,
    build_shadow_observation_design_report,
)

__all__ = [
    "OBSERVATION_CONTRACT_VERSION",
    "SCHEMA_VERSION",
    "build_observation_records",
    "build_shadow_observation_design_report",
]
