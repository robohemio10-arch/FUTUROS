"""Treasury reserve simulator kept strictly separate from alpha PnL."""

from __future__ import annotations

from .contracts import TreasuryScenario, TreasurySimulation


def simulate_treasury_reserve(scenario: TreasuryScenario) -> TreasurySimulation:
    strategy_before_transfer = scenario.strategy_start_equity_usdt + scenario.strategy_pnl_usdt
    shortfall = max(0.0, scenario.liquidity_floor_usdt - strategy_before_transfer)
    transferable_reserve = max(
        0.0,
        scenario.reserve_start_usdt - scenario.min_reserve_remaining_usdt,
    )
    transfer = min(shortfall, scenario.max_transfer_usdt, transferable_reserve)
    strategy_after_transfer = strategy_before_transfer + transfer
    reserve_after_transfer = scenario.reserve_start_usdt - transfer
    total_equity = strategy_after_transfer + reserve_after_transfer
    reason = (
        "treasury_transfer_simulated_separate_from_alpha_pnl"
        if transfer > 0.0
        else "treasury_transfer_not_required_or_not_available"
    )
    return TreasurySimulation(
        strategy_equity_before_transfer_usdt=strategy_before_transfer,
        reserve_before_transfer_usdt=scenario.reserve_start_usdt,
        reserve_transfer_usdt=transfer,
        strategy_equity_after_transfer_usdt=strategy_after_transfer,
        reserve_after_transfer_usdt=reserve_after_transfer,
        total_economic_equity_usdt=total_equity,
        strategy_pnl_for_performance_usdt=scenario.strategy_pnl_usdt,
        reason=reason,
    )
