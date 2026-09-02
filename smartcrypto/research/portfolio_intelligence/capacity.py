"""Pure capacity and concentration checks for the shadow allocator."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .contracts import PortfolioAllocatorConfig


@dataclass(frozen=True)
class VirtualPortfolioState:
    symbols: tuple[str, ...]
    capital_by_symbol: dict[str, float]
    total_capital_usdt: float
    position_count: int


def build_virtual_state(
    symbols_and_capital: list[tuple[str, float]],
) -> VirtualPortfolioState:
    capital_by_symbol: dict[str, float] = {}
    for symbol, capital in symbols_and_capital:
        capital_by_symbol[symbol] = capital_by_symbol.get(symbol, 0.0) + capital
    return VirtualPortfolioState(
        symbols=tuple(symbol for symbol, _ in symbols_and_capital),
        capital_by_symbol=capital_by_symbol,
        total_capital_usdt=sum(capital for _, capital in symbols_and_capital),
        position_count=len(symbols_and_capital),
    )


def capacity_reasons(
    *,
    state: VirtualPortfolioState,
    candidate_symbol: str,
    candidate_capital_usdt: float,
    config: PortfolioAllocatorConfig,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if state.position_count >= config.max_positions:
        reasons.append("global_position_capacity_full")
    counts = Counter(state.symbols)
    if counts[candidate_symbol] >= config.max_positions_per_symbol:
        reasons.append("symbol_position_capacity_full")
    projected_total = state.total_capital_usdt + candidate_capital_usdt
    if projected_total > config.shadow_capital_budget_usdt + 1e-12:
        reasons.append("shadow_capital_budget_exceeded")
    projected_symbol = state.capital_by_symbol.get(candidate_symbol, 0.0) + candidate_capital_usdt
    if projected_total > 0:
        fraction = projected_symbol / config.shadow_capital_budget_usdt
        if fraction > config.max_symbol_concentration_fraction + 1e-12:
            reasons.append("symbol_concentration_exceeded")
    return tuple(reasons)
