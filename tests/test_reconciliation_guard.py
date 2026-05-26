from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartcrypto.execution.order_manager import OrderManager
from smartcrypto.risk.risk_manager import RiskLimits, RiskManager
from smartcrypto.state.reconciliation_guard import (
    CORRUPTED,
    DIVERGED,
    RECONCILED,
    ReconciliationGuard,
    ReconciliationGuardError,
)


def build_manager(tmp_path: Path) -> OrderManager:
    return OrderManager(
        state_path=tmp_path / "state.json",
        event_log_path=tmp_path / "events.jsonl",
        runtime_mode="paper",
        max_capital_global=100.0,
        risk_manager=RiskManager(
            RiskLimits(runtime_mode="paper", allowed_pairs=("BTCUSDT", "ETHUSDT"))
        ),
    )


def build_guard(tmp_path: Path) -> ReconciliationGuard:
    return ReconciliationGuard(
        state_path=tmp_path / "state.json",
        event_log_path=tmp_path / "reconciliation_events.jsonl",
        runtime_mode="paper",
        max_capital_global=100.0,
    )


def test_reconciles_consistent_paper_state(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    guard = build_guard(tmp_path)

    result = guard.reconcile()

    assert result.status == RECONCILED
    assert result.block_operation is False
    assert guard.event_logger.read_events()[-1]["event_type"] == "state_reconciled"


def test_validates_filled_reservations_against_paper_filled_orders(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    manager.fill_order("client-1")

    result = build_guard(tmp_path).reconcile()

    assert result.status == RECONCILED


def test_detects_filled_reservation_without_paper_filled_order(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    intent = manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["reservations"][intent.reservation_id]["status"] = "FILLED"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = build_guard(tmp_path).reconcile()

    assert result.status == DIVERGED
    assert result.block_operation is True
    assert any("filled_reservation_order_status_mismatch" in item for item in result.divergences)


def test_detects_duplicate_client_order_id(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    intent = manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    duplicate = dict(state["reservations"][intent.reservation_id])
    duplicate["reservation_id"] = "manual-duplicate"
    state["reservations"]["manual-duplicate"] = duplicate
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = build_guard(tmp_path).reconcile()

    assert result.status == DIVERGED
    assert any("duplicate_client_order_id:reservations" in item for item in result.divergences)


def test_detects_reserved_capital_mismatch_without_auto_repair(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["capital"]["reserved_notional"] = 1.0
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = build_guard(tmp_path).reconcile()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))

    assert result.status == DIVERGED
    assert any("reserved_notional_mismatch" in item for item in result.divergences)
    assert persisted["capital"]["reserved_notional"] == 1.0


def test_detects_corrupted_financial_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("{bad-json", encoding="utf-8")

    result = build_guard(tmp_path).reconcile()

    assert result.status == CORRUPTED
    assert result.block_operation is True
    assert build_guard(tmp_path).event_logger.read_events()[-1]["event_type"] == (
        "reconciliation_failed"
    )


def test_assert_reconciled_blocks_operation_on_divergence(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["capital"]["available_notional"] = 999.0
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(ReconciliationGuardError):
        build_guard(tmp_path).assert_reconciled()
