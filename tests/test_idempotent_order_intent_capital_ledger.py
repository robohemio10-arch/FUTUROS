from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import run_order_intent_capital_ledger_audit as cli
from smartcrypto.execution.capital_reservation_ledger import (
    CapitalReservationLedger,
    CapitalReservationSafetyError,
    CapitalReservationValidationError,
    ensure_schema,
)
from smartcrypto.execution.order_intent_ledger import (
    OrderIntentLedger,
    OrderIntentSafetyError,
    OrderIntentValidationError,
    deterministic_client_order_id,
)


ROOT = Path(__file__).resolve().parents[1]


def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "order_intent_capital_ledger.sqlite"


def order_ledger(tmp_path: Path, **kwargs) -> OrderIntentLedger:
    return OrderIntentLedger(
        ledger_path(tmp_path),
        runtime_mode="paper",
        max_capital_global=kwargs.pop("max_capital_global", 500.0),
        **kwargs,
    )


def capital_ledger(tmp_path: Path, **kwargs) -> CapitalReservationLedger:
    return CapitalReservationLedger(
        ledger_path(tmp_path),
        runtime_mode="paper",
        max_capital_global=kwargs.pop("max_capital_global", 500.0),
        **kwargs,
    )


def create_intent(ledger: OrderIntentLedger, **overrides):
    payload = {
        "correlation_id": "corr-1",
        "idempotency_key": "idem-1",
        "symbol": "BTCUSDT",
        "side": "long",
        "order_type": "market",
        "requested_notional": 100.0,
        "requested_quantity": 0.01,
        "requested_price": 100000.0,
        "reserved_capital": 100.0,
        "leverage": 1.0,
        "risk_decision_id": "risk-1",
        "risk_mode": "NORMAL",
        "state_before": {"available": 500},
    }
    payload.update(overrides)
    return ledger.create_intent(**payload)


def reserve_and_submit(ledger: OrderIntentLedger, **overrides):
    intent = create_intent(ledger, **overrides)
    ledger.reserve_capital(intent.order_intent_id)
    submitted = ledger.submit_simulated(intent.order_intent_id)
    return submitted


def test_order_intent_creates_deterministic_client_order_id(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)

    intent = create_intent(ledger, idempotency_key="same-key")

    assert intent.client_order_id == deterministic_client_order_id("same-key")
    assert deterministic_client_order_id("same-key") == deterministic_client_order_id("same-key")


