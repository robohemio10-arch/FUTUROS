from __future__ import annotations

from .auditor import audit_paper_shadow_soak_continuity_and_gap_accounting
from .catalog import EVIDENCE_SOURCES, iter_soak_gap_accounting_sources
from .contracts import (
    DEFAULT_DIAGNOSTIC_SOAK_DAYS,
    DEFAULT_MAX_CRITICAL_GAP_MINUTES,
    DEFAULT_MAX_WARNING_GAP_MINUTES,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_REQUIRED_SOAK_DAYS,
    PROJECT_NAME,
    SCHEMA_VERSION,
    GapAccountingResult,
    GapWindow,
    SoakEvidenceSource,
)

__all__ = [
    "DEFAULT_DIAGNOSTIC_SOAK_DAYS",
    "DEFAULT_MAX_CRITICAL_GAP_MINUTES",
    "DEFAULT_MAX_WARNING_GAP_MINUTES",
    "DEFAULT_OUTPUT_PATH",
    "DEFAULT_REQUIRED_SOAK_DAYS",
    "EVIDENCE_SOURCES",
    "PROJECT_NAME",
    "SCHEMA_VERSION",
    "GapAccountingResult",
    "GapWindow",
    "SoakEvidenceSource",
    "audit_paper_shadow_soak_continuity_and_gap_accounting",
    "iter_soak_gap_accounting_sources",
]
