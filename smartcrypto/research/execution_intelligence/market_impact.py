"""Market-impact and execution-cost accounting helpers for W8."""

from __future__ import annotations

from .contracts import ExecutionCostModel, LiquidityRole


def fee_bps_for_role(role: LiquidityRole, cost_model: ExecutionCostModel) -> float:
    return (
        cost_model.maker_fee_bps
        if role == LiquidityRole.MAKER
        else cost_model.taker_fee_bps
    )


def latency_cost_bps(latency_ms: float, cost_model: ExecutionCostModel) -> float:
    if latency_ms < 0:
        raise ValueError("latency_ms_must_be_non_negative")
    return latency_ms / 1_000.0 * cost_model.latency_penalty_bps_per_second


def total_execution_cost_bps(
    *,
    slippage_bps: float,
    fee_bps: float,
    latency_cost: float,
) -> float:
    values = (slippage_bps, fee_bps, latency_cost)
    if any(value < 0 for value in values):
        raise ValueError("execution_cost_component_negative")
    return sum(values)
