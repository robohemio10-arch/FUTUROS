"""Research-only Paper A/B Edge Selector V1."""

from .assignment import assign_candidate, treatment_eligibility
from .contracts import (
    DECISION,
    FINANCIAL_EVIDENCE_STATES,
    REQUIRED_TREATMENT_GATES,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    ABObservation,
    ArmFinancialMetrics,
    AssignmentRecord,
    ExperimentConfig,
    IncrementalEdgeEvidence,
)
from .engine import (
    PaperABEdgeSelectorEngine,
    build_paper_ab_edge_selector_v1,
    deterministic_bootstrap_delta_expectancy,
)

__all__ = [
    "DECISION",
    "FINANCIAL_EVIDENCE_STATES",
    "REQUIRED_TREATMENT_GATES",
    "SAFETY_FLAGS",
    "SCHEMA_VERSION",
    "ABObservation",
    "ArmFinancialMetrics",
    "AssignmentRecord",
    "ExperimentConfig",
    "IncrementalEdgeEvidence",
    "PaperABEdgeSelectorEngine",
    "assign_candidate",
    "treatment_eligibility",
    "deterministic_bootstrap_delta_expectancy",
    "build_paper_ab_edge_selector_v1",
]
