from __future__ import annotations

from datetime import datetime, timedelta, timezone

from smartcrypto.research.risk_intelligence import (
    CurrentRiskObservation,
    HistoricalReturnObservation,
    ProtectionState,
    RiskIntelligenceRequest,
    StressConfig,
    build_stress_report,
)
from smartcrypto.research.risk_intelligence.circuit_breaker import evaluate_circuit_breaker

_HASH = "b" * 64
_NOW = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


def _history() -> tuple[HistoricalReturnObservation, ...]:
    return tuple(
        HistoricalReturnObservation(
            observation_id=f"h-{index}",
            event_time_utc=_NOW - timedelta(minutes=200 - index),
            available_at_utc=_NOW - timedelta(minutes=200 - index) + timedelta(seconds=1),
            net_return_bps=(12.0 if index % 3 else -20.0),
            source_hash=_HASH,
        )
        for index in range(40)
    )


def _request(
    *,
    state: ProtectionState,
    day_pnl_bps: float,
    drawdown_bps: float,
    exposure: float,
    concentration: float,
) -> RiskIntelligenceRequest:
    return RiskIntelligenceRequest(
        request_id="circuit-test",
        decision_time_utc=_NOW,
        current_state=state,
        historical_returns=_history(),
        current_risk=CurrentRiskObservation(
            available_at_utc=_NOW,
            day_pnl_bps=day_pnl_bps,
            current_drawdown_bps=drawdown_bps,
            gross_exposure_ratio=exposure,
            concentration_ratio=concentration,
            open_positions=3,
            source_hash=_HASH,
        ),
        stress=StressConfig(simulation_count=200, seed=11),
    )


def test_circuit_escalates_immediately_to_required_severity() -> None:
    request = _request(
        state=ProtectionState.NORMAL,
        day_pnl_bps=-500.0,
        drawdown_bps=800.0,
        exposure=2.0,
        concentration=0.95,
    )
    stress = build_stress_report(request)
    decision = evaluate_circuit_breaker(request, stress)

    assert decision.escalation is True
    assert decision.next_state in {ProtectionState.PAUSED, ProtectionState.PANIC}
    assert decision.risk_pressure >= request.circuit.paused_pressure


def test_circuit_deescalates_only_one_step_after_recovery() -> None:
    request = _request(
        state=ProtectionState.PAUSED,
        day_pnl_bps=0.0,
        drawdown_bps=0.0,
        exposure=0.0,
        concentration=0.0,
    )
    stress = build_stress_report(request)
    decision = evaluate_circuit_breaker(request, stress)

    assert decision.required_state == ProtectionState.NORMAL
    assert decision.deescalation is True
    assert decision.next_state == ProtectionState.REDUCE_ONLY
    assert decision.deescalation_limited_to_one_step is True
