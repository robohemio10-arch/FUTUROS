from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from smartcrypto.research.relative_value import (
    BasisScenario,
    CostModel,
    FundingObservation,
    PairRelativeValueScenario,
    PriceObservation,
    RebalanceState,
    basis_bps,
    beta_residual,
    expected_funding_carry_bps,
    rebalance_diagnostics,
    relative_value_spread_bps,
)

UTC = timezone.utc
HASH_A = "a" * 64
HASH_B = "b" * 64


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 28, hour, minute, tzinfo=UTC)


def price(obs_id: str, symbol: str, market: str, value: float, at: datetime) -> PriceObservation:
    return PriceObservation(
        observation_id=obs_id,
        source_id="fixture",
        exchange="binance",
        symbol=symbol,
        market_type=market,
        price=value,
        event_time_utc=at,
        available_at_utc=at,
        source_hash=HASH_A,
    )


def costs() -> CostModel:
    return CostModel(
        entry_cost_bps_per_leg=2,
        exit_cost_bps_per_leg=2,
        slippage_bps_per_leg_round_trip=1,
        leg_risk_penalty_bps=2,
    )


def test_basis_and_funding_accounting() -> None:
    assert basis_bps(100.0, 101.0) == pytest.approx(100.0)
    funding = FundingObservation(
        observation_id="funding-1",
        source_id="fixture",
        exchange="binance",
        symbol="BTCUSDT",
        funding_rate=0.0001,
        rate_kind="predicted",
        funding_time_utc=dt(20),
        observed_at_utc=dt(18),
        available_at_utc=dt(18),
        source_hash=HASH_B,
    )
    assert expected_funding_carry_bps(
        funding,
        perp_side="short",
        decision_time_utc=dt(18),
        holding_hours=16,
        interval_hours=8,
    ) == pytest.approx(2.0)
    assert expected_funding_carry_bps(
        funding,
        perp_side="long",
        decision_time_utc=dt(18),
        holding_hours=16,
        interval_hours=8,
    ) == pytest.approx(-2.0)


def test_realized_future_funding_rejected() -> None:
    with pytest.raises(ValidationError):
        FundingObservation(
            observation_id="funding-future",
            source_id="fixture",
            exchange="binance",
            symbol="BTCUSDT",
            funding_rate=0.0001,
            rate_kind="realized",
            funding_time_utc=dt(19),
            observed_at_utc=dt(18),
            available_at_utc=dt(18),
            source_hash=HASH_B,
        )


def test_pair_spread_and_rebalance() -> None:
    scenario = PairRelativeValueScenario(
        scenario_id="btc-eth-1",
        strategy_id="relative-value-btc-eth-v1",
        leg_a_anchor=price("btc0", "BTCUSDT", "perp", 100.0, dt(17)),
        leg_a_current=price("btc1", "BTCUSDT", "perp", 102.0, dt(18)),
        leg_b_anchor=price("eth0", "ETHUSDT", "perp", 100.0, dt(17)),
        leg_b_current=price("eth1", "ETHUSDT", "perp", 101.0, dt(18)),
        beta_a_to_b=1.2,
        hedge_ratio=1.2,
        max_beta_residual=0.05,
        rebalance_tolerance=0.1,
        prior_hedge_ratio=1.0,
        convergence_capture_fraction=0.5,
        cost_model=costs(),
    )
    assert relative_value_spread_bps(scenario) > 0
    assert beta_residual(1.2, 1.2) == pytest.approx(0.0)
    state, drift = rebalance_diagnostics(scenario)
    assert state == RebalanceState.REBALANCE_RESEARCH
    assert drift == pytest.approx(0.2)


def test_basis_symbol_mismatch_rejected() -> None:
    with pytest.raises(ValidationError):
        BasisScenario(
            scenario_id="bad-basis",
            strategy_id="basis-v1",
            spot=price("s", "BTCUSDT", "spot", 100.0, dt(18)),
            perp=price("p", "ETHUSDT", "perp", 101.0, dt(18)),
            holding_hours=8,
            convergence_capture_fraction=0.5,
            cost_model=costs(),
        )
