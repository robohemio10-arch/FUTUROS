"""Research-only OOS causal attribution for Paper/Master divergence.

The package is intentionally pure and non-operational. It does not read live
exchange state, mutate Freqtrade, alter RiskManager, update Qlib runtime, or
promote models/rules.
"""

from .causal_attribution import (
    SCHEMA_VERSION,
    build_oos_causal_attribution_report,
    run_oos_causal_attribution_research,
)

__all__ = [
    "SCHEMA_VERSION",
    "build_oos_causal_attribution_report",
    "run_oos_causal_attribution_research",
]
