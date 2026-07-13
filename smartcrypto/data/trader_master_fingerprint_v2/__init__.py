"""Trader Master fingerprint V2 and read-only staging validation."""

from .fingerprint_spec import (
    FINGERPRINT_SPEC_VERSION,
    NORMALIZER_VERSION,
    canonical_trade_id_for,
    normalize_trade_row,
    primary_identity_for,
    row_fingerprint_for,
)
from .staging_validator import (
    KillSwitchMonitor,
    validate_staging_records,
)

__all__ = [
    "FINGERPRINT_SPEC_VERSION",
    "KillSwitchMonitor",
    "NORMALIZER_VERSION",
    "canonical_trade_id_for",
    "normalize_trade_row",
    "primary_identity_for",
    "row_fingerprint_for",
    "validate_staging_records",
]
