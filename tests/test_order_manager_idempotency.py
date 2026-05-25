from __future__ import annotations

from pathlib import Path

import pytest

from smartcrypto.execution.order_manager import (
    DuplicateClientOrderError,
    InvalidOrderIntentError,
    OrderManager,
    OrderSafetyError,
)
from smartcrypto.risk.risk_manager import RiskLimits, RiskManager


def build_manager(tmp_path: Path, risk_limits: RiskLimits | None = None) -> OrderManager:
    return OrderManager(
        state_path=tmp_path / "order_manager_state.json",
        event_log_path=tmp_path / "order_manager_events.jsonl",
        runtime_mode="paper",
        max_capital_global=100.0,
        risk_manager=RiskManager(
            risk_limits
            or RiskLimits(
                runtime_mode="paper",
                max_position_usdt=100.0,
                max_leverage=2.0,
                min_score_long=0.6,
                max_score_short=0.4,
                kill_switch_enabled=False,
                allowed_pairs=("BTCUSDT", "ETHUSDT"),
            )
        ),
    )


def test_creates_paper_order_intent_and_submits_without_real_order(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)

    intent = manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=25.0,
        score=0.75,
    )

    assert intent.order_intent_id
    assert intent.status == "PAPER_SUBMITTED"
    assert intent.reservation_id
    state = manager.repository.load()
    assert state["order_intents"][intent.order_intent_id]["status"] == "PAPER_SUBMITTED"
    assert state["capital"]["reserved_notional"] == 25.0
    event_types = [event["event_type"] for event in manager.event_logger.read_events()]
    assert event_types == ["signal_generated", "risk_approved", "paper_trade_adjusted"]


def test_requires_correlation_id_and_client_order_id(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)

    with pytest.raises(InvalidOrderIntentError, match="correlation_id is required"):
        manager.create_order_intent(
            correlation_id="",
            client_order_id="client-1",
            symbol="BTCUSDT",
            side="long",
            notional=10.0,
            score=0.7,
        )

    with pytest.raises(InvalidOrderIntentError, match="client_order_id is required"):
        manager.create_order_intent(
            correlation_id="corr-1",
            client_order_id="",
            symbol="BTCUSDT",
            side="long",
            notional=10.0,
            score=0.7,
        )


def test_blocks_duplicate_client_order_id(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="duplicate-client-id",
        symbol="BTCUSDT",
        side="long",
        notional=10.0,
        score=0.7,
    )

    with pytest.raises(DuplicateClientOrderError):
        manager.create_order_intent(
            correlation_id="corr-2",
            client_order_id="duplicate-client-id",
            symbol="BTCUSDT",
            side="long",
            notional=10.0,
            score=0.7,
        )


def test_passes_through_risk_manager_before_capital_reservation(tmp_path: Path) -> None:
    manager = build_manager(
        tmp_path,
        RiskLimits(
            runtime_mode="paper",
            max_position_usdt=100.0,
            max_leverage=2.0,
            min_score_long=0.8,
            max_score_short=0.2,
            allowed_pairs=("BTCUSDT",),
        ),
    )

    intent = manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=10.0,
        score=0.5,
    )

    assert intent.status == "RISK_REJECTED"
    assert intent.reservation_id is None
    assert manager.repository.load()["capital"]["reserved_notional"] == 0.0
    event_types = [event["event_type"] for event in manager.event_logger.read_events()]
    assert event_types == ["signal_generated", "risk_rejected"]


def test_fills_cancel_and_reject_lifecycle_states(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    fill_intent = manager.create_order_intent(
        correlation_id="corr-fill",
        client_order_id="client-fill",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    cancel_intent = manager.create_order_intent(
        correlation_id="corr-cancel",
        client_order_id="client-cancel",
        symbol="ETHUSDT",
        side="short",
        notional=10.0,
        score=0.3,
    )
    reject_intent = manager.create_order_intent(
        correlation_id="corr-reject",
        client_order_id="client-reject",
        symbol="ETHUSDT",
        side="short",
        notional=10.0,
        score=0.3,
    )

    filled = manager.fill_order(fill_intent.client_order_id)
    cancelled = manager.cancel_order(cancel_intent.client_order_id)
    rejected = manager.reject_order(reject_intent.client_order_id, reason="paper_only")

    assert filled.status == "PAPER_FILLED"
    assert cancelled.status == "PAPER_CANCELLED"
    assert rejected.status == "PAPER_REJECTED"
    state = manager.repository.load()
    assert state["reservations"][fill_intent.reservation_id]["status"] == "FILLED"
    assert state["reservations"][cancel_intent.reservation_id]["status"] == "CANCELLED"
    assert state["reservations"][reject_intent.reservation_id]["status"] == "REJECTED"


def test_recovers_orders_after_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "order_manager_state.json"
    log_path = tmp_path / "events.jsonl"
    first = OrderManager(
        state_path=state_path,
        event_log_path=log_path,
        runtime_mode="paper",
        max_capital_global=100.0,
        risk_manager=RiskManager(
            RiskLimits(runtime_mode="paper", allowed_pairs=("BTCUSDT",))
        ),
    )
    first.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=10.0,
        score=0.7,
    )

    restarted = OrderManager(
        state_path=state_path,
        event_log_path=log_path,
        runtime_mode="paper",
        max_capital_global=100.0,
        risk_manager=RiskManager(
            RiskLimits(runtime_mode="paper", allowed_pairs=("BTCUSDT",))
        ),
    )

    assert restarted.get_order("client-1").status == "PAPER_SUBMITTED"
    with pytest.raises(DuplicateClientOrderError):
        restarted.create_order_intent(
            correlation_id="corr-2",
            client_order_id="client-1",
            symbol="BTCUSDT",
            side="long",
            notional=10.0,
            score=0.7,
        )


def test_blocks_live_runtime_and_order_submission_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(OrderSafetyError):
        OrderManager(
            state_path=tmp_path / "state.json",
            event_log_path=tmp_path / "events.jsonl",
            runtime_mode="live",
        )

    monkeypatch.setenv("ORDER_SUBMISSION_ENABLED", "true")
    with pytest.raises(OrderSafetyError):
        OrderManager(
            state_path=tmp_path / "state.json",
            event_log_path=tmp_path / "events.jsonl",
            runtime_mode="paper",
        )
