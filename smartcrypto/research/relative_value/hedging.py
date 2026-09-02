"""Beta-neutral hedge and rebalance diagnostics."""

from __future__ import annotations

from .contracts import BasisScenario, PairRelativeValueScenario, RebalanceState


def target_hedge_ratio(beta_a_to_b: float) -> float:
    if beta_a_to_b <= 0:
        raise ValueError("beta_must_be_positive")
    return beta_a_to_b


def beta_residual(beta_a_to_b: float, hedge_ratio: float) -> float:
    if beta_a_to_b <= 0 or hedge_ratio <= 0:
        raise ValueError("beta_and_hedge_ratio_must_be_positive")
    return abs(beta_a_to_b - hedge_ratio)


def is_beta_neutral(scenario: PairRelativeValueScenario) -> bool:
    return beta_residual(scenario.beta_a_to_b, scenario.hedge_ratio) <= scenario.max_beta_residual


def rebalance_diagnostics(scenario: PairRelativeValueScenario) -> tuple[RebalanceState, float | None]:
    if scenario.prior_hedge_ratio is None:
        return RebalanceState.NO_REBALANCE, None
    drift = abs(scenario.hedge_ratio - scenario.prior_hedge_ratio)
    if drift > scenario.rebalance_tolerance:
        return RebalanceState.REBALANCE_RESEARCH, drift
    return RebalanceState.NO_REBALANCE, drift


def basis_delta_residual(hedge_ratio: float) -> float:
    if hedge_ratio <= 0:
        raise ValueError("hedge_ratio_must_be_positive")
    return abs(1.0 - hedge_ratio)


def is_basis_delta_neutral(scenario: BasisScenario) -> bool:
    return basis_delta_residual(scenario.hedge_ratio) <= scenario.max_delta_residual


def basis_rebalance_diagnostics(
    scenario: BasisScenario,
) -> tuple[RebalanceState, float | None]:
    if scenario.prior_hedge_ratio is None:
        return RebalanceState.NO_REBALANCE, None
    drift = abs(scenario.hedge_ratio - scenario.prior_hedge_ratio)
    if drift > scenario.rebalance_tolerance:
        return RebalanceState.REBALANCE_RESEARCH, drift
    return RebalanceState.NO_REBALANCE, drift
