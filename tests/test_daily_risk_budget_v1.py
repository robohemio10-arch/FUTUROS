from __future__ import annotations

from datetime import datetime, timedelta, timezone

from smartcrypto.research.risk_intelligence import (
    CurrentRiskObservation,
    HistoricalReturnObservation,
    ProtectionState,
    RiskIntelligenceRequest,
    StressConfig,
    build_snapshot,
)

_HASH = "c" * 64
_NOW = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


def _request(day_pnl_bps: float = -5.0, exposure: float = 0.25) -> RiskIntelligenceRequest:
    history = tuple(
        HistoricalReturnObservation(
            observation_id=f"r-{index}",
            event_time_utc=_NOW - timedelta(minutes=300 - index),
            available_at_utc=_NOW - timedelta(minutes=300 - index) + timedelta(seconds=1),
            net_return_bps=(20.0 if index % 4 else -15.0),
            source_hash=_HASH,
        )
        for index in range(40)
    )
    return RiskIntelligenceRequest(
        request_id="budget-test",
        decision_time_utc=_NOW,
        historical_returns=history,
        current_risk=CurrentRiskObservation(
            available_at_utc=_NOW,
            day_pnl_bps=day_pnl_bps,
            current_drawdown_bps=10.0,
            gross_exposure_ratio=exposure,
            concentration_ratio=0.15,
            open_positions=1,
            source_hash=_HASH,
        ),
        stress=StressConfig(simulation_count=200, seed=13),
    )


def test_budget_is_stress_calibrated_shadow_only_and_deterministic() -> None:
    first = build_snapshot(_request())
    second = build_snapshot(_request())

    assert first == second
    assert first.daily_budget is not None
    assert first.circuit_decision is not None
    assert first.daily_budget.calibration_method == "stress_mc_cvar_v1"
    assert 0.0 <= first.daily_budget.stress_multiplier <= 1.0
    assert first.daily_budget.calibrated_budget_bps <= first.daily_budget.base_budget_bps
    assert first.daily_budget.operationally_applied is False
    assert first.riskmanager_final_authority is True
    assert first.safety.changes_risk is False
    assert first.safety.risk_budget_operationally_applied is False


def test_overexposure_forces_reduce_only_or_stricter() -> None:
    snapshot = build_snapshot(_request(day_pnl_bps=-200.0, exposure=2.0))

    assert snapshot.daily_budget is not None
    assert snapshot.daily_budget.protection_state in {
        ProtectionState.REDUCE_ONLY,
        ProtectionState.PAUSED,
        ProtectionState.PANIC,
    }
    assert snapshot.daily_budget.new_risk_allowed is False
    assert snapshot.daily_budget.reduce_only is True
