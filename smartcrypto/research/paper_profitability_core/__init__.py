"""Paper profitability research core."""

from .evaluator import (
    DEFAULT_OUTPUT_PATH,
    SCENARIO_MATRIX,
    evaluate_paper_candidate_profile_preflight,
    evaluate_paper_profitability_core,
    evaluate_scenario_matrix,
)

__all__ = [
    "DEFAULT_OUTPUT_PATH",
    "SCENARIO_MATRIX",
    "evaluate_paper_candidate_profile_preflight",
    "evaluate_paper_profitability_core",
    "evaluate_scenario_matrix",
]
