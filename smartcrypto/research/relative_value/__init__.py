"""W7 Relative Value research-only package."""

from .basis import basis_bps, convergence_gross_bps, directional_convergence_bps
from .contracts import (
    BasisEvaluation,
    BasisScenario,
    CandidateStatus,
    CostModel,
    FundingObservation,
    PairEvaluation,
    PairRelativeValueScenario,
    PriceObservation,
    RebalanceState,
    RelativeValueRequest,
    RelativeValueSnapshot,
    RelativeValueStatus,
    SafetyContract,
)
from .evaluator import build_snapshot, evaluate_basis, evaluate_pair
from .funding import expected_funding_carry_bps, expected_funding_intervals, funding_carry_direction
from .hedging import (
    basis_delta_residual,
    basis_rebalance_diagnostics,
    beta_residual,
    is_basis_delta_neutral,
    is_beta_neutral,
    rebalance_diagnostics,
    target_hedge_ratio,
)
from .pairs import log_return, relative_value_spread_bps

__all__ = [
    "BasisEvaluation",
    "BasisScenario",
    "CandidateStatus",
    "CostModel",
    "FundingObservation",
    "PairEvaluation",
    "PairRelativeValueScenario",
    "PriceObservation",
    "RebalanceState",
    "RelativeValueRequest",
    "RelativeValueSnapshot",
    "RelativeValueStatus",
    "SafetyContract",
    "basis_bps",
    "basis_delta_residual",
    "basis_rebalance_diagnostics",
    "beta_residual",
    "build_snapshot",
    "convergence_gross_bps",
    "directional_convergence_bps",
    "evaluate_basis",
    "evaluate_pair",
    "expected_funding_carry_bps",
    "expected_funding_intervals",
    "funding_carry_direction",
    "is_basis_delta_neutral",
    "is_beta_neutral",
    "log_return",
    "rebalance_diagnostics",
    "relative_value_spread_bps",
    "target_hedge_ratio",
]
