"""Causal, conservative intrabar exit laboratory for W8.

The lab evaluates breakeven, trailing, and time-stop policies on 15-second or
1-minute OHLC paths.  Activation learned from a completed bar becomes active
only for the next bar.  This adverse-first rule prevents same-bar high/low
ordering from creating optimistic fills.
"""

from __future__ import annotations

from datetime import datetime

from .contracts import (
    ExitPolicyEvaluation,
    ExitPolicyName,
    ExitStatus,
    IntrabarBar,
    IntrabarExitScenario,
    PositionSide,
)

BPS = 10_000.0


def _gross_return_bps(side: PositionSide, entry: float, exit_price: float) -> float:
    if side == PositionSide.LONG:
        return (exit_price - entry) / entry * BPS
    return (entry - exit_price) / entry * BPS


def _bar_excursions(side: PositionSide, entry: float, bar: IntrabarBar) -> tuple[float, float]:
    if side == PositionSide.LONG:
        favorable = max((bar.high - entry) / entry * BPS, 0.0)
        adverse = max((entry - bar.low) / entry * BPS, 0.0)
    else:
        favorable = max((entry - bar.low) / entry * BPS, 0.0)
        adverse = max((bar.high - entry) / entry * BPS, 0.0)
    return favorable, adverse


def _stop_hit(side: PositionSide, bar: IntrabarBar, stop_price: float) -> bool:
    if side == PositionSide.LONG:
        return bar.low <= stop_price
    return bar.high >= stop_price


def _capital_hours(notional_usdt: float, hold_seconds: float) -> float:
    return notional_usdt * hold_seconds / 3_600.0


def _finalize(
    scenario: IntrabarExitScenario,
    policy: ExitPolicyName,
    *,
    status: ExitStatus,
    reason: str,
    used: list[IntrabarBar],
    exit_time: datetime | None,
    exit_price: float | None,
    activation_time: datetime | None,
    trigger_price: float | None,
    mfe_bps: float,
    mae_bps: float,
) -> ExitPolicyEvaluation:
    if exit_time is not None:
        hold_seconds = max((exit_time - scenario.entry_time_utc).total_seconds(), 0.0)
    elif used:
        hold_seconds = max((used[-1].end_time_utc - scenario.entry_time_utc).total_seconds(), 0.0)
    else:
        hold_seconds = 0.0
    gross = (
        None
        if exit_price is None
        else _gross_return_bps(scenario.side, scenario.entry_price, exit_price)
    )
    net = None if gross is None else gross - scenario.exit_cost_bps
    return ExitPolicyEvaluation(
        scenario_id=scenario.scenario_id,
        strategy_id=scenario.strategy_id,
        policy=policy,
        status=status,
        reason=reason,
        path_granularity=scenario.bars[0].granularity,
        exit_time_utc=exit_time,
        exit_price=exit_price,
        gross_return_bps=gross,
        exit_cost_bps=scenario.exit_cost_bps,
        net_return_bps=net,
        hold_seconds=hold_seconds,
        capital_hours_consumed=_capital_hours(scenario.notional_usdt, hold_seconds),
        mfe_bps=mfe_bps,
        mae_bps=mae_bps,
        activation_time_utc=activation_time,
        trigger_price=trigger_price,
        used_bar_ids=tuple(bar.bar_id for bar in used),
    )


def evaluate_breakeven(scenario: IntrabarExitScenario) -> ExitPolicyEvaluation:
    active = False
    activation_time: datetime | None = None
    stop_price: float | None = None
    used: list[IntrabarBar] = []
    mfe_bps = 0.0
    mae_bps = 0.0

    for bar in scenario.bars:
        if bar.end_time_utc <= scenario.entry_time_utc:
            continue
        used.append(bar)
        favorable, adverse = _bar_excursions(scenario.side, scenario.entry_price, bar)
        mfe_bps = max(mfe_bps, favorable)
        mae_bps = max(mae_bps, adverse)

        if active and stop_price is not None and _stop_hit(scenario.side, bar, stop_price):
            return _finalize(
                scenario,
                ExitPolicyName.BREAKEVEN,
                status=ExitStatus.EXITED,
                reason="breakeven_stop_triggered_conservative_next_bar",
                used=used,
                exit_time=bar.end_time_utc,
                exit_price=stop_price,
                activation_time=activation_time,
                trigger_price=stop_price,
                mfe_bps=mfe_bps,
                mae_bps=mae_bps,
            )

        if not active and favorable >= scenario.breakeven_trigger_bps:
            active = True
            activation_time = bar.available_at_utc
            lock = scenario.breakeven_lock_bps / BPS
            stop_price = (
                scenario.entry_price * (1.0 + lock)
                if scenario.side == PositionSide.LONG
                else scenario.entry_price * (1.0 - lock)
            )

    return _finalize(
        scenario,
        ExitPolicyName.BREAKEVEN,
        status=ExitStatus.OPEN_AT_PATH_END,
        reason="breakeven_not_triggered_or_not_stopped_by_path_end",
        used=used,
        exit_time=None,
        exit_price=None,
        activation_time=activation_time,
        trigger_price=stop_price,
        mfe_bps=mfe_bps,
        mae_bps=mae_bps,
    )


