from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from smartcrypto.execution.order_manager import OrderManager, PaperOrderIntent
from smartcrypto.risk.kill_switch_guard import disabled_entry, utc_timestamp
from smartcrypto.risk.risk_manager import RiskLimits, RiskManager
from smartcrypto.state.reconciliation_guard import (
    CORRUPTED,
    DIVERGED,
    RECONCILED,
    ReconciliationGuard,
    ReconciliationGuardError,
)


def build_manager(tmp_path: Path, *, state_path: Path | None = None) -> OrderManager:
    timestamp = utc_timestamp()
    authority_path = tmp_path / "kill_switch.json"
    authority_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runtime_mode": "paper",
                "global": disabled_entry("explicit_fixture_clear", "pytest", timestamp),
                "symbols": {},
                "updated_at": timestamp,
            }
        ),
        encoding="utf-8",
    )
    return OrderManager(
        state_path=state_path or tmp_path / "state.json",
        event_log_path=tmp_path / "events.jsonl",
        kill_switch_path=authority_path,
        runtime_mode="paper",
        max_capital_global=100.0,
        risk_manager=RiskManager(
            RiskLimits(runtime_mode="paper", allowed_pairs=("BTCUSDT", "ETHUSDT"))
        ),
    )


def assert_persisted_intent(manager: OrderManager, intent: PaperOrderIntent) -> dict[str, Any]:
    assert intent.status == "PAPER_SUBMITTED"
    assert intent.reservation_id is not None
    assert manager.repository.path.is_file()
    state = json.loads(manager.repository.path.read_text(encoding="utf-8"))
    assert state["order_intents"][intent.order_intent_id] == intent.to_dict()
    reservation = state["reservations"][intent.reservation_id]
    assert reservation["client_order_id"] == intent.client_order_id
    assert reservation["status"] == "RESERVED"
    assert reservation["notional"] == pytest.approx(intent.notional)
    assert state["capital"]["reserved_notional"] == pytest.approx(intent.notional)
    return state


def build_guard(tmp_path: Path) -> ReconciliationGuard:
    return ReconciliationGuard(
        state_path=tmp_path / "state.json",
        event_log_path=tmp_path / "reconciliation_events.jsonl",
        runtime_mode="paper",
        max_capital_global=100.0,
    )


def test_reconciles_consistent_paper_state(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    intent = manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    assert_persisted_intent(manager, intent)
    before = manager.repository.path.read_bytes()
    guard = build_guard(tmp_path)

    result = guard.reconcile()

    assert result.status == RECONCILED
    assert result.block_operation is False
    assert manager.repository.path.read_bytes() == before
    assert guard.event_logger.read_events()[-1]["event_type"] == "state_reconciled"


def test_validates_filled_reservations_against_paper_filled_orders(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    intent = manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    assert_persisted_intent(manager, intent)
    filled = manager.fill_order("client-1")
    assert filled.status == "PAPER_FILLED"
    state = json.loads(manager.repository.path.read_text(encoding="utf-8"))
    assert state["order_intents"][filled.order_intent_id] == filled.to_dict()
    assert state["reservations"][filled.reservation_id]["status"] == "FILLED"
    before = manager.repository.path.read_bytes()

    result = build_guard(tmp_path).reconcile()

    assert result.status == RECONCILED
    assert manager.repository.path.read_bytes() == before


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
    state = assert_persisted_intent(manager, intent)
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
    state = assert_persisted_intent(manager, intent)
    duplicate = dict(state["reservations"][intent.reservation_id])
    duplicate["reservation_id"] = "manual-duplicate"
    state["reservations"]["manual-duplicate"] = duplicate
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = build_guard(tmp_path).reconcile()

    assert result.status == DIVERGED
    assert any("duplicate_client_order_id:reservations" in item for item in result.divergences)


def test_detects_reserved_capital_mismatch_without_auto_repair(tmp_path: Path) -> None:
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
    state = assert_persisted_intent(manager, intent)
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
    intent = manager.create_order_intent(
        correlation_id="corr-1",
        client_order_id="client-1",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    state_path = tmp_path / "state.json"
    state = assert_persisted_intent(manager, intent)
    state["capital"]["available_notional"] = 999.0
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(ReconciliationGuardError):
        build_guard(tmp_path).assert_reconciled()


def test_authorized_intent_creates_directory_and_reservation_via_real_repository(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "financial-state" / "state.json"
    manager = build_manager(tmp_path, state_path=state_path)
    assert not state_path.parent.exists()
    before_authority = manager.kill_switch_guard.state_path.read_bytes()
    intent = manager.create_order_intent(
        correlation_id="corr-real-persistence",
        client_order_id="client-real-persistence",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    state = assert_persisted_intent(manager, intent)
    assert len(state["order_intents"]) == len(state["reservations"]) == 1
    assert state["capital"]["available_notional"] == pytest.approx(80.0)
    before_state = state_path.read_bytes()
    guard = ReconciliationGuard(
        state_path=state_path,
        event_log_path=tmp_path / "reconciliation_events.jsonl",
        runtime_mode="paper",
        max_capital_global=100.0,
    )
    assert guard.reconcile().status == RECONCILED
    assert state_path.read_bytes() == before_state
    assert manager.kill_switch_guard.state_path.read_bytes() == before_authority


def test_missing_authority_blocks_intent_before_financial_state_creation(tmp_path: Path) -> None:
    state_path = tmp_path / "financial-state" / "state.json"
    manager = build_manager(tmp_path, state_path=state_path)
    manager.kill_switch_guard.state_path.unlink()
    intent = manager.create_order_intent(
        correlation_id="corr-missing-authority",
        client_order_id="client-blocked",
        symbol="BTCUSDT",
        side="long",
        notional=20.0,
        score=0.7,
    )
    assert intent.status == "RISK_REJECTED"
    assert intent.reservation_id is None
    assert intent.risk_reasons == ["kill_switch_corrupted"]
    assert not state_path.parent.exists()
    assert not manager.kill_switch_guard.state_path.exists()
    events = [event["event_type"] for event in manager.event_logger.read_events()]
    assert events == ["kill_switch_corrupted", "risk_rejected"]
