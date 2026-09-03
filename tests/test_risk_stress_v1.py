from __future__ import annotations

from datetime import datetime, timedelta, timezone

from smartcrypto.research.risk_intelligence import (
    CurrentRiskObservation,
    HistoricalReturnObservation,
    RiskIntelligenceRequest,
    SnapshotStatus,
    StressConfig,
    build_stress_report,
)

_HASH = "a" * 64
_BASE = datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc)


def _returns(count: int = 40, *, future: bool = False) -> tuple[HistoricalReturnObservation, ...]:
    rows: list[HistoricalReturnObservation] = []
    pattern = (25.0, -15.0, 18.0, -10.0, 12.0, -8.0, 20.0, -12.0)
    for index in range(count):
        event_time = _BASE - timedelta(minutes=(count - index) * 5)
        available = event_time + timedelta(seconds=2)
        if future and index == count - 1:
            available = _BASE + timedelta(minutes=1)
        rows.append(
            HistoricalReturnObservation(
                observation_id=f"ret-{index}",
                event_time_utc=event_time,
                available_at_utc=available,
                net_return_bps=pattern[index % len(pattern)],
                source_hash=_HASH,
            )
        )
    return tuple(rows)


def _request(*, future: bool = False) -> RiskIntelligenceRequest:
    return RiskIntelligenceRequest(
        request_id="stress-test",
        decision_time_utc=_BASE,
        historical_returns=_returns(future=future),
        current_risk=CurrentRiskObservation(
            available_at_utc=_BASE,
            day_pnl_bps=-10.0,
            current_drawdown_bps=25.0,
            gross_exposure_ratio=0.40,
            concentration_ratio=0.20,
            open_positions=2,
            source_hash=_HASH,
        ),
        stress=StressConfig(simulation_count=200, seed=7),
    )


def test_stress_report_is_deterministic_and_contains_mc_cvar() -> None:
    first = build_stress_report(_request())
    second = build_stress_report(_request())

    assert first == second
    assert first.status == SnapshotStatus.READY
    assert first.point_in_time_valid is True
    assert len(first.scenario_metrics) == 6
    assert first.worst_cvar_bps is not None
    assert first.worst_cvar_bps < 0
    assert first.worst_p95_drawdown_bps is not None
    assert first.worst_p95_drawdown_bps >= 0
    assert first.max_ruin_probability is not None


def test_future_return_observation_blocks_stress_analysis() -> None:
    report = build_stress_report(_request(future=True))

    assert report.status == SnapshotStatus.BLOCKED
    assert report.point_in_time_valid is False
    assert report.future_observation_count == 1
    assert report.scenario_metrics == ()
