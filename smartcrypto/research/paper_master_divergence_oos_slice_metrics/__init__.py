"""Research-only OOS slice metrics for Paper/Master divergence."""

from smartcrypto.research.paper_master_divergence_oos_slice_metrics.slice_metrics import (
    SCHEMA_VERSION,
    build_oos_slice_metrics_report,
    compute_slice_metrics,
    run_oos_slice_metrics_research,
)

__all__ = [
    "SCHEMA_VERSION",
    "build_oos_slice_metrics_report",
    "compute_slice_metrics",
    "run_oos_slice_metrics_research",
]
