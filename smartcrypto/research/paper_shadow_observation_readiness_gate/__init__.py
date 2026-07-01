"""Paper shadow observation readiness gate V1."""

from smartcrypto.research.paper_shadow_observation_readiness_gate.readiness_gate import (
    DEFAULT_OUTPUT_REPORT,
    SCHEMA_VERSION,
    build_paper_shadow_observation_readiness_gate_report,
    compute_readiness_gate,
    load_readiness_inputs,
)

__all__ = [
    "DEFAULT_OUTPUT_REPORT",
    "SCHEMA_VERSION",
    "build_paper_shadow_observation_readiness_gate_report",
    "compute_readiness_gate",
    "load_readiness_inputs",
]
