from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smartcrypto.risk.kill_switch_classifier import classify_kill_switch
from smartcrypto.risk.kill_switch_guard import (
    DEFAULT_KILL_SWITCH_PATH,
    GLOBAL_BLOCKED,
    KillSwitchGuard,
)
from smartcrypto.risk.risk_manager import set_kill_switch
from smartcrypto.runtime.preflight_orchestrator import (
    BLOCKED,
    RuntimePreflightOrchestrator,
)


CANONICAL_PATH = "data/runtime/kill_switch.json"
NOW = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)


def valid_config(tmp_path: Path, kill_switch_path: Path) -> dict:
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
            "kill_switch_state": str(kill_switch_path),
            "financial_event_log": str(tmp_path / "preflight_events.jsonl"),
        },
    }


def market_snapshot(now: datetime) -> dict[str, object]:
    return {
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


def build_orchestrator(
    tmp_path: Path,
    kill_switch_path: Path,
) -> RuntimePreflightOrchestrator:
    return RuntimePreflightOrchestrator(
        config_path=tmp_path / "runtime_preflight.yml",
        event_log_path=tmp_path / "preflight_events.jsonl",
        kill_switch_path=kill_switch_path,
        state_path=tmp_path / "state.json",
        runtime_mode="paper",
    )


def test_default_paths_are_unified() -> None:
    from smartcrypto.risk import kill_switch_guard as guard_module
    from smartcrypto.runtime import preflight_orchestrator as preflight_module

    assert guard_module.DEFAULT_KILL_SWITCH_PATH == CANONICAL_PATH
    assert "kill_switch_guard.json" not in guard_module.DEFAULT_KILL_SWITCH_PATH

    assert preflight_module.KILL_SWITCH_SINGLE_SOURCE_PATH == CANONICAL_PATH
    assert "kill_switch_guard.json" not in preflight_module.KILL_SWITCH_SINGLE_SOURCE_PATH


def test_set_kill_switch_seen_by_classifier_and_guard_same_path(tmp_path: Path) -> None:
    kill_switch_path = tmp_path / "kill_switch.json"

    set_kill_switch(True, "manual halt", path=kill_switch_path)

    classification = classify_kill_switch(kill_switch_path, now=NOW).to_dict()

    assert classification["status"] == "active"
    assert classification["active_now"] is True
    assert classification["blocks_paper"] is True

    guard = KillSwitchGuard(
        state_path=kill_switch_path,
        event_log_path=tmp_path / "events.jsonl",
        runtime_mode="paper",
    )

    result = guard.evaluate()

    assert result.status == GLOBAL_BLOCKED
    assert result.block_operation is True


def test_set_kill_switch_blocks_preflight(tmp_path: Path) -> None:
    kill_switch_path = tmp_path / "kill_switch.json"

    set_kill_switch(True, "manual halt", path=kill_switch_path)

    orchestrator = build_orchestrator(tmp_path, kill_switch_path)
    result = orchestrator.run(
        config=valid_config(tmp_path, kill_switch_path),
        market_snapshot=market_snapshot(NOW),
        now=NOW,
    )

    assert result.status == BLOCKED
    assert result.block_execution is True
    assert any(error.startswith("kill_switch_") for error in result.errors)


def test_legacy_flat_schema_migrates_in_guard(tmp_path: Path) -> None:
    kill_switch_path = tmp_path / "kill_switch.json"

    kill_switch_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "reason": "legacy_manual_halt",
                "runtime_mode": "paper",
                "updated_at": "2026-05-29T11:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    guard = KillSwitchGuard(
        state_path=kill_switch_path,
        event_log_path=tmp_path / "events.jsonl",
        runtime_mode="paper",
    )

    result = guard.evaluate()

    assert result.status == GLOBAL_BLOCKED
    assert result.block_operation is True

    payload = json.loads(kill_switch_path.read_text(encoding="utf-8"))

    assert payload == {
        "enabled": True,
        "reason": "legacy_manual_halt",
        "runtime_mode": "paper",
        "updated_at": "2026-05-29T11:00:00Z",
    }


def test_no_runtime_file_written_by_import_or_test_defaults() -> None:
    project_runtime = Path(DEFAULT_KILL_SWITCH_PATH)
    existed_before = project_runtime.exists()

    guard = KillSwitchGuard(
        state_path=Path("data/runtime/_unit_test_should_not_exist_kill_switch.json"),
        event_log_path=Path("data/runtime/_unit_test_should_not_exist_events.jsonl"),
        runtime_mode="paper",
    )

    result = guard.evaluate()

    assert result.block_operation is False

    if not existed_before:
        assert not project_runtime.exists()

    assert not Path("data/runtime/_unit_test_should_not_exist_kill_switch.json").exists()