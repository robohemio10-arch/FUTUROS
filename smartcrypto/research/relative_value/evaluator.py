"""Deterministic evaluator for W7 relative-value research scenarios."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .basis import (
    basis_bps,
    convergence_direction as basis_direction,
    directional_convergence_bps,
)
from .contracts import (
    BasisEvaluation,
    CandidateStatus,
    PairEvaluation,
    RelativeValueRequest,
    RelativeValueSnapshot,
    RelativeValueStatus,
    canonical_sha256,
)
from .funding import expected_funding_carry_bps, funding_carry_direction
from .hedging import (
    basis_delta_residual,
    basis_rebalance_diagnostics,
    beta_residual,
    is_basis_delta_neutral,
    is_beta_neutral,
    rebalance_diagnostics,
    target_hedge_ratio,
)
from .pairs import convergence_gross_bps as pair_gross
from .pairs import relative_value_direction, relative_value_spread_bps


def _basis_pit_errors(request: RelativeValueRequest, index: int) -> tuple[str, ...]:
    scenario = request.basis_scenarios[index]
    errors: list[str] = []
    for prefix, observation in (("spot", scenario.spot), ("perp", scenario.perp)):
        errors.extend(
            f"{prefix}:{item}"
            for item in observation.point_in_time_errors(request.decision_time_utc)
        )
    if scenario.funding is not None:
        errors.extend(
            f"funding:{item}"
            for item in scenario.funding.point_in_time_errors(request.decision_time_utc)
        )
    return tuple(sorted(set(errors)))


def _pair_pit_errors(request: RelativeValueRequest, index: int) -> tuple[str, ...]:
    scenario = request.pair_scenarios[index]
    errors: list[str] = []
    observations = (
        ("leg_a_anchor", scenario.leg_a_anchor),
        ("leg_a_current", scenario.leg_a_current),
        ("leg_b_anchor", scenario.leg_b_anchor),
        ("leg_b_current", scenario.leg_b_current),
    )
    for prefix, observation in observations:
        errors.extend(
            f"{prefix}:{item}"
            for item in observation.point_in_time_errors(request.decision_time_utc)
        )
    return tuple(sorted(set(errors)))


def evaluate_basis(request: RelativeValueRequest, index: int) -> BasisEvaluation:
    scenario = request.basis_scenarios[index]
    pit_errors = _basis_pit_errors(request, index)
    delta_residual = basis_delta_residual(scenario.hedge_ratio)
    delta_neutral = is_basis_delta_neutral(scenario)
    rebalance_state, hedge_drift = basis_rebalance_diagnostics(scenario)
    if pit_errors:
        return BasisEvaluation(
            scenario_id=scenario.scenario_id,
            strategy_id=scenario.strategy_id,
            research_objective=scenario.research_objective,
            status=CandidateStatus.BLOCKED,
            reason="invalid_point_in_time_basis_input",
            direction=None,
            basis_bps=None,
            convergence_gross_bps=None,
            expected_funding_carry_bps=None,
            round_trip_cost_bps=None,
            net_scenario_edge_bps=None,
            hedge_ratio=scenario.hedge_ratio,
            delta_residual=delta_residual,
            delta_neutral=False,
            rebalance_state=rebalance_state,
            hedge_ratio_drift=hedge_drift,
            point_in_time_valid=False,
            point_in_time_errors=pit_errors,
        )

    value = basis_bps(scenario.spot.price, scenario.perp.price)
    if scenario.research_objective == "funding_carry":
        if scenario.funding is None:
            raise ValueError("funding_carry_requires_funding_observation")
        direction = funding_carry_direction(scenario.funding.funding_rate)
        if direction is None:
            return BasisEvaluation(
                scenario_id=scenario.scenario_id,
                strategy_id=scenario.strategy_id,
                research_objective=scenario.research_objective,
                status=CandidateStatus.NOT_EVALUABLE,
                reason="zero_funding_carry_not_evaluable",
                direction=None,
                basis_bps=value,
                convergence_gross_bps=0.0,
                expected_funding_carry_bps=0.0,
                round_trip_cost_bps=None,
                net_scenario_edge_bps=None,
                hedge_ratio=scenario.hedge_ratio,
                delta_residual=delta_residual,
                delta_neutral=delta_neutral,
                rebalance_state=rebalance_state,
                hedge_ratio_drift=hedge_drift,
                point_in_time_valid=True,
                point_in_time_errors=(),
            )
    else:
        direction = basis_direction(value)

    perp_side: Literal["long", "short"] = (
        "short" if direction == "LONG_SPOT_SHORT_PERP" else "long"
    )
    funding = expected_funding_carry_bps(
        scenario.funding,
        perp_side=perp_side,
        decision_time_utc=request.decision_time_utc,
        holding_hours=scenario.holding_hours,
        interval_hours=scenario.funding_interval_hours,
    ) * scenario.hedge_ratio
    gross = directional_convergence_bps(
        basis_value_bps=value,
        trade_direction=direction,
        capture_fraction=scenario.convergence_capture_fraction,
    )
    costs = scenario.cost_model.weighted_round_trip_bps(
        leg_a_weight=1.0,
        leg_b_weight=scenario.hedge_ratio,
    )
    net = gross + funding - costs
    status = (
        CandidateStatus.RESEARCH_EVALUATED
        if delta_neutral
        else CandidateStatus.NOT_EVALUABLE
    )
    reason = (
        "scenario_accounting_only_edge_not_proven"
        if delta_neutral
        else "delta_neutrality_gate_failed"
    )
    return BasisEvaluation(
        scenario_id=scenario.scenario_id,
        strategy_id=scenario.strategy_id,
        research_objective=scenario.research_objective,
        status=status,
        reason=reason,
        direction=direction,
        basis_bps=value,
        convergence_gross_bps=gross,
        expected_funding_carry_bps=funding,
        round_trip_cost_bps=costs,
        net_scenario_edge_bps=net if delta_neutral else None,
        hedge_ratio=scenario.hedge_ratio,
        delta_residual=delta_residual,
        delta_neutral=delta_neutral,
        rebalance_state=rebalance_state,
        hedge_ratio_drift=hedge_drift,
        point_in_time_valid=True,
        point_in_time_errors=(),
    )


def evaluate_pair(request: RelativeValueRequest, index: int) -> PairEvaluation:
    scenario = request.pair_scenarios[index]
    pit_errors = _pair_pit_errors(request, index)
    target = target_hedge_ratio(scenario.beta_a_to_b)
    residual = beta_residual(scenario.beta_a_to_b, scenario.hedge_ratio)
    neutral = is_beta_neutral(scenario)
    rebalance_state, drift = rebalance_diagnostics(scenario)
    if pit_errors:
        return PairEvaluation(
            scenario_id=scenario.scenario_id,
            strategy_id=scenario.strategy_id,
            status=CandidateStatus.BLOCKED,
            reason="invalid_point_in_time_pair_input",
            direction=None,
            spread_bps=None,
            convergence_gross_bps=None,
            beta_a_to_b=scenario.beta_a_to_b,
            hedge_ratio=scenario.hedge_ratio,
            beta_residual=residual,
            beta_neutral=False,
            target_hedge_ratio=target,
            rebalance_state=rebalance_state,
            hedge_ratio_drift=drift,
            round_trip_cost_bps=None,
            net_scenario_edge_bps=None,
            point_in_time_valid=False,
            point_in_time_errors=pit_errors,
        )

    spread = relative_value_spread_bps(scenario)
    gross = pair_gross(scenario)
    costs = scenario.cost_model.weighted_round_trip_bps(
        leg_a_weight=1.0,
        leg_b_weight=scenario.hedge_ratio,
    )
    net = gross - costs
    if not neutral:
        status = CandidateStatus.NOT_EVALUABLE
        reason = "beta_neutrality_gate_failed"
        net_value: float | None = None
    else:
        status = CandidateStatus.RESEARCH_EVALUATED
        reason = "scenario_accounting_only_edge_not_proven"
        net_value = net
    return PairEvaluation(
        scenario_id=scenario.scenario_id,
        strategy_id=scenario.strategy_id,
        status=status,
        reason=reason,
        direction=relative_value_direction(spread),
        spread_bps=spread,
        convergence_gross_bps=gross,
        beta_a_to_b=scenario.beta_a_to_b,
        hedge_ratio=scenario.hedge_ratio,
        beta_residual=residual,
        beta_neutral=neutral,
        target_hedge_ratio=target,
        rebalance_state=rebalance_state,
        hedge_ratio_drift=drift,
        round_trip_cost_bps=costs,
        net_scenario_edge_bps=net_value,
        point_in_time_valid=True,
        point_in_time_errors=(),
    )


def build_snapshot(
    request: RelativeValueRequest,
    *,
    created_at_utc: datetime | None = None,
) -> RelativeValueSnapshot:
    created = request.decision_time_utc if created_at_utc is None else created_at_utc
    basis_results = tuple(
        evaluate_basis(request, idx) for idx in range(len(request.basis_scenarios))
    )
    pair_results = tuple(
        evaluate_pair(request, idx) for idx in range(len(request.pair_scenarios))
    )
    all_results = (*basis_results, *pair_results)
    blocked = sum(result.status == CandidateStatus.BLOCKED for result in all_results)
    evaluated = sum(
        result.status == CandidateStatus.RESEARCH_EVALUATED for result in all_results
    )
    positive = sum(
        result.net_scenario_edge_bps is not None and result.net_scenario_edge_bps > 0
        for result in all_results
    )
    if blocked == len(all_results):
        status = RelativeValueStatus.BLOCKED
        reason = "all_relative_value_scenarios_blocked"
    elif blocked > 0 or evaluated < len(all_results):
        status = RelativeValueStatus.PARTIAL
        reason = "relative_value_ready_with_blocked_or_non_neutral_scenarios"
    else:
        status = RelativeValueStatus.SUCCESS
        reason = "relative_value_research_snapshot_ready"

    semantic = {
        "request": request.model_dump(mode="json"),
        "basis": [item.model_dump(mode="json") for item in basis_results],
        "pairs": [item.model_dump(mode="json") for item in pair_results],
    }
    snapshot_id = f"relative-value-{canonical_sha256(semantic)}"
    return RelativeValueSnapshot(
        snapshot_id=snapshot_id,
        request_id=request.request_id,
        decision_time_utc=request.decision_time_utc,
        created_at_utc=created,
        status=status,
        reason=reason,
        basis_evaluations=basis_results,
        pair_evaluations=pair_results,
        evaluated_count=evaluated,
        blocked_count=blocked,
        edge_positive_count=positive,
        edge_proven=False,
        safety=request.safety,
    )
