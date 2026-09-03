"""Deterministic Monte Carlo and CVaR stress analysis for W9 research."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

from .contracts import (
    HistoricalReturnObservation,
    RiskIntelligenceRequest,
    SnapshotStatus,
    StressReport,
    StressScenarioMetrics,
)

_SCENARIOS = (
    "baseline",
    "fee_slippage_stress",
    "loss_cluster_stress",
    "fat_tail_stress",
    "low_liquidity_stress",
    "combined_adverse_stress",
)


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("quantile_requires_non_empty_values")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _lower_tail_var_cvar(values: Sequence[float], confidence: float) -> tuple[float, float]:
    tail_q = 1.0 - confidence
    var_value = _quantile(values, tail_q)
    tail = [float(value) for value in values if float(value) <= var_value]
    cvar_value = sum(tail) / len(tail) if tail else var_value
    return var_value, cvar_value


def _apply_scenario(
    returns_bps: Sequence[float],
    scenario: str,
    request: RiskIntelligenceRequest,
) -> list[float]:
    config = request.stress
    values = [float(value) for value in returns_bps]
    if scenario == "baseline":
        return values
    if scenario == "fee_slippage_stress":
        return [value - config.fee_slippage_stress_bps for value in values]
    if scenario == "loss_cluster_stress":
        return sorted(values, key=lambda value: (value >= 0.0, value))
    if scenario == "fat_tail_stress":
        return [
            value * config.fat_tail_loss_multiplier
            if value < 0.0
            else value * config.fat_tail_gain_multiplier
            for value in values
        ]
    if scenario == "low_liquidity_stress":
        return [
            (
                value * config.low_liquidity_loss_multiplier
                if value < 0.0
                else value * config.low_liquidity_gain_multiplier
            )
            - config.fee_slippage_stress_bps
            for value in values
        ]
    if scenario == "combined_adverse_stress":
        stressed = [
            value * (config.fat_tail_loss_multiplier * 1.15)
            if value < 0.0
            else value * (config.low_liquidity_gain_multiplier * 0.90)
            for value in values
        ]
        return sorted(
            (
                value - (config.fee_slippage_stress_bps * 1.5)
                for value in stressed
            ),
            key=lambda value: (value >= 0.0, value),
        )
    raise ValueError(f"unknown_stress_scenario:{scenario}")


def _path_metrics(
    returns_bps: Sequence[float],
    *,
    seed: int,
    simulation_count: int,
    horizon: int,
    ruin_equity_ratio: float,
    confidence: float,
) -> tuple[float, float, float, float]:
    rng = random.Random(seed)
    terminal_returns_bps: list[float] = []
    max_drawdowns_bps: list[float] = []
    loss_count = 0
    ruin_count = 0

    for _ in range(simulation_count):
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        ruined = False
        for _step in range(horizon):
            sampled_bps = float(returns_bps[rng.randrange(len(returns_bps))])
            factor = max(0.0, 1.0 + sampled_bps / 10_000.0)
            equity *= factor
            if equity > peak:
                peak = equity
            if peak > 0.0:
                drawdown = (peak - equity) / peak * 10_000.0
                max_drawdown = max(max_drawdown, drawdown)
            if equity <= ruin_equity_ratio:
                ruined = True
        terminal_bps = (equity - 1.0) * 10_000.0
        terminal_returns_bps.append(terminal_bps)
        max_drawdowns_bps.append(max_drawdown)
        if terminal_bps < 0.0:
            loss_count += 1
        if ruined:
            ruin_count += 1

    p95_drawdown = _quantile(max_drawdowns_bps, confidence)
    loss_probability = loss_count / simulation_count
    ruin_probability = ruin_count / simulation_count
    _terminal_var, terminal_cvar = _lower_tail_var_cvar(
        terminal_returns_bps,
        confidence,
    )
    return p95_drawdown, loss_probability, ruin_probability, terminal_cvar


def _causal_returns(
    request: RiskIntelligenceRequest,
) -> tuple[list[HistoricalReturnObservation], int]:
    causal = [
        item
        for item in request.historical_returns
        if item.available_at_utc <= request.decision_time_utc
    ]
    future_count = len(request.historical_returns) - len(causal)
    return causal, future_count


def build_stress_report(request: RiskIntelligenceRequest) -> StressReport:
    causal, future_count = _causal_returns(request)
    if future_count > 0:
        return StressReport(
            status=SnapshotStatus.BLOCKED,
            reason="future_risk_observation_detected",
            point_in_time_valid=False,
            valid_observation_count=len(causal),
            future_observation_count=future_count,
            scenario_metrics=(),
            worst_scenario=None,
            worst_cvar_bps=None,
            worst_p95_drawdown_bps=None,
            max_ruin_probability=None,
        )
    if request.current_risk.available_at_utc > request.decision_time_utc:
        return StressReport(
            status=SnapshotStatus.BLOCKED,
            reason="future_current_risk_observation_detected",
            point_in_time_valid=False,
            valid_observation_count=len(causal),
            future_observation_count=1,
            scenario_metrics=(),
            worst_scenario=None,
            worst_cvar_bps=None,
            worst_p95_drawdown_bps=None,
            max_ruin_probability=None,
        )
    if len(causal) < request.budget.min_history_observations:
        return StressReport(
            status=SnapshotStatus.BLOCKED,
            reason="insufficient_history_for_calibrated_risk_budget",
            point_in_time_valid=True,
            valid_observation_count=len(causal),
            future_observation_count=0,
            scenario_metrics=(),
            worst_scenario=None,
            worst_cvar_bps=None,
            worst_p95_drawdown_bps=None,
            max_ruin_probability=None,
        )

    raw_returns = [item.net_return_bps for item in causal]
    horizon = request.stress.horizon_observations or len(raw_returns)
    scenario_metrics: list[StressScenarioMetrics] = []
    for index, scenario in enumerate(_SCENARIOS):
        stressed = _apply_scenario(raw_returns, scenario, request)
        empirical_var, empirical_cvar = _lower_tail_var_cvar(
            stressed,
            request.stress.confidence_level,
        )
        p95_drawdown, loss_probability, ruin_probability, terminal_cvar = (
            _path_metrics(
                stressed,
                seed=request.stress.seed + index * 1009,
                simulation_count=request.stress.simulation_count,
                horizon=horizon,
                ruin_equity_ratio=request.stress.ruin_equity_ratio,
                confidence=request.stress.confidence_level,
            )
        )
        scenario_metrics.append(
            StressScenarioMetrics(
                scenario=scenario,
                observation_count=len(stressed),
                empirical_var_bps=empirical_var,
                empirical_cvar_bps=empirical_cvar,
                monte_carlo_p95_max_drawdown_bps=p95_drawdown,
                monte_carlo_loss_probability=loss_probability,
                monte_carlo_ruin_probability=ruin_probability,
                monte_carlo_terminal_cvar_bps=terminal_cvar,
            )
        )

    worst = min(
        scenario_metrics,
        key=lambda item: (
            item.empirical_cvar_bps,
            item.monte_carlo_terminal_cvar_bps,
            -item.monte_carlo_p95_max_drawdown_bps,
        ),
    )
    worst_p95 = max(item.monte_carlo_p95_max_drawdown_bps for item in scenario_metrics)
    max_ruin = max(item.monte_carlo_ruin_probability for item in scenario_metrics)
    return StressReport(
        status=SnapshotStatus.READY,
        reason="stress_mc_cvar_research_snapshot_ready",
        point_in_time_valid=True,
        valid_observation_count=len(causal),
        future_observation_count=0,
        scenario_metrics=tuple(scenario_metrics),
        worst_scenario=worst.scenario,
        worst_cvar_bps=worst.empirical_cvar_bps,
        worst_p95_drawdown_bps=worst_p95,
        max_ruin_probability=max_ruin,
    )
