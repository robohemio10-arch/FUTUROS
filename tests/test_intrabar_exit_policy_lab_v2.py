from __future__ import annotations

from datetime import datetime, timedelta, timezone

from smartcrypto.research.execution_intelligence import (
    CandleGranularity,
    ExitPolicyName,
    ExitStatus,
    IntrabarBar,
    IntrabarExitScenario,
    PositionSide,
    evaluate_intrabar_scenario,
)

UTC = timezone.utc
HASH_A = "a" * 64
BASE = datetime(2026, 8, 28, 18, 0, tzinfo=UTC)


def bar(
    index: int,
    start_seconds: int,
    *,
    seconds: int = 15,
    open_: float = 100.0,
    high: float = 100.0,
    low: float = 100.0,
    close: float = 100.0,
    granularity: CandleGranularity = CandleGranularity.SECOND_15,
) -> IntrabarBar:
    start = BASE + timedelta(seconds=start_seconds)
    end = start + timedelta(seconds=seconds)
    return IntrabarBar(
        bar_id=f"bar-{index}",
        source_id="fixture",
        symbol="BTCUSDT",
        granularity=granularity,
        start_time_utc=start,
        end_time_utc=end,
        available_at_utc=end,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=10.0,
        source_hash=HASH_A,
    )


def scenario(
    *,
    granularity: CandleGranularity = CandleGranularity.SECOND_15,
) -> IntrabarExitScenario:
    seconds = 15 if granularity == CandleGranularity.SECOND_15 else 60
    bars = (
        bar(
            1,
            0,
            seconds=seconds,
            open_=100.0,
            high=100.4,
            low=99.9,
            close=100.3,
            granularity=granularity,
        ),
        bar(
            2,
            seconds,
            seconds=seconds,
            open_=100.3,
            high=100.6,
            low=100.25,
            close=100.5,
            granularity=granularity,
        ),
        bar(
            3,
            seconds * 2,
            seconds=seconds,
            open_=100.5,
            high=100.55,
            low=100.1,
            close=100.2,
            granularity=granularity,
        ),
        bar(
            4,
            seconds * 3,
            seconds=seconds,
            open_=100.2,
            high=100.25,
            low=99.95,
            close=100.0,
            granularity=granularity,
        ),
    )
    return IntrabarExitScenario(
        scenario_id=f"exit-{granularity.value}",
        strategy_id="exit-research-v1",
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        entry_time_utc=BASE,
        entry_price=100.0,
        notional_usdt=1_000.0,
        bars=bars,
        breakeven_trigger_bps=30.0,
        breakeven_lock_bps=0.0,
        trailing_activation_bps=30.0,
        trailing_distance_bps=20.0,
        time_stop_seconds=seconds * 3,
        exit_cost_bps=4.0,
    )


def test_intrabar_lab_evaluates_be_trailing_and_time_stop() -> None:
    results = evaluate_intrabar_scenario(scenario())
    assert {item.policy for item in results} == set(ExitPolicyName)
    assert all(item.path_granularity == CandleGranularity.SECOND_15 for item in results)
    assert all(item.lookahead_detected is False for item in results)
    assert all(item.capital_hours_consumed >= 0 for item in results)


def test_breakeven_activation_is_not_allowed_to_stop_in_same_bar() -> None:
    bars = (
        bar(1, 0, high=100.5, low=99.5, close=100.3),
        bar(2, 15, high=100.4, low=99.9, close=100.0),
    )
    item = scenario().model_copy(update={"bars": bars, "breakeven_trigger_bps": 30.0})
    breakeven = evaluate_intrabar_scenario(item)[0]
    assert breakeven.status == ExitStatus.EXITED
    assert breakeven.exit_time_utc == bars[1].end_time_utc
    assert breakeven.reason == "breakeven_stop_triggered_conservative_next_bar"


def test_trailing_uses_prior_completed_bar_anchor() -> None:
    trailing = evaluate_intrabar_scenario(scenario())[1]
    assert trailing.policy == ExitPolicyName.TRAILING
    assert trailing.status == ExitStatus.EXITED
    assert trailing.activation_time_utc is not None
    assert trailing.trigger_price is not None
    assert trailing.net_return_bps is not None


def test_time_stop_exits_at_first_completed_bar_after_horizon_and_reports_costs() -> None:
    time_stop = evaluate_intrabar_scenario(scenario())[2]
    assert time_stop.policy == ExitPolicyName.TIME_STOP
    assert time_stop.status == ExitStatus.EXITED
    assert time_stop.hold_seconds == 45.0
    assert time_stop.gross_return_bps is not None
    assert time_stop.net_return_bps == time_stop.gross_return_bps - 4.0
    assert time_stop.capital_hours_consumed == 12.5


def test_one_minute_path_is_supported_without_changing_ordering_policy() -> None:
    results = evaluate_intrabar_scenario(scenario(granularity=CandleGranularity.MINUTE_1))
    assert all(item.path_granularity == CandleGranularity.MINUTE_1 for item in results)
    assert all(item.intrabar_ordering == "conservative_adverse_first" for item in results)


def test_short_side_return_sign_and_excursions_are_directionally_correct() -> None:
    bars = (
        bar(1, 0, open_=100.0, high=100.1, low=99.5, close=99.6),
        bar(2, 15, open_=99.6, high=99.8, low=99.2, close=99.3),
        bar(3, 30, open_=99.3, high=99.7, low=99.2, close=99.6),
    )
    item = scenario().model_copy(
        update={
            "scenario_id": "exit-short",
            "side": PositionSide.SHORT,
            "bars": bars,
            "time_stop_seconds": 45,
        }
    )
    time_stop = evaluate_intrabar_scenario(item)[2]
    assert time_stop.gross_return_bps is not None and time_stop.gross_return_bps > 0
    assert time_stop.mfe_bps > 0
    assert time_stop.mae_bps > 0
