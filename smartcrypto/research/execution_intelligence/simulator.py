"""Deterministic W8 execution-policy simulator and snapshot builder."""

from __future__ import annotations

from .contracts import (
    ExecutionEvaluation,
    ExecutionIntelligenceRequest,
    ExecutionIntelligenceSnapshot,
    ExecutionPolicyName,
    ExecutionScenario,
    ExecutionStatus,
    FillRecord,
    LiquidityRole,
    SafetyContract,
    canonical_sha256,
)
from .fill_model import (
    aggressive_fill_estimate,
    aggressive_limit_price,
    market_slice_available_at,
    passive_fill_capacity,
    passive_limit_price,
    signed_slippage_bps,
    touched_passive_limit,
    weighted_average_price,
)
from .intrabar_exit_lab import evaluate_intrabar_scenario
from .maker_reprice import simulate_maker_reprice
from .market_impact import fee_bps_for_role, latency_cost_bps, total_execution_cost_bps
from .policies import submit_time, timeout_time
from .twap import simulate_twap


def _single_limit(scenario: ExecutionScenario):
    live_time = submit_time(scenario)
    deadline = timeout_time(scenario)
    arrival = market_slice_available_at(scenario.market_path, live_time)
    if arrival is None:
        return (), 0.0, (), None, None, None, float(scenario.submit_latency_ms), True

    limit_price = passive_limit_price(arrival, scenario.side, scenario.limit_offset_bps)
    remaining = scenario.quantity
    fills: list[FillRecord] = []
    used: list[str] = [arrival.slice_id]
    probability_not_filled = 1.0
    latest = arrival.available_at_utc

    for market in scenario.market_path:
        if market.available_at_utc < live_time or market.available_at_utc > deadline:
            continue
        if market.slice_id not in used:
            used.append(market.slice_id)
        latest = max(latest, market.available_at_utc)
        if not touched_passive_limit(market, scenario.side, limit_price):
            continue
        capacity, probability = passive_fill_capacity(
            market,
            scenario.side,
            remaining,
            scenario.participation_cap,
        )
        probability_not_filled *= 1.0 - probability
        if capacity <= 0:
            continue
        fills.append(
            FillRecord(
                fill_id=f"{scenario.scenario_id}/limit-fill/{len(fills) + 1}",
                source_slice_id=market.slice_id,
                fill_time_utc=market.available_at_utc,
                quantity=capacity,
                price=limit_price,
                liquidity_role=LiquidityRole.MAKER,
                fee_bps=fee_bps_for_role(LiquidityRole.MAKER, scenario.cost_model),
                impact_bps=0.0,
                child_index=0,
            )
        )
        remaining = max(remaining - capacity, 0.0)
        if remaining <= 1e-12:
            break

    return (
        tuple(fills),
        min(max(1.0 - probability_not_filled, 0.0), 1.0),
        tuple(used),
        latest,
        arrival.mid_price,
        arrival.spread_bps,
        float(scenario.submit_latency_ms),
        remaining > 1e-12,
    )


def _aggressive_limit(scenario: ExecutionScenario):
    live_time = submit_time(scenario)
    arrival = market_slice_available_at(scenario.market_path, live_time)
    if arrival is None:
        return (), 0.0, (), None, None, None, float(scenario.submit_latency_ms), True
    limit_price = aggressive_limit_price(arrival, scenario.side, scenario.aggressive_limit_bps)
    estimate = aggressive_fill_estimate(
        arrival,
        scenario.side,
        scenario.quantity,
        scenario.participation_cap,
        limit_price,
        scenario.cost_model,
    )
    fills: tuple[FillRecord, ...] = ()
    if estimate.quantity > 0:
        fills = (
            FillRecord(
                fill_id=f"{scenario.scenario_id}/aggressive-fill/1",
                source_slice_id=arrival.slice_id,
                fill_time_utc=live_time,
                quantity=estimate.quantity,
                price=estimate.price,
                liquidity_role=LiquidityRole.TAKER,
                fee_bps=fee_bps_for_role(LiquidityRole.TAKER, scenario.cost_model),
                impact_bps=estimate.impact_bps,
                child_index=0,
            ),
        )
    return (
        fills,
        estimate.fill_probability,
        (arrival.slice_id,),
        arrival.available_at_utc,
        arrival.mid_price,
        arrival.spread_bps,
        float(scenario.submit_latency_ms),
        False,
    )


