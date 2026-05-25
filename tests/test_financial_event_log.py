from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartcrypto.state.financial_event_log import (
    MINIMUM_EVENT_TYPES,
    FinancialEventLogger,
    FinancialEventLogConfig,
    InvalidFinancialEvent,
    RuntimeSafetySnapshot,
)


def test_records_valid_jsonl_with_required_fields(tmp_path: Path) -> None:
    log_path = tmp_path / "financial_event_log.jsonl"
    logger = FinancialEventLogger(log_path, runtime_mode="paper", source="unit_test")

    event = logger.record(
        "signal_generated",
        correlation_id="corr-1",
        symbol="btc/usdt",
        payload={"score": 0.72},
    )

    assert event.event_type == "signal_generated"
    assert event.symbol == "BTC/USDT"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    decoded = json.loads(lines[0])
    assert set(decoded) == {
        "event_id",
        "timestamp_utc",
        "event_type",
        "correlation_id",
        "symbol",
        "source",
        "runtime_mode",
        "live_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "payload",
    }
    assert decoded["runtime_mode"] == "paper"
    assert decoded["live_enabled"] is False
    assert decoded["order_submission_enabled"] is False
    assert decoded["real_order_submission_enabled"] is False
    assert decoded["payload"] == {"score": 0.72}


def test_supports_all_required_event_types(tmp_path: Path) -> None:
    logger = FinancialEventLogger(tmp_path / "events.jsonl")

    for event_type in sorted(MINIMUM_EVENT_TYPES):
        logger.record(event_type, correlation_id="corr-all", payload={"event_type": event_type})

    events = logger.read_events()
    assert [event["event_type"] for event in events] == sorted(MINIMUM_EVENT_TYPES)


def test_rejects_unknown_event_type(tmp_path: Path) -> None:
    logger = FinancialEventLogger(tmp_path / "events.jsonl")

    with pytest.raises(InvalidFinancialEvent):
        logger.record("real_order_submitted", payload={})


def test_blocks_unsafe_runtime_for_regular_events(tmp_path: Path) -> None:
    logger = FinancialEventLogger(tmp_path / "events.jsonl")
    runtime = RuntimeSafetySnapshot(
        runtime_mode="live",
        live_enabled=True,
        order_submission_enabled=True,
        real_order_submission_enabled=True,
    )

    with pytest.raises(InvalidFinancialEvent):
        logger.record("risk_approved", payload={}, runtime=runtime)
    assert not logger.log_path.exists()


def test_allows_runtime_guard_blocked_to_capture_unsafe_flags(tmp_path: Path) -> None:
    logger = FinancialEventLogger(tmp_path / "events.jsonl")
    runtime = RuntimeSafetySnapshot(
        runtime_mode="live",
        live_enabled=True,
        order_submission_enabled=True,
        real_order_submission_enabled=True,
    )

    event = logger.record(
        "runtime_guard_blocked",
        symbol="ETHUSDT",
        payload={"reason": "live_flags_blocked"},
        runtime=runtime,
    )

    assert event.live_enabled is True
    assert event.order_submission_enabled is True
    assert event.real_order_submission_enabled is True
    assert logger.read_events()[0]["event_type"] == "runtime_guard_blocked"


def test_loads_example_config_contract() -> None:
    config = FinancialEventLogConfig.from_yaml("config/financial_event_log.example.yml")

    assert config.log_path == "data/runtime/financial_event_log.jsonl"
    assert config.runtime_mode == "paper"
    assert set(config.allowed_event_types) == MINIMUM_EVENT_TYPES
