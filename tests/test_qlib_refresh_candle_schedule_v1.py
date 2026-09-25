from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from test_qlib_paper_refresh_supervisor import load_cli_module

from smartcrypto.learning.paper_autolearning import qlib_market_context_economic_challenger as pit
from smartcrypto.qlib_engine import paper_refresh_supervisor as supervisor

START = datetime(2026, 9, 24, 12, 29, 58, tzinfo=UTC).timestamp()


def pit_at(captured: float, decided: float, *, missing_candles: int = 0) -> tuple[list, dict]:
    available = datetime.fromtimestamp((captured // 300 - missing_candles) * 300, UTC)
    market = pd.DataFrame([{
        **dict.fromkeys(pit.MARKET_SOURCE_COLUMNS, 1.0),
        "symbol": "BTCUSDT", "ts": available - timedelta(seconds=300),
        "available_at_utc": available,
    }])
    before = market.copy(deep=True)
    result = pit._align_point_in_time_market_features(
        [{"__symbol": "BTCUSDT", "__open_time": datetime.fromtimestamp(decided, UTC)}], market,
    )
    pd.testing.assert_frame_equal(market, before)
    return result


def test_reproduces_expiry_and_aligned_collection_has_real_pit_budget() -> None:
    old, old_report = pit_at(START, START + 28)
    assert old == [] and old_report["stale_or_gap_feature_count"] == 1
    delay = supervisor.next_refresh_delay(interval_seconds=300, timeframe="5m", initial=True, now=START)
    assert delay == 7
    captured = START + delay
    ready, report = pit_at(captured, captured + 28)
    assert report["coverage"] == 1.0
    assert ready[0]["__market_feature_age_seconds"] == 33
    assert report["maximum_feature_age_seconds_exclusive"] == 300
    assert report["same_candle_lookahead_allowed"] is False


@pytest.mark.parametrize("runner", ["cli", "library"])
def test_continuous_entrypoints_keep_slots_despite_runtime_duration(monkeypatch, capsys, runner) -> None:
    clock = [START]
    starts = []
    module = load_cli_module() if runner == "cli" else supervisor

    def run(config):
        if len(starts) == 3:
            raise KeyboardInterrupt
        starts.append(clock[0])
        clock[0] += [28, 45, 28][len(starts) - 1]
        ready, _ = pit_at(starts[-1], clock[0])
        assert len(ready) == 1
        return {"status": "ok"}

    monkeypatch.setattr(module, "run_paper_refresh_supervisor", run)
    monkeypatch.setattr(supervisor.time, "time", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    with pytest.raises(KeyboardInterrupt):
        if runner == "cli":
            module.main(["--interval-seconds", "300"])
        else:
            module.run_supervisor_loop(supervisor.PaperRefreshSupervisorConfig(), interval_seconds=300, once=False)
    assert starts == [START + 7, START + 307, START + 607]


def test_missed_slots_are_skipped_without_replaying_decisions() -> None:
    completed = START + 7 + 650
    delay = supervisor.next_refresh_delay(interval_seconds=300, timeframe="5m", now=completed)
    assert delay == 250
    assert completed + delay == START + 907


@pytest.mark.parametrize("duration,missing", [(295, 0), (30, 1)])
def test_overrun_or_missing_download_remains_fail_closed(duration, missing) -> None:
    captured = START + 7
    ready, report = pit_at(captured, captured + duration, missing_candles=missing)
    assert ready == []
    assert report["coverage"] == 0.0
    assert report["maximum_feature_age_seconds_exclusive"] == 300


def test_custom_non_candle_interval_and_once_keep_existing_behavior(monkeypatch) -> None:
    assert supervisor.next_refresh_delay(interval_seconds=42, timeframe="5m", initial=True, now=START) == 0
    assert supervisor.next_refresh_delay(interval_seconds=42, timeframe="5m", now=START) == 42
    monkeypatch.setattr(supervisor, "run_paper_refresh_supervisor", lambda cfg: {"status": "ok"})

    def unexpected_sleep(delay):
        pytest.fail("once must not wait for a scheduled slot")

    monkeypatch.setattr(supervisor.time, "sleep", unexpected_sleep)
    assert supervisor.run_supervisor_loop(supervisor.PaperRefreshSupervisorConfig(), interval_seconds=300) == {"status": "ok"}


@pytest.mark.parametrize("clock", [float("nan"), float("inf")])
def test_invalid_clock_is_not_used_to_schedule(clock) -> None:
    with pytest.raises(ValueError, match="clock_must_be_finite"):
        supervisor.next_refresh_delay(interval_seconds=300, timeframe="5m", now=clock)