def _evaluate_execution(scenario: ExecutionScenario) -> ExecutionEvaluation:
    if scenario.simulate_api_timeout:
        latency_ms = float(scenario.submit_latency_ms)
        return ExecutionEvaluation(
            scenario_id=scenario.scenario_id,
            strategy_id=scenario.strategy_id,
            policy=scenario.policy,
            status=ExecutionStatus.BLOCKED,
            reason="simulated_api_timeout_before_acknowledgement",
            requested_quantity=scenario.quantity,
            filled_quantity=0.0,
            fill_rate=0.0,
            fill_probability=0.0,
            partial_fill=False,
            timed_out=True,
            arrival_mid_price=None,
            average_fill_price=None,
            spread_bps=None,
            slippage_bps=None,
            market_impact_bps=None,
            fee_bps=None,
            latency_ms=latency_ms,
            latency_cost_bps=latency_cost_bps(latency_ms, scenario.cost_model),
            total_execution_cost_bps=None,
            reprice_count=0,
            child_order_count=0,
            used_market_slice_ids=(),
            latest_used_available_at_utc=None,
            fills=(),
        )

    reprice_count = 0
    child_order_count = 1
    if scenario.policy == ExecutionPolicyName.SINGLE_LIMIT:
        raw = _single_limit(scenario)
    elif scenario.policy == ExecutionPolicyName.AGGRESSIVE_LIMIT:
        raw = _aggressive_limit(scenario)
    elif scenario.policy == ExecutionPolicyName.MAKER_REPRICE:
        maker_result = simulate_maker_reprice(scenario)
        raw = (
            maker_result.fills,
            maker_result.fill_probability,
            maker_result.used_market_slice_ids,
            maker_result.latest_used_available_at_utc,
            maker_result.arrival_mid_price,
            maker_result.spread_bps,
            maker_result.latency_ms,
            maker_result.timed_out,
        )
        reprice_count = maker_result.reprice_count
        child_order_count = maker_result.child_order_count
    elif scenario.policy == ExecutionPolicyName.TWAP:
        twap_result = simulate_twap(scenario)
        raw = (
            twap_result.fills,
            twap_result.fill_probability,
            twap_result.used_market_slice_ids,
            twap_result.latest_used_available_at_utc,
            twap_result.arrival_mid_price,
            twap_result.spread_bps,
            twap_result.latency_ms,
            twap_result.timed_out,
        )
        child_order_count = twap_result.child_order_count
    else:  # pragma: no cover - Enum validation makes this defensive only.
        raise ValueError("unsupported_execution_policy")

    fills, probability, used_ids, latest, arrival_mid, spread_bps, latency_ms, timed_out = raw
    filled_quantity = sum(item.quantity for item in fills)
    fill_rate = min(max(filled_quantity / scenario.quantity, 0.0), 1.0)
    average_fill = weighted_average_price([(item.quantity, item.price) for item in fills])

    if arrival_mid is None:
        status = ExecutionStatus.BLOCKED
        reason = "no_causal_market_slice_available_at_submit"
        slippage_bps = None
        impact_bps = None
        fee_bps = None
        total_cost = None
    else:
        if fill_rate >= 1.0 - 1e-12:
            status = ExecutionStatus.SUCCESS
            reason = "execution_policy_fully_filled_in_offline_simulation"
        elif filled_quantity > 0:
            status = ExecutionStatus.PARTIAL
            reason = "execution_policy_partial_fill_in_offline_simulation"
        else:
            status = ExecutionStatus.PARTIAL
            reason = "execution_policy_no_fill_before_timeout_or_liquidity_limit"
        slippage_bps = (
            None
            if average_fill is None
            else signed_slippage_bps(scenario.side, arrival_mid, average_fill)
        )
        impact_bps = (
            None
            if not fills
            else sum(item.quantity * item.impact_bps for item in fills) / filled_quantity
        )
        fee_bps = (
            None
            if not fills
            else sum(item.quantity * item.fee_bps for item in fills) / filled_quantity
        )
        latency_cost = latency_cost_bps(latency_ms, scenario.cost_model)
        total_cost = (
            None
            if slippage_bps is None or fee_bps is None
            else total_execution_cost_bps(
                slippage_bps=slippage_bps,
                fee_bps=fee_bps,
                latency_cost=latency_cost,
            )
        )

    return ExecutionEvaluation(
        scenario_id=scenario.scenario_id,
        strategy_id=scenario.strategy_id,
        policy=scenario.policy,
        status=status,
        reason=reason,
        requested_quantity=scenario.quantity,
        filled_quantity=filled_quantity,
        fill_rate=fill_rate,
        fill_probability=probability,
        partial_fill=0 < filled_quantity < scenario.quantity,
        timed_out=timed_out,
        arrival_mid_price=arrival_mid,
        average_fill_price=average_fill,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        market_impact_bps=impact_bps,
        fee_bps=fee_bps,
        latency_ms=latency_ms,
        latency_cost_bps=latency_cost_bps(latency_ms, scenario.cost_model),
        total_execution_cost_bps=total_cost,
        reprice_count=reprice_count,
        child_order_count=child_order_count,
        used_market_slice_ids=used_ids,
        latest_used_available_at_utc=latest,
        fills=fills,
    )


def build_snapshot(request: ExecutionIntelligenceRequest) -> ExecutionIntelligenceSnapshot:
    execution = tuple(_evaluate_execution(item) for item in request.execution_scenarios)
    exit_items = tuple(
        result
        for scenario in request.intrabar_exit_scenarios
        for result in evaluate_intrabar_scenario(scenario)
    )

    blocked = sum(item.status == ExecutionStatus.BLOCKED for item in execution)
    partial = sum(item.status == ExecutionStatus.PARTIAL for item in execution)
    evaluated = len(execution) - blocked
    if execution and blocked == len(execution) and not exit_items:
        status = ExecutionStatus.BLOCKED
        reason = "all_execution_scenarios_blocked"
    elif blocked or partial:
        status = ExecutionStatus.PARTIAL
        reason = "execution_intelligence_ready_with_partial_or_blocked_scenarios"
    else:
        status = ExecutionStatus.SUCCESS
        reason = "execution_intelligence_research_snapshot_ready"

    identity_payload = {
        "request": request.model_dump(mode="json"),
        "execution_evaluations": [item.model_dump(mode="json") for item in execution],
        "intrabar_exit_evaluations": [item.model_dump(mode="json") for item in exit_items],
        "status": status.value,
        "reason": reason,
    }
    snapshot_id = f"execution-intelligence-{canonical_sha256(identity_payload)}"
    return ExecutionIntelligenceSnapshot(
        snapshot_id=snapshot_id,
        request_id=request.request_id,
        decision_time_utc=request.decision_time_utc,
        created_at_utc=request.decision_time_utc,
        status=status,
        reason=reason,
        execution_evaluations=execution,
        intrabar_exit_evaluations=exit_items,
        evaluated_count=evaluated,
        partial_count=partial,
        blocked_count=blocked,
        safety=SafetyContract(),
    )
