"""Paper closed trades shadow rule attribution V1."""

from smartcrypto.research.paper_closed_trades_shadow_rule_attribution.attribution import (
    DEFAULT_OUTPUT_REPORT,
    SCHEMA_VERSION,
    attribute_closed_trades_to_shadow_replay,
    build_paper_closed_trades_shadow_rule_attribution_report,
    compute_attribution_metrics,
    load_attribution_inputs,
)

__all__ = [
    "DEFAULT_OUTPUT_REPORT",
    "SCHEMA_VERSION",
    "attribute_closed_trades_to_shadow_replay",
    "build_paper_closed_trades_shadow_rule_attribution_report",
    "compute_attribution_metrics",
    "load_attribution_inputs",
]
