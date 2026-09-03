from __future__ import annotations

from smartcrypto.research.risk_intelligence import TreasuryScenario, simulate_treasury_reserve


def test_treasury_transfer_never_changes_reported_strategy_pnl() -> None:
    scenario = TreasuryScenario(
        strategy_start_equity_usdt=1000.0,
        reserve_start_usdt=500.0,
        strategy_pnl_usdt=-200.0,
        liquidity_floor_usdt=900.0,
        max_transfer_usdt=150.0,
        min_reserve_remaining_usdt=300.0,
    )

    result = simulate_treasury_reserve(scenario)

    assert result.strategy_equity_before_transfer_usdt == 800.0
    assert result.reserve_transfer_usdt == 100.0
    assert result.strategy_equity_after_transfer_usdt == 900.0
    assert result.reserve_after_transfer_usdt == 400.0
    assert result.strategy_pnl_for_performance_usdt == -200.0
    assert result.reserve_transfer_included_in_strategy_pnl is False
    assert result.reserve_can_mask_negative_expectancy is False
    assert result.operationally_applied is False


def test_treasury_does_not_transfer_when_floor_is_already_met() -> None:
    scenario = TreasuryScenario(
        strategy_start_equity_usdt=1000.0,
        reserve_start_usdt=500.0,
        strategy_pnl_usdt=25.0,
        liquidity_floor_usdt=900.0,
        max_transfer_usdt=150.0,
        min_reserve_remaining_usdt=300.0,
    )

    result = simulate_treasury_reserve(scenario)

    assert result.reserve_transfer_usdt == 0.0
    assert result.strategy_pnl_for_performance_usdt == 25.0
    assert result.total_economic_equity_usdt == 1525.0
