"""OCR Shadow Research explicit evidence pack V1."""

from smartcrypto.research.ocr_shadow_research_explicit_evidence_pack.evidence_pack import (
    ALLOWED_STAGE_IDS,
    DEFAULT_MARKDOWN_REPORT,
    DEFAULT_OUTPUT_REPORT,
    SCHEMA_VERSION,
    build_ocr_shadow_research_explicit_evidence_pack_report,
    run_stage,
    validate_stage_selection,
)

__all__ = [
    "ALLOWED_STAGE_IDS",
    "DEFAULT_MARKDOWN_REPORT",
    "DEFAULT_OUTPUT_REPORT",
    "SCHEMA_VERSION",
    "build_ocr_shadow_research_explicit_evidence_pack_report",
    "run_stage",
    "validate_stage_selection",
]
