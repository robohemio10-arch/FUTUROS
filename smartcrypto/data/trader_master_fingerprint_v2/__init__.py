"""Trader Master fingerprint V2 and read-only staging validation."""

from .fingerprint_spec import (
    FINGERPRINT_SPEC_VERSION,
    NORMALIZER_VERSION,
    canonical_trade_id_for,
    normalize_trade_row,
    primary_identity_for,
    row_fingerprint_for,
)
from .freqtrade_adapter import build_freqtrade_paper_closed_trades_adapter_report
from .quarantine_forensics import build_targeted_quarantine_forensics_report
from .source_profile import load_source_profile
from .staging_validator import (
    KillSwitchMonitor,
    validate_staging_records,
)

__all__ = [
    "FINGERPRINT_SPEC_VERSION",
    "KillSwitchMonitor",
    "NORMALIZER_VERSION",
    "canonical_trade_id_for",
    "build_freqtrade_paper_closed_trades_adapter_report",
    "build_targeted_quarantine_forensics_report",
    "load_source_profile",
    "normalize_trade_row",
    "primary_identity_for",
    "row_fingerprint_for",
    "validate_staging_records",
]
