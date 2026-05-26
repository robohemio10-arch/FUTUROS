from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from smartcrypto.dashboard.command_bus import REJECTED, DashboardReadonlyCommandBus
from smartcrypto.execution.order_manager import OrderManager
from smartcrypto.risk.kill_switch_guard import KillSwitchGuard
from smartcrypto.risk.risk_manager import RiskLimits, RiskManager
from smartcrypto.runtime.preflight_orchestrator import BLOCKED, RuntimePreflightOrchestrator


def safe_config(tmp_path: Path) -> dict:
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
            "financial_event_log": str(tmp_path / "events.jsonl"),
            "kill_switch_state": str(tmp_path / "kill_switch.json"),
        },
    }


def market_snapshot(now: datetime, symbol: str = "BTCUSDT") -> dict[str, object]:
    return {
        "symbol": symbol,
        "ticker_timestamp_utc": (now - timedelta(seconds=5)).isoformat(),
        "candle_timestamp_utc": (now - timedelta(seconds=60)).isoformat(),
        "bid": 100.0,
        "ask": 100.1,
        "liquidity_usdt": 50_000.0,
        "latency_ms": 100.0,
        "ws_price": 100.0,
        "rest_price": 100.02,
    }


def activate_global(tmp_path: Path) -> None:
    KillSwitchGuard(
        state_path=tmp_path / "kill_switch.json",
        event_log_path=tmp_path / "events.jsonl",
    ).activate_global(reason="institutional halt", actor="risk")


def activate_symbol(tmp_path: Path, symbol: str = "BTCUSDT") -> None:
    KillSwitchGuard(
        state_path=tmp_path / "kill_switch.json",
        event_log_path=tmp_path / "events.jsonl",
    ).activate_symbol(symbol, reason="symbol halt", actor="risk")


def event_types(path: Path) -> list[str]:
    logger = KillSwitchGuard(
        state_path=path / "unused.json",
        event_log_path=path / "events.jsonl",
    ).event_logger
    return [event["event_type"] for event in logger.read_events()]


def test_preflight_blocks_when_global_kill_switch_is_active(tmp_path: Path) -> None:
    activate_global(tmp_path)
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    orchestrator = RuntimePreflightOrchestrator(
        event_log_path=tmp_path / "events.jsonl",
        state_path=tmp_path / "state.json",
        kill_switch_path=tmp_path / "kill_switch.json",
        runtime_mode="paper",
    )

    result = orchestrator.run(
        config=safe_config(tmp_path),
        market_snapshot=market_snapshot(now),
        now=now,
    )

    assert result.status == BLOCKED
    assert result.block_execution is True
    assert result.errors == ["kill_switch_global_blocked"]
    assert "kill_switch_blocked" in event_types(tmp_path)
    assert event_types(tmp_path)[-1] == "runtime_preflight_failed"


def test_preflight_blocks_when_symbol_kill_switch_is_active(tmp_path: Path) -> None:
    activate_symbol(tmp_path, "ETHUSDT")
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)

    result = RuntimePreflightOrchestrator(
        event_log_path=tmp_path / "events.jsonl",
        state_path=tmp_path / "state.json",
        kill_switch_path=tmp_path / "kill_switch.json",
        runtime_mode="paper",
    ).run(config=safe_config(tmp_path), market_snapshot=market_snapshot(now, "ETHUSDT"), now=now)

    assert result.status == BLOCKED
    assert result.errors == ["kill_switch_symbol_blocked"]
    assert "kill_switch_blocked" in event_types(tmp_path)


def test_order_manager_rejects_intent_when_global_kill_switch_is_active(
    tmp_path: Path,
) -> None:
    activate_global(tmp_path)
    manager = OrderManager(
        state_path=tmp_path / "order_state.json",
        event_log_path=tmp_path / "events.jsonl",
        kill_switch_path=tmp_path / "kill_switch.json",
        runtime_mode="paper",
        max_capital_global=100.0,
        risk_manager=RiskManager(
            RiskLimits(runtime_mode="paper", allowed_pairs=("BTCUSDT",))
        ),
    )

    intent = manager.create_order_intent(
        correlation_id="corr-order",
        client_order_id="client-order",
        symbol="BTCUSDT",
        side="long",
        notional=10.0,
        score=0.7,
    )

    assert intent.status == "RISK_REJECTED"
    assert intent.reservation_id is None
    assert intent.risk_reasons == ["kill_switch_global_blocked"]
    events = event_types(tmp_path)
    assert "kill_switch_blocked" in events
    assert events[-1] == "risk_rejected"


def test_order_manager_rejects_intent_when_symbol_kill_switch_is_active(
    tmp_path: Path,
) -> None:
    activate_symbol(tmp_path, "BTCUSDT")
    manager = OrderManager(
        state_path=tmp_path / "order_state.json",
        event_log_path=tmp_path / "events.jsonl",
        kill_switch_path=tmp_path / "kill_switch.json",
        runtime_mode="paper",
        max_capital_global=100.0,
        risk_manager=RiskManager(
            RiskLimits(runtime_mode="paper", allowed_pairs=("BTCUSDT",))
        ),
    )

    intent = manager.create_order_intent(
        correlation_id="corr-order",
        client_order_id="client-order",
        symbol="BTCUSDT",
        side="long",
        notional=10.0,
        score=0.7,
    )

    assert intent.status == "RISK_REJECTED"
    assert intent.risk_reasons == ["kill_switch_symbol_blocked"]
    assert "kill_switch_blocked" in event_types(tmp_path)


def test_dashboard_rejects_command_when_kill_switch_is_active(tmp_path: Path) -> None:
    activate_symbol(tmp_path, "BTCUSDT")
    bus = DashboardReadonlyCommandBus(
        event_log_path=tmp_path / "events.jsonl",
        kill_switch_path=tmp_path / "kill_switch.json",
        runtime_mode="paper",
        readonly=True,
    )

    result = bus.submit(
        "request_market_health_check",
        correlation_id="corr-dashboard",
        operator="analyst",
        source="dashboard",
        payload={"symbol": "BTCUSDT"},
    )

    assert result.status == REJECTED
    assert result.reasons == ["kill_switch_symbol_blocked"]
    events = event_types(tmp_path)
    assert "kill_switch_blocked" in events
    assert events[-1] == "dashboard_command_rejected"
