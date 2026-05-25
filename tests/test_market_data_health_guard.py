from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smartcrypto.market.market_data_health_guard import (
    BLOCKED,
    DEGRADED,
    HEALTHY,
    MarketDataBlockedError,
    MarketDataHealthGuard,
    MarketDataHealthLimits,
    MarketDataSafetyError,
)


def snapshot(now: datetime, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "BTCUSDT",
        "ticker_timestamp_utc": (now - timedelta(seconds=5)).isoformat(),
        "candle_timestamp_utc": (now - timedelta(seconds=60)).isoformat(),
        "bid": 100.0,
        "ask": 100.1,
        "liquidity_usdt": 50_000.0,
        "latency_ms": 100.0,
        "ws_price": 100.0,
        "rest_price": 100.02,
    }
    payload.update(overrides)
    return payload


def build_guard(tmp_path: Path) -> MarketDataHealthGuard:
    return MarketDataHealthGuard(
        event_log_path=tmp_path / "market_data_health.jsonl",
        runtime_mode="paper",
    )


def test_healthy_market_data_records_event(tmp_path: Path) -> None:
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    guard = build_guard(tmp_path)

    result = guard.evaluate(snapshot(now), now=now)

    assert result.status == HEALTHY
    assert result.block_decision is False
    assert guard.event_logger.read_events()[-1]["event_type"] == "market_data_healthy"


def test_detects_stale_ticker_and_stale_candle_as_blocked(tmp_path: Path) -> None:
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    result = build_guard(tmp_path).evaluate(
        snapshot(
            now,
            ticker_timestamp_utc=(now - timedelta(seconds=31)).isoformat(),
            candle_timestamp_utc=(now - timedelta(seconds=301)).isoformat(),
        ),
        now=now,
    )

    assert result.status == BLOCKED
    assert "stale_ticker" in result.reasons
    assert "stale_candle" in result.reasons


def test_detects_spread_liquidity_latency_and_ws_rest_divergence(tmp_path: Path) -> None:
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    result = build_guard(tmp_path).evaluate(
        snapshot(
            now,
            bid=100.0,
            ask=101.0,
            liquidity_usdt=100.0,
            latency_ms=1500.0,
            ws_price=100.0,
            rest_price=101.0,
        ),
        now=now,
    )

    assert result.status == BLOCKED
    assert "spread_too_high" in result.reasons
    assert "liquidity_too_low" in result.reasons
    assert "latency_too_high" in result.reasons
    assert "ws_rest_divergence_too_high" in result.reasons


def test_degraded_market_data_does_not_block_but_records_event(tmp_path: Path) -> None:
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    result = build_guard(tmp_path).evaluate(
        snapshot(
            now,
            ticker_timestamp_utc=(now - timedelta(seconds=20)).isoformat(),
            latency_ms=700.0,
        ),
        now=now,
    )

    assert result.status == DEGRADED
    assert result.block_decision is False
    assert "ticker_age_degraded" in result.warnings
    assert "latency_degraded" in result.warnings


def test_assert_healthy_blocks_decision_when_status_blocked(tmp_path: Path) -> None:
    now = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)

    with pytest.raises(MarketDataBlockedError):
        build_guard(tmp_path).assert_healthy_for_decision(
            snapshot(now, liquidity_usdt=1.0),
            now=now,
        )


def test_blocks_live_runtime_and_order_submission_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(MarketDataSafetyError):
        MarketDataHealthGuard(
            event_log_path=tmp_path / "events.jsonl",
            runtime_mode="live",
        )

    monkeypatch.setenv("REAL_ORDER_SUBMISSION_ENABLED", "true")
    with pytest.raises(MarketDataSafetyError):
        MarketDataHealthGuard(
            event_log_path=tmp_path / "events.jsonl",
            runtime_mode="paper",
        )


def test_loads_safe_example_config(tmp_path: Path) -> None:
    guard = MarketDataHealthGuard.from_config("config/market_data_health_guard.example.yml")

    assert isinstance(guard.limits, MarketDataHealthLimits)
    assert guard.runtime_mode == "paper"
