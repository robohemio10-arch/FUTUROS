"""Research-only real source loader for Paper/Master divergence OOS analysis."""

from smartcrypto.research.paper_master_divergence_oos_real_source_loader.real_source_loader import (
    MINIMUM_NORMALIZED_COLUMNS,
    OOS_SLICE_DIMENSIONS,
    SCHEMA_VERSION,
    build_paper_master_divergence_oos_real_source_loader_report,
    load_trade_source,
    normalize_trade_rows,
)

__all__ = [
    "MINIMUM_NORMALIZED_COLUMNS",
    "OOS_SLICE_DIMENSIONS",
    "SCHEMA_VERSION",
    "build_paper_master_divergence_oos_real_source_loader_report",
    "load_trade_source",
    "normalize_trade_rows",
]
