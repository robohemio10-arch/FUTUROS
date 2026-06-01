from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smartcrypto.config.schema import load_config_file
from smartcrypto.runtime.preflight_orchestrator import (
    BLOCKED,
    FAILED,
    PASSED,
    RuntimePreflightOrchestrator,
)


def valid_config(tmp_path: Path) -> dict:
    return {
        "runtime_mode": "paper",
        "safety": {
            "live_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "allow_ai_to_increase_size": False,
            "allow_dashboard_direct_order": False,
        },
        "risk_limits": {
            "max_drawdown_pct": 5.0,
            "max_data_age_seconds": 300,
            "max_spread_bps": 25.0,
            "max_order_notional": 50.0,
            "max_capital_global": 100.0,
        },
        "paths": {
            "state_repository": str(tmp_path / "state.json"),
            "kill_switch_state": str(tmp_path / "kill_switch_guard.json"),
            "financial_event_log": str(tmp_path / "preflight_events.jsonl"),
        },
    }


def market_snapshot(now: datetime, **overrides: object) -> dict[str, object]:
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


def build_orchestrator(tmp_path: Path) -> RuntimePreflightOrchestrator:
    return RuntimePreflightOrchestrator(
        config_path=tmp_path / "runtime_preflight.yml",
        event_log_path=tmp_path / "preflight_events.jsonl",
        kill_switch_path=tmp_path / "kill_switch_guard.json",
        state_path=tmp_path / "state.json",
        runtime_mode="paper",
    )


def test_preflight_passes_for_safe_config_market_and_reconciled_state(tmp_path: Path) -> None:
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    orchestrator = build_orchestrator(tmp_path)

    result = orchestrator.run(
        config=valid_config(tmp_path),
        market_snapshot=market_snapshot(now),
        now=now,
    )

    assert result.status == PASSED
    assert result.block_execution is False
    events = [event["event_type"] for event in orchestrator.event_logger.read_events()]
    assert "runtime_preflight_started" in events
    assert "market_data_healthy" in events
    assert "state_reconciled" in events
    assert events[-1] == "runtime_preflight_passed"


def test_blocks_runtime_mode_live_in_config(tmp_path: Path) -> None:
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    config = valid_config(tmp_path)
    config["runtime_mode"] = "live"

    result = build_orchestrator(tmp_path).run(
        config=config,
        market_snapshot=market_snapshot(now),
        now=now,
    )

    assert result.status == BLOCKED
    assert any("runtime_mode_not_allowed" in error for error in result.errors)


def test_blocks_live_and_order_env_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORDER_SUBMISSION_ENABLED", "true")
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    orchestrator = build_orchestrator(tmp_path)

    result = orchestrator.run(
        config=valid_config(tmp_path),
        market_snapshot=market_snapshot(now),
        now=now,
    )

    assert result.status == BLOCKED
    assert result.block_execution is True
    assert "ORDER_SUBMISSION_ENABLED=true" in result.errors
    assert orchestrator.event_logger.read_events()[-1]["event_type"] == "runtime_guard_blocked"


def test_blocks_when_market_data_is_blocked(tmp_path: Path) -> None:
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)

    result = build_orchestrator(tmp_path).run(
        config=valid_config(tmp_path),
        market_snapshot=market_snapshot(now, liquidity_usdt=1.0),
        now=now,
    )

    assert result.status == BLOCKED
    assert result.block_execution is True
    assert result.errors == ["market_data_blocked"]
    assert result.checks["market_data"]["status"] == "BLOCKED"


def test_blocks_when_reconciliation_diverges(tmp_path: Path) -> None:
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    config = valid_config(tmp_path)
    state_path = Path(config["paths"]["state_repository"])
    state_path.write_text(
        '{"runtime_mode":"paper","capital":{"max_capital_global":100,'
        '"reserved_notional":99,"filled_notional":0,"available_notional":1},'
        '"reservations":{},"positions":{},"events":[]}',
        encoding="utf-8",
    )

    result = build_orchestrator(tmp_path).run(
        config=config,
        market_snapshot=market_snapshot(now),
        now=now,
    )

    assert result.status == BLOCKED
    assert result.errors == ["reconciliation_diverged"]
    assert result.checks["reconciliation"]["status"] == "DIVERGED"


def test_fails_when_market_snapshot_missing(tmp_path: Path) -> None:
    result = build_orchestrator(tmp_path).run(config=valid_config(tmp_path))

    assert result.status == FAILED
    assert result.errors == ["market_snapshot_required"]


def test_example_config_is_valid_for_schema(tmp_path: Path) -> None:
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    config = load_config_file("config/runtime_preflight.example.yml")
    config.setdefault("paths", {})["state_repository"] = str(
        tmp_path / "runtime_preflight_state.json"
    )
    config["paths"]["financial_event_log"] = str(
        tmp_path / "runtime_preflight_events.jsonl"
    )
    config["paths"]["kill_switch_state"] = str(tmp_path / "kill_switch_guard.json")

    orchestrator = RuntimePreflightOrchestrator(
        config_path="config/runtime_preflight.example.yml",
        event_log_path=tmp_path / "runtime_preflight_events.jsonl",
        kill_switch_path=tmp_path / "kill_switch_guard.json",
        state_path=tmp_path / "runtime_preflight_state.json",
        runtime_mode="paper",
    )

    result = orchestrator.run(config=config, market_snapshot=market_snapshot(now), now=now)

    assert result.status == PASSED