def evaluate_trailing(scenario: IntrabarExitScenario) -> ExitPolicyEvaluation:
    active = False
    activation_time: datetime | None = None
    favorable_anchor = scenario.entry_price
    stop_price: float | None = None
    used: list[IntrabarBar] = []
    mfe_bps = 0.0
    mae_bps = 0.0

    for bar in scenario.bars:
        if bar.end_time_utc <= scenario.entry_time_utc:
            continue
        used.append(bar)
        favorable, adverse = _bar_excursions(scenario.side, scenario.entry_price, bar)
        mfe_bps = max(mfe_bps, favorable)
        mae_bps = max(mae_bps, adverse)

        if active and stop_price is not None and _stop_hit(scenario.side, bar, stop_price):
            return _finalize(
                scenario,
                ExitPolicyName.TRAILING,
                status=ExitStatus.EXITED,
                reason="trailing_stop_triggered_conservative_prior_bar_anchor",
                used=used,
                exit_time=bar.end_time_utc,
                exit_price=stop_price,
                activation_time=activation_time,
                trigger_price=stop_price,
                mfe_bps=mfe_bps,
                mae_bps=mae_bps,
            )

        if not active and favorable >= scenario.trailing_activation_bps:
            active = True
            activation_time = bar.available_at_utc
            favorable_anchor = bar.high if scenario.side == PositionSide.LONG else bar.low
        elif active:
            if scenario.side == PositionSide.LONG:
                favorable_anchor = max(favorable_anchor, bar.high)
            else:
                favorable_anchor = min(favorable_anchor, bar.low)

        if active:
            distance = scenario.trailing_distance_bps / BPS
            stop_price = (
                favorable_anchor * (1.0 - distance)
                if scenario.side == PositionSide.LONG
                else favorable_anchor * (1.0 + distance)
            )

    return _finalize(
        scenario,
        ExitPolicyName.TRAILING,
        status=ExitStatus.OPEN_AT_PATH_END,
        reason="trailing_not_triggered_or_not_stopped_by_path_end",
        used=used,
        exit_time=None,
        exit_price=None,
        activation_time=activation_time,
        trigger_price=stop_price,
        mfe_bps=mfe_bps,
        mae_bps=mae_bps,
    )


def evaluate_time_stop(scenario: IntrabarExitScenario) -> ExitPolicyEvaluation:
    target_seconds = scenario.time_stop_seconds
    used: list[IntrabarBar] = []
    mfe_bps = 0.0
    mae_bps = 0.0

    for bar in scenario.bars:
        if bar.end_time_utc <= scenario.entry_time_utc:
            continue
        used.append(bar)
        favorable, adverse = _bar_excursions(scenario.side, scenario.entry_price, bar)
        mfe_bps = max(mfe_bps, favorable)
        mae_bps = max(mae_bps, adverse)
        elapsed = (bar.end_time_utc - scenario.entry_time_utc).total_seconds()
        if elapsed >= target_seconds:
            return _finalize(
                scenario,
                ExitPolicyName.TIME_STOP,
                status=ExitStatus.EXITED,
                reason="time_stop_exit_at_first_completed_bar_after_horizon",
                used=used,
                exit_time=bar.end_time_utc,
                exit_price=bar.close,
                activation_time=None,
                trigger_price=None,
                mfe_bps=mfe_bps,
                mae_bps=mae_bps,
            )

    return _finalize(
        scenario,
        ExitPolicyName.TIME_STOP,
        status=ExitStatus.OPEN_AT_PATH_END,
        reason="time_stop_horizon_not_reached_by_path_end",
        used=used,
        exit_time=None,
        exit_price=None,
        activation_time=None,
        trigger_price=None,
        mfe_bps=mfe_bps,
        mae_bps=mae_bps,
    )


def evaluate_intrabar_scenario(
    scenario: IntrabarExitScenario,
) -> tuple[ExitPolicyEvaluation, ExitPolicyEvaluation, ExitPolicyEvaluation]:
    return (
        evaluate_breakeven(scenario),
        evaluate_trailing(scenario),
        evaluate_time_stop(scenario),
    )
