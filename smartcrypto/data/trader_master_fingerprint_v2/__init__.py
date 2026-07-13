"""Trader Master fingerprint V2 and read-only staging validation."""

from .fingerprint_spec import (
    FINGERPRINT_SPEC_VERSION,
    NORMALIZER_VERSION,
    canonical_json,
    canonical_trade_id_for,
    normalize_trade_row,
    primary_identity_for,
    row_fingerprint_for,
)
from .freqtrade_adapter import (
    FreqtradePaperAdapterBundle,
    build_freqtrade_paper_closed_trades_adapter_bundle,
    build_freqtrade_paper_closed_trades_adapter_report,
)
from .master_reconciliation import build_trader_master_reconciliation_report
from .quarantine_forensics import build_targeted_quarantine_forensics_report
from .quarantine_recovery import (
    AuthoritativeRecovery,
    apply_authoritative_recoveries,
    build_authoritative_recovery_map,
)
from .source_profile import load_source_profile
from .staging_validator import (
    KillSwitchMonitor,
    validate_staging_records,
)

__all__ = [
    "FINGERPRINT_SPEC_VERSION",
    "KillSwitchMonitor",
    "NORMALIZER_VERSION",
    "FreqtradePaperAdapterBundle",
    "canonical_json",
    "canonical_trade_id_for",
    "build_freqtrade_paper_closed_trades_adapter_bundle",
    "build_freqtrade_paper_closed_trades_adapter_report",
    "build_trader_master_reconciliation_report",
    "build_targeted_quarantine_forensics_report",
    "AuthoritativeRecovery",
    "apply_authoritative_recoveries",
    "build_authoritative_recovery_map",
    "load_source_profile",
    "normalize_trade_row",
    "primary_identity_for",
    "row_fingerprint_for",
    "validate_staging_records",
]
