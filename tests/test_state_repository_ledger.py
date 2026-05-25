from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartcrypto.state.capital_reservation_ledger import (
    CapitalReservationLedger,
    DuplicateReservationError,
    InsufficientCapitalError,
    InvalidReservationStateError,
)
from smartcrypto.state.state_repository import StateRepository, StateSafetyError


def test_reserve_capital_persists_json_runtime_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state_repository.json"
    ledger = CapitalReservationLedger(
        state_path=state_path,
        runtime_mode="paper",
        max_capital_global=100.0,
    )

    reservation = ledger.reserve(
        correlation_id="corr-1",
        client_order_id="paper-order-1",
        symbol="btcusdt",
        side="buy",
        notional=25.0,
    )

    assert reservation.status == "RESERVED"
    assert reservation.symbol == "BTCUSDT"
    assert reservation.side == "LONG"
    assert state_path.exists()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["reservations"][reservation.reservation_id]["notional"] == 25.0
    assert persisted["capital"]["reserved_notional"] == 25.0
    assert persisted["capital"]["available_notional"] == 75.0


def test_blocks_duplicate_client_order_id(tmp_path: Path) -> None:
    ledger = CapitalReservationLedger(
        state_path=tmp_path / "state.json",
        max_capital_global=100.0,
    )
    ledger.reserve(
        correlation_id="corr-1",
        client_order_id="same-client-order",
        symbol="ETHUSDT",
        side="short",
        notional=10.0,
    )

    with pytest.raises(DuplicateReservationError):
        ledger.reserve(
            correlation_id="corr-2",
            client_order_id="same-client-order",
            symbol="ETHUSDT",
            side="short",
            notional=10.0,
        )


def test_blocks_double_spend_when_available_capital_is_insufficient(tmp_path: Path) -> None:
    ledger = CapitalReservationLedger(
        state_path=tmp_path / "state.json",
        max_capital_global=50.0,
    )
    ledger.reserve(
        correlation_id="corr-1",
        client_order_id="paper-order-1",
        symbol="BTCUSDT",
        side="long",
        notional=40.0,
    )

    with pytest.raises(InsufficientCapitalError):
        ledger.reserve(
            correlation_id="corr-2",
            client_order_id="paper-order-2",
            symbol="ETHUSDT",
            side="long",
            notional=20.0,
        )


def test_release_cancel_and_reject_free_reserved_capital(tmp_path: Path) -> None:
    ledger = CapitalReservationLedger(
        state_path=tmp_path / "state.json",
        max_capital_global=30.0,
    )
    reservation = ledger.reserve(
        correlation_id="corr-1",
        client_order_id="paper-order-1",
        symbol="ETHUSDT",
        side="long",
        notional=30.0,
    )

    released = ledger.release(reservation.reservation_id, status="CANCELLED")

    assert released.status == "CANCELLED"
    assert ledger.available_capital() == 30.0
    state = ledger.state()
    assert state["capital"]["reserved_notional"] == 0.0


def test_mark_filled_keeps_capital_occupied_and_restores_after_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    ledger = CapitalReservationLedger(
        state_path=state_path,
        max_capital_global=100.0,
    )
    reservation = ledger.reserve(
        correlation_id="corr-1",
        client_order_id="paper-order-1",
        symbol="BTCUSDT",
        side="long",
        notional=60.0,
    )

    filled = ledger.mark_filled(reservation.reservation_id)
    restarted = CapitalReservationLedger(
        state_path=state_path,
        max_capital_global=100.0,
    )

    assert filled.status == "FILLED"
    assert restarted.available_capital() == 40.0
    state = restarted.state()
    assert state["reservations"][reservation.reservation_id]["status"] == "FILLED"
    assert state["positions"]["BTCUSDT:LONG"]["notional"] == 60.0


def test_cannot_transition_non_reserved_reservation_twice(tmp_path: Path) -> None:
    ledger = CapitalReservationLedger(
        state_path=tmp_path / "state.json",
        max_capital_global=100.0,
    )
    reservation = ledger.reserve(
        correlation_id="corr-1",
        client_order_id="paper-order-1",
        symbol="BTCUSDT",
        side="long",
        notional=10.0,
    )
    ledger.release(reservation.reservation_id, status="REJECTED")

    with pytest.raises(InvalidReservationStateError):
        ledger.mark_filled(reservation.reservation_id)


def test_state_repository_blocks_live_runtime_and_order_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(StateSafetyError):
        StateRepository(tmp_path / "state.json", runtime_mode="live")

    monkeypatch.setenv("ORDER_SUBMISSION_ENABLED", "true")
    with pytest.raises(StateSafetyError):
        StateRepository(tmp_path / "state.json", runtime_mode="paper").load()