def test_order_intent_blocks_duplicate_idempotency_key(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    create_intent(ledger, idempotency_key="dup-key")

    with pytest.raises(OrderIntentValidationError, match="active_duplicate_idempotency_key"):
        create_intent(ledger, idempotency_key="dup-key", correlation_id="corr-2")


def test_order_intent_blocks_duplicate_client_order_id(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    create_intent(ledger, idempotency_key="key-1", client_order_id="client-same")

    with pytest.raises(OrderIntentValidationError, match="duplicate_client_order_id"):
        create_intent(ledger, idempotency_key="key-2", client_order_id="client-same")


def test_order_intent_blocks_invalid_status_transition(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    intent = create_intent(ledger)

    with pytest.raises(OrderIntentValidationError, match="invalid_status_transition"):
        ledger.transition_status(intent.order_intent_id, "FILLED")

    report = cli.run_order_intent_capital_ledger_audit(
        repository_path=ledger_path(tmp_path),
        report_path=tmp_path / "report.json",
    )
    assert report["status"] == "blocked"
    assert report["invalid_transition_findings"]


def test_order_intent_timeout_marks_dispatch_unknown(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    submitted = reserve_and_submit(ledger)

    updated = ledger.mark_timeout(submitted.order_intent_id)

    assert updated.status == "DISPATCH_UNKNOWN"
    report = cli.run_order_intent_capital_ledger_audit(repository_path=ledger_path(tmp_path), report_path=None)
    assert report["dispatch_unknown_count"] == 1
    assert report["status"] == "blocked"


def test_order_intent_dispatch_unknown_blocks_same_symbol_side(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    submitted = reserve_and_submit(ledger, idempotency_key="timeout-key")
    ledger.mark_timeout(submitted.order_intent_id)

    with pytest.raises(OrderIntentValidationError, match="dispatch_unknown_blocks_symbol_side"):
        create_intent(ledger, idempotency_key="new-key", symbol="BTCUSDT", side="long")


def test_capital_ledger_reserves_before_submit(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    intent = create_intent(ledger)

    with pytest.raises(OrderIntentValidationError, match="capital_must_be_reserved_before_simulated_submit"):
        ledger.submit_simulated(intent.order_intent_id)

    reserved = ledger.reserve_capital(intent.order_intent_id)
    submitted = ledger.submit_simulated(reserved.order_intent_id)

    assert reserved.status == "CAPITAL_RESERVED"
    assert submitted.status == "SIMULATED_SUBMITTED"


def test_capital_ledger_blocks_negative_or_zero_reservation(tmp_path: Path) -> None:
    ledger = capital_ledger(tmp_path)

    with pytest.raises(CapitalReservationValidationError):
        ledger.reserve(order_intent_id="intent", client_order_id="client", idempotency_key="key", symbol="BTCUSDT", reserved_amount=0)
    with pytest.raises(CapitalReservationValidationError):
        ledger.reserve(order_intent_id="intent", client_order_id="client", idempotency_key="key", symbol="BTCUSDT", reserved_amount=-1)


def test_capital_ledger_blocks_over_consumption(tmp_path: Path) -> None:
    ledger = capital_ledger(tmp_path)
    reservation = ledger.reserve(order_intent_id="intent", client_order_id="client", idempotency_key="key", symbol="BTCUSDT", reserved_amount=50)

    with pytest.raises(CapitalReservationValidationError, match="consume_amount_exceeds_remaining_reserve"):
        ledger.consume(reservation.reservation_id, 60)


def test_capital_ledger_adjusts_on_partial_fill(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    submitted = reserve_and_submit(ledger)

    updated = ledger.partial_fill(submitted.order_intent_id, consumed_amount=40)
    reservation = ledger.capital_ledger.get_by_order_intent_id(submitted.order_intent_id)

    assert updated.status == "PARTIALLY_FILLED"
    assert reservation is not None
    assert reservation.status == "PARTIALLY_CONSUMED"
    assert reservation.consumed_amount == 40
    assert reservation.released_amount == 0


def test_capital_ledger_releases_on_cancel(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    submitted = reserve_and_submit(ledger)

    cancelled = ledger.cancel(submitted.order_intent_id)
    reservation = ledger.capital_ledger.get_by_order_intent_id(submitted.order_intent_id)

    assert cancelled.status == "CANCELLED"
    assert reservation is not None
    assert reservation.status == "CANCELLED_RELEASED"
    assert reservation.released_amount == reservation.reserved_amount


def test_capital_ledger_releases_on_reject(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    intent = create_intent(ledger)
    ledger.reserve_capital(intent.order_intent_id)

    rejected = ledger.reject(intent.order_intent_id, reason="risk_rejected")
    reservation = ledger.capital_ledger.get_by_order_intent_id(intent.order_intent_id)

    assert rejected.status == "REJECTED"
    assert reservation is not None
    assert reservation.status == "REJECTED_RELEASED"
    assert reservation.released_amount == reservation.reserved_amount


def test_capital_ledger_consumes_on_full_fill(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    submitted = reserve_and_submit(ledger)
    ledger.acknowledge_simulated(submitted.order_intent_id)

    filled = ledger.fill(submitted.order_intent_id)
    reservation = ledger.capital_ledger.get_by_order_intent_id(submitted.order_intent_id)

    assert filled.status == "FILLED"
    assert reservation is not None
    assert reservation.status == "CONSUMED"
    assert reservation.consumed_amount == reservation.reserved_amount


def test_capital_ledger_blocks_double_spend(tmp_path: Path) -> None:
    ledger = capital_ledger(tmp_path)
    ledger.reserve(order_intent_id="intent-1", client_order_id="client-1", idempotency_key="same", symbol="BTCUSDT", reserved_amount=50)

    with pytest.raises(CapitalReservationValidationError, match="active_reservation_duplicate_idempotency_key"):
        ledger.reserve(order_intent_id="intent-2", client_order_id="client-2", idempotency_key="same", symbol="BTCUSDT", reserved_amount=50)


def test_ledger_blocks_unsafe_safety_flags(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)

    with pytest.raises(OrderIntentSafetyError):
        create_intent(ledger, safety_overrides={"live_trading_enabled": True})

    reservations = capital_ledger(tmp_path)
    with pytest.raises(CapitalReservationSafetyError):
        reservations.reserve(
            order_intent_id="intent",
            client_order_id="client",
            idempotency_key="key",
            symbol="BTCUSDT",
            reserved_amount=50,
            safety_overrides={"exchange_private_access": True},
        )


def test_cli_run_order_intent_capital_ledger_audit_runs_successfully(tmp_path: Path, capsys) -> None:
    ledger = order_ledger(tmp_path)
    intent = create_intent(ledger)
    ledger.reserve_capital(intent.order_intent_id)
    report_path = tmp_path / "reports" / "ledger_report.json"

    rc = cli.main([
        "--repository",
        str(ledger_path(tmp_path)),
        "--report",
        str(report_path),
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "ok"
    assert payload["repository_state"] == "repository_present_with_valid_events"
    assert payload["evidence_quality_summary"]["operational_evidence_complete"] is True
    assert payload["order_intents_count"] == 1
    assert payload["capital_reservations_count"] == 1
    assert report_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    create_intent(ledger)
    training_dataset = tmp_path / "data" / "features" / "training_dataset.parquet"
    trades_master = tmp_path / "data" / "trades" / "trades_master.xlsx"
    training_dataset.parent.mkdir(parents=True, exist_ok=True)
    trades_master.parent.mkdir(parents=True, exist_ok=True)
    training_dataset.write_text("training-stays", encoding="utf-8")
    trades_master.write_text("master-stays", encoding="utf-8")

    report = cli.run_order_intent_capital_ledger_audit(repository_path=ledger_path(tmp_path), report_path=tmp_path / "report.json")

    assert report["status"] == "ok"
    assert training_dataset.read_text(encoding="utf-8") == "training-stays"
    assert trades_master.read_text(encoding="utf-8") == "master-stays"


def test_does_not_touch_freqtrade_db_registry_models_or_signal_producer(tmp_path: Path) -> None:
    ledger = order_ledger(tmp_path)
    create_intent(ledger)
    protected = {
        tmp_path / "freqtrade" / "user_data" / "tradesv3.paper.sqlite": "freqtrade-db-stays",
        tmp_path / "data" / "models" / "registry" / "model_registry.json": "registry-stays",
        tmp_path / "data" / "models" / "shadow" / "model.joblib": "model-stays",
        tmp_path / "scripts" / "phase13_generate_active_signals.py": "signal-producer-stays",
    }
    for path, content in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    report = cli.run_order_intent_capital_ledger_audit(repository_path=ledger_path(tmp_path), report_path=tmp_path / "report.json")

    assert report["status"] == "ok"
    for path, content in protected.items():
        assert path.read_text(encoding="utf-8") == content


def test_never_sends_orders_or_accesses_exchange() -> None:
    combined = (
        (ROOT / "smartcrypto" / "execution" / "order_intent_ledger.py").read_text(encoding="utf-8")
        + (ROOT / "smartcrypto" / "execution" / "capital_reservation_ledger.py").read_text(encoding="utf-8")
        + (ROOT / "scripts" / "run_order_intent_capital_ledger_audit.py").read_text(encoding="utf-8")
    ).lower()
    for forbidden in ("ccxt", "freqtradeapi", "create_order(", "fetch_balance", "private_get"):
        assert forbidden not in combined


def test_audit_detects_manual_double_spend_and_over_consumption(tmp_path: Path) -> None:
    ledger = capital_ledger(tmp_path)
    reservation = ledger.reserve(order_intent_id="intent", client_order_id="client", idempotency_key="key", symbol="BTCUSDT", reserved_amount=50)
    with sqlite3.connect(ledger_path(tmp_path)) as connection:
        connection.execute(
            """
            UPDATE capital_reservations
            SET consumed_amount = 60, released_amount = 1
            WHERE reservation_id = ?
            """,
            (reservation.reservation_id,),
        )

    report = cli.run_order_intent_capital_ledger_audit(repository_path=ledger_path(tmp_path), report_path=None)

    assert report["status"] == "blocked"
    assert report["over_consumption_findings"]


def test_ledger_audit_differentiates_missing_repository(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite"

    report = cli.run_order_intent_capital_ledger_audit(repository_path=missing, report_path=None)

    assert report["status"] == "missing_data"
    assert report["reason"] == "repository_missing"
    assert report["repository_state"] == "repository_missing"
    assert str(missing) in report["required_sources_missing"]
    assert report["evidence_quality_summary"]["operational_evidence_complete"] is False
    assert "initialize_empty_ledger_repository_if_runtime_needs_materialization" in report["next_required_actions"]


def test_ledger_audit_differentiates_empty_repository_file(tmp_path: Path) -> None:
    empty = ledger_path(tmp_path)
    empty.touch()

    report = cli.run_order_intent_capital_ledger_audit(repository_path=empty, report_path=None)

    assert report["status"] == "missing_data"
    assert report["reason"] == "repository_empty"
    assert report["repository_state"] == "repository_empty"
    assert report["evidence_quality_summary"]["has_schema"] is False
    assert report["evidence_quality_summary"]["has_real_events"] is False


def test_ledger_audit_does_not_treat_empty_structure_as_operational_evidence(tmp_path: Path) -> None:
    path = ledger_path(tmp_path)
    ensure_schema(path)

    report = cli.run_order_intent_capital_ledger_audit(repository_path=path, report_path=None)

    assert report["status"] == "warning"
    assert report["reason"] == "repository_present_but_no_events"
    assert report["repository_state"] == "repository_present_but_no_events"
    assert report["recommended_mode"] == "NORMAL"
    assert report["evidence_quality_summary"]["operational_evidence_complete"] is False
    assert "wait_for_real_paper_order_intent_or_capital_reservation_event" in report["next_required_actions"]


def test_ledger_audit_blocks_core_schema_invalid(tmp_path: Path) -> None:
    path = ledger_path(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE order_intents (order_intent_id TEXT)")

    report = cli.run_order_intent_capital_ledger_audit(repository_path=path, report_path=None)

    assert report["status"] == "blocked"
    assert report["reason"] == "schema_invalid"
    assert report["schema_findings"]
    assert report["evidence_quality_summary"]["operational_evidence_complete"] is False


def test_ledger_audit_blocks_event_schema_invalid(tmp_path: Path) -> None:
    path = ledger_path(tmp_path)
    ensure_schema(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE order_intent_events")

    report = cli.run_order_intent_capital_ledger_audit(repository_path=path, report_path=None)

    assert report["status"] == "blocked"
    assert report["reason"] == "event_schema_invalid"
    assert report["event_schema_findings"] == ["missing_event_table:order_intent_events"]


def test_ledger_initializes_empty_repository_without_fake_events(tmp_path: Path) -> None:
    path = ledger_path(tmp_path)

    report = cli.run_order_intent_capital_ledger_audit(
        repository_path=path,
        report_path=None,
        initialize_empty_repository=True,
    )

    assert path.exists()
    assert report["status"] == "warning"
    assert report["repository_materialized"] is True
    assert report["repository_state"] == "repository_present_but_no_events"
    assert report["order_intents_count"] == 0
    assert report["capital_reservations_count"] == 0
    assert report["evidence_quality_summary"]["operational_evidence_complete"] is False
