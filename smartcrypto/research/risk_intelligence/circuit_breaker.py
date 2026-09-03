"""Stress-calibrated shadow protection state machine for W9."""

from __future__ import annotations

from .contracts import (
    CircuitDecision,
    ProtectionState,
    RiskIntelligenceRequest,
    StressReport,
)

_STATE_ORDER = (
    ProtectionState.NORMAL,
    ProtectionState.CONSERVATIVE,
    ProtectionState.PROTECTION,
    ProtectionState.REDUCE_ONLY,
    ProtectionState.PAUSED,
    ProtectionState.PANIC,
)


def _state_for_pressure(request: RiskIntelligenceRequest, pressure: float) -> ProtectionState:
    config = request.circuit
    if pressure >= config.panic_pressure:
        return ProtectionState.PANIC
    if pressure >= config.paused_pressure:
        return ProtectionState.PAUSED
    if pressure >= config.reduce_only_pressure:
        return ProtectionState.REDUCE_ONLY
    if pressure >= config.protection_pressure:
        return ProtectionState.PROTECTION
    if pressure >= config.conservative_pressure:
        return ProtectionState.CONSERVATIVE
    return ProtectionState.NORMAL


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0.0:
        return 0.0 if numerator <= 0.0 else float("inf")
    return numerator / denominator


def calculate_risk_pressure(
    request: RiskIntelligenceRequest,
    stress_report: StressReport,
) -> float:
    if stress_report.worst_cvar_bps is None or stress_report.worst_p95_drawdown_bps is None:
        raise ValueError("stress_metrics_required_for_risk_pressure")
    cvar_scale = max(abs(stress_report.worst_cvar_bps), 1e-9)
    drawdown_scale = max(stress_report.worst_p95_drawdown_bps, 1e-9)
    current = request.current_risk
    components = (
        _safe_ratio(max(0.0, -current.day_pnl_bps), cvar_scale),
        _safe_ratio(current.current_drawdown_bps, drawdown_scale),
        _safe_ratio(current.gross_exposure_ratio, request.budget.max_gross_exposure_ratio),
        _safe_ratio(current.concentration_ratio, request.budget.max_concentration_ratio),
    )
    return max(components)


def evaluate_circuit_breaker(
    request: RiskIntelligenceRequest,
    stress_report: StressReport,
) -> CircuitDecision:
    pressure = calculate_risk_pressure(request, stress_report)
    required_state = _state_for_pressure(request, pressure)
    prior_index = _STATE_ORDER.index(request.current_state)
    required_index = _STATE_ORDER.index(required_state)

    if required_index > prior_index:
        next_state = required_state
        reason = "stress_calibrated_immediate_escalation"
        escalation = True
        deescalation = False
    elif required_index < prior_index and pressure <= request.circuit.recovery_pressure:
        next_state = _STATE_ORDER[max(0, prior_index - 1)]
        reason = "recovery_deescalation_one_step_only"
        escalation = False
        deescalation = True
    else:
        next_state = request.current_state
        reason = "protection_state_held"
        escalation = False
        deescalation = False

    return CircuitDecision(
        prior_state=request.current_state,
        required_state=required_state,
        next_state=next_state,
        risk_pressure=pressure,
        escalation=escalation,
        deescalation=deescalation,
        reason=reason,
    )
