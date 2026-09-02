from __future__ import annotations

from datetime import datetime, timezone

import pytest

from smartcrypto.research.relative_value import (
    BasisScenario,
    CandidateStatus,
    CostModel,
    FundingObservation,
    PairRelativeValueScenario,
    PriceObservation,
    RelativeValueRequest,
    RelativeValueStatus,
    build_snapshot,
)

UTC = timezone.utc
HASH_A = "a" * 64
HASH_B = "b" * 64


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 28, hour, minute, tzinfo=UTC)


def price(obs_id: str, symbol: str, market: str, value: float, at: datetime, available: datetime | None = None) -> PriceObservation:
    return PriceObservation(
        observation_id=obs_id,
        source_id="fixture",
        exchange="binance",
        symbol=symbol,
        market_type=market,
        price=value,
        event_time_utc=at,
        available_at_utc=at if available is None else available,
        source_hash=HASH_A,
    )


def costs() -> CostModel:
    return CostModel(
        entry_cost_bps_per_leg=1.0,
        exit_cost_bps_per_leg=1.0,
        slippage_bps_per_leg_round_trip=0.5,
        leg_risk_penalty_bps=1.0,
    )


def request(*, future_pair: bool = False, non_neutral: bool = False) -> RelativeValueRequest:
    funding = FundingObservation(
        observation_id="funding-1",
        source_id="fixture",
        exchange="binance",
        symbol="BTCUSDT",
        funding_rate=0.0002,
        rate_kind="predicted",
        funding_time_utc=dt(20),
        observed_at_utc=dt(18),
        available_at_utc=dt(18),
        source_hash=HASH_B,
    )
    basis = BasisScenario(
        scenario_id="basis-btc",
        strategy_id="spot-perp-basis-v1",
        spot=price("btc-spot", "BTCUSDT", "spot", 100.0, dt(18)),
        perp=price("btc-perp", "BTCUSDT", "perp", 101.0, dt(18)),
        funding=funding,
        holding_hours=8,
        convergence_capture_fraction=0.5,
        cost_model=costs(),
    )
    current_time = dt(18, 30)
    available = dt(19) if future_pair else current_time
    pair = PairRelativeValueScenario(
        scenario_id="pair-btc-eth",
        strategy_id="btc-eth-relative-value-v1",
        leg_a_anchor=price("btc0", "BTCUSDT", "perp", 100.0, dt(17)),
        leg_a_current=price("btc1", "BTCUSDT", "perp", 103.0, current_time, available),
        leg_b_anchor=price("eth0", "ETHUSDT", "perp", 100.0, dt(17)),
        leg_b_current=price("eth1", "ETHUSDT", "perp", 101.0, current_time),
        beta_a_to_b=1.2,
        hedge_ratio=0.8 if non_neutral else 1.2,
        max_beta_residual=0.05,
        rebalance_tolerance=0.1,
        prior_hedge_ratio=1.1,
        convergence_capture_fraction=0.4,
        cost_model=costs(),
    )
    return RelativeValueRequest(
        request_id="w7-fixture",
        decision_time_utc=dt(18, 45),
        basis_scenarios=(basis,),
        pair_scenarios=(pair,),
    )


def test_snapshot_deterministic_and_research_only() -> None:
    first = build_snapshot(request())
    second = build_snapshot(request())
    assert first.snapshot_id == second.snapshot_id
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.status == RelativeValueStatus.SUCCESS
    assert first.edge_proven is False
    assert first.safety.sends_orders is False
    assert first.safety.exchange_private_access is False
    assert first.safety.operational_authority is False


def test_basis_cost_and_funding_are_explicit() -> None:
    snapshot = build_snapshot(request())
    item = snapshot.basis_evaluations[0]
    assert item.status == CandidateStatus.RESEARCH_EVALUATED
    assert item.basis_bps == pytest.approx(100.0)
    assert item.expected_funding_carry_bps == pytest.approx(2.0)
    assert item.round_trip_cost_bps == pytest.approx(6.0)
    assert item.net_scenario_edge_bps == pytest.approx(46.0)
    assert item.delta_neutral is True
    assert item.delta_residual == pytest.approx(0.0)


def test_non_neutral_pair_not_evaluable() -> None:
    snapshot = build_snapshot(request(non_neutral=True))
    item = snapshot.pair_evaluations[0]
    assert snapshot.status == RelativeValueStatus.PARTIAL
    assert item.status == CandidateStatus.NOT_EVALUABLE
    assert item.reason == "beta_neutrality_gate_failed"
    assert item.net_scenario_edge_bps is None
    assert item.beta_neutral is False


def test_future_pair_input_blocked_without_using_future_value() -> None:
    snapshot = build_snapshot(request(future_pair=True))
    item = snapshot.pair_evaluations[0]
    assert snapshot.status == RelativeValueStatus.PARTIAL
    assert item.status == CandidateStatus.BLOCKED
    assert item.spread_bps is None
    assert item.net_scenario_edge_bps is None
    assert item.point_in_time_valid is False
    assert any("available_after_decision" in error for error in item.point_in_time_errors)


def test_basis_delta_neutrality_gate() -> None:
    base = request()
    scenario = base.basis_scenarios[0].model_copy(update={"hedge_ratio": 1.3, "max_delta_residual": 0.05})
    gated = base.model_copy(update={"basis_scenarios": (scenario,)})
    snapshot = build_snapshot(gated)
    item = snapshot.basis_evaluations[0]
    assert snapshot.status == RelativeValueStatus.PARTIAL
    assert item.status == CandidateStatus.NOT_EVALUABLE
    assert item.reason == "delta_neutrality_gate_failed"
    assert item.net_scenario_edge_bps is None


def test_funding_carry_objective_uses_funding_direction() -> None:
    base = request()
    scenario = base.basis_scenarios[0].model_copy(update={"research_objective": "funding_carry"})
    funding_request = base.model_copy(update={"basis_scenarios": (scenario,), "pair_scenarios": ()})
    snapshot = build_snapshot(funding_request)
    item = snapshot.basis_evaluations[0]
    assert snapshot.status == RelativeValueStatus.SUCCESS
    assert item.research_objective == "funding_carry"
    assert item.direction == "LONG_SPOT_SHORT_PERP"
    assert item.expected_funding_carry_bps == pytest.approx(2.0)
