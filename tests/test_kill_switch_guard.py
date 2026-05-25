from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartcrypto.risk.kill_switch_guard import (
    CLEAR,
    CORRUPTED,
    GLOBAL_BLOCKED,
    SYMBOL_BLOCKED,
    KillSwitchBlockedError,
    KillSwitchGuard,
)


def build_guard(tmp_path: Path) -> KillSwitchGuard:
    return KillSwitchGuard(
        state_path=tmp_path / "kill_switch.json",
        event_log_path=tmp_path / "events.jsonl",
        runtime_mode="paper",
    )


def test_missing_state_is_clear_and_does_not_create_runtime_file(tmp_path: Path) -> None:
    guard = build_guard(tmp_path)

    result = guard.evaluate("BTCUSDT")

    assert result.status == CLEAR
    assert result.block_operation is False
    assert not guard.state_path.exists()
    assert guard.event_logger.read_events() == []


def test_global_kill_switch_blocks_all_symbols_and_records_events(tmp_path: Path) -> None:
    guard = build_guard(tmp_path)

    state = guard.activate_global(
        reason="daily emergency stop",
        actor="risk-operator",
        correlation_id="corr-global",
    )
    result = guard.evaluate("ETHUSDT")

    assert state["global"]["enabled"] is True
    assert state["global"]["reason"] == "daily emergency stop"
    assert state["global"]["actor"] == "risk-operator"
    assert state["global"]["updated_at"].endswith("Z")
    assert result.status == GLOBAL_BLOCKED
    assert result.block_operation is True
    with pytest.raises(KillSwitchBlockedError):
        guard.assert_clear("BTCUSDT")
    assert [event["event_type"] for event in guard.event_logger.read_events()] == [
        "kill_switch_triggered",
        "kill_switch_blocked",
        "kill_switch_blocked",
    ]


def test_symbol_kill_switch_only_blocks_requested_symbol(tmp_path: Path) -> None:
    guard = build_guard(tmp_path)

    guard.activate_symbol("btcusdt", reason="manual symbol halt", actor="operator-a")

    blocked = guard.evaluate("BTCUSDT")
    clear = guard.evaluate("ETHUSDT")

    assert blocked.status == SYMBOL_BLOCKED
    assert blocked.block_operation is True
    assert blocked.symbol == "BTCUSDT"
    assert clear.status == CLEAR
    assert clear.block_operation is False


def test_clear_global_and_symbol_return_clear(tmp_path: Path) -> None:
    guard = build_guard(tmp_path)
    guard.activate_global(reason="test halt", actor="risk")
    guard.activate_symbol("ETHUSDT", reason="symbol halt", actor="risk")

    guard.clear_global(actor="risk", reason="resolved")
    guard.clear_symbol("ETHUSDT", actor="risk", reason="resolved")

    assert guard.evaluate("ETHUSDT").status == CLEAR
    assert guard.evaluate("BTCUSDT").status == CLEAR
    events = [event["event_type"] for event in guard.event_logger.read_events()]
    assert events.count("kill_switch_cleared") == 2


def test_activate_requires_reason_and_actor(tmp_path: Path) -> None:
    guard = build_guard(tmp_path)

    with pytest.raises(ValueError, match="reason is required"):
        guard.activate_global(reason="", actor="risk")
    with pytest.raises(ValueError, match="actor is required"):
        guard.activate_symbol("BTCUSDT", reason="halt", actor="")


def test_corrupted_json_blocks_without_repairing_state(tmp_path: Path) -> None:
    guard = build_guard(tmp_path)
    guard.state_path.write_text("{broken json", encoding="utf-8")

    result = guard.evaluate("BTCUSDT")

    assert result.status == CORRUPTED
    assert result.block_operation is True
    assert "kill_switch_json_corrupted" in result.errors[0]
    assert guard.state_path.read_text(encoding="utf-8") == "{broken json"
    assert guard.event_logger.read_events()[-1]["event_type"] == "kill_switch_corrupted"


def test_invalid_enabled_entry_is_corrupted_and_blocks(tmp_path: Path) -> None:
    guard = build_guard(tmp_path)
    guard.state_path.write_text(
        json.dumps({"runtime_mode": "paper", "global": {"enabled": "yes"}, "symbols": {}}),
        encoding="utf-8",
    )

    result = guard.evaluate()

    assert result.status == CORRUPTED
    assert result.block_operation is True
