"""Research-only OOS real slice computation for Paper/Master divergence.

This package is intentionally read-only. It computes descriptive research metrics
only when explicit input rows or explicit read-enabled sources are supplied.
"""

from .real_slice_computation import (
    EXPECTED_TRADE_VALUE_FORMULA,
    OOS_SLICE_DIMENSIONS,
    build_oos_real_slice_computation_report,
    compute_oos_real_slice_metrics,
)

__all__ = [
    "EXPECTED_TRADE_VALUE_FORMULA",
    "OOS_SLICE_DIMENSIONS",
    "build_oos_real_slice_computation_report",
    "compute_oos_real_slice_metrics",
]
