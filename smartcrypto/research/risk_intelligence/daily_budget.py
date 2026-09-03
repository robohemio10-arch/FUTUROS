"""Daily risk budget calibrated by stress, Monte Carlo and CVaR."""

from __future__ import annotations

from .circuit_breaker import evaluate_circuit_breaker
from .contracts import (
    CircuitDecision,
    DailyBudgetDecision,
    ProtectionState,
    RiskIntelligenceRequest,
    StressReport,
)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _state_multiplier(request: RiskIntelligenceRequest, state: ProtectionState) -> float:
    config = request.budget
    if state == ProtectionState.NORMAL:
        return 1.0
    if state == ProtectionState.CONSERVATIVE:
        return config.conservative_multiplier
    if state == ProtectionState.PROTECTION:
        return config.protection_multiplier
    if state == ProtectionState.REDUCE_ONLY:
        return config.reduce_only_multiplier
    return 0.0


def evaluate_daily_budget(
    request: RiskIntelligenceRequest,
    stress_report: StressReport,
) -> tuple[DailyBudgetDecision, CircuitDecision]:
    if stress_report.worst_cvar_bps is None or stress_report.worst_p95_drawdown_bps is None:
        raise ValueError("stress_metrics_required_for_daily_budget")
    circuit = evaluate_circuit_breaker(request, stress_report)
    cvar_magnitude = max(abs(stress_report.worst_cvar_bps), 1e-9)
    drawdown_magnitude = max(stress_report.worst_p95_drawdown_bps, 1e-9)
    cvar_multiplier = _clamp(request.budget.target_cvar_bps / cvar_magnitude, 0.0, 1.0)
    drawdown_multiplier = _clamp(
        request.budget.target_mc_p95_drawdown_bps / drawdown_magnitude,
        0.0,
        1.0,
    )
    stress_multiplier = min(cvar_multiplier, drawdown_multiplier)
    state_multiplier = _state_multiplier(request, circuit.next_state)
    calibrated_budget = request.budget.base_budget_bps * stress_multiplier * state_multiplier
    used_loss_budget = max(0.0, -request.current_risk.day_pnl_bps)
    remaining_budget = max(0.0, calibrated_budget - used_loss_budget)
    reduce_only = circuit.next_state in {
        ProtectionState.REDUCE_ONLY,
        ProtectionState.PAUSED,
        ProtectionState.PANIC,
    }
    new_risk_allowed = (
        not reduce_only
        and calibrated_budget > 0.0
        and remaining_budget > 0.0
    )
    decision = DailyBudgetDecision(
        protection_state=circuit.next_state,
        base_budget_bps=request.budget.base_budget_bps,
        stress_multiplier=stress_multiplier,
        state_multiplier=state_multiplier,
        calibrated_budget_bps=calibrated_budget,
        remaining_daily_budget_bps=remaining_budget,
        new_risk_allowed=new_risk_allowed,
        reduce_only=reduce_only,
    )
    return decision, circuit
