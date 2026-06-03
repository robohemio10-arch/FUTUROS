from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts import run_state_reconciliation_audit as cli
from smartcrypto.state.reconciliation_guard import run_state_reconciliation_audit
from smartcrypto.state.state_repository import StateRepository


ROOT = Path(__file__).resolve().parents[1]


def repository(tmp_path: Path) -> StateRepository:
    return StateRepository(tmp_path / "state_repository.sqlite", runtime_mode="paper", max_capital_global=1000)


def create_intent(repo: StateRepository, **overrides) -> dict:
    payload = {
        "correlation_id": "corr-1",
        "client_order_id": "client-1",
        "symbol": "BTCUSDT",
        "side": "long",
        "order_type": "market",
        "requested_notional": 100.0,
        "requested_quantity": 0.01,
        "reserved_capital": 100.0,
        "risk_decision_id": "risk-1",
        "state_before": {"available": 1000},
    }
    payload.update(overrides)
    return repo.create_order_intent(**payload)


def audit(tmp_path: Path, *, snapshot: Path | None = None, safety_overrides: dict | None = None) -> dict:
    return run_state_reconciliation_audit(
        repository_path=tmp_path / "state_repository.sqlite",
        snapshot_path=snapshot,
        report_path=tmp_path / "reports" / "state_reconciliation_audit_report.json",
        safety_overrides=safety_overrides,
    )


def test_state_repository_creates_order_intent_with_correlation_and_client_id(tmp_path: Path) -> None:
    repo = repository(tmp_path)

    intent = create_intent(repo, correlation_id="corr-123", client_order_id="client-123")

    assert intent["correlation_id"] == "corr-123"
    assert intent["client_order_id"] == "client-123"
    assert intent["order_intent_id"]
    assert intent["paper_only"] is True
    assert intent["shadow_only"] is True
    assert intent["live_trading_enabled"] is False
    assert intent["order_submission_enabled"] is False


def test_state_repository_reserves_capital_before_order_intent(tmp_path: Path) -> None:
    repo = repository(tmp_path)

    intent = create_intent(repo, client_order_id="client-reserve")
    reservation = repo.get_reservation("client-reserve")

    assert reservation is not None
    assert reservation["client_order_id"] == "client-reserve"
    assert reservation["status"] == "RESERVED"
    assert reservation["reserved_capital"] == 100.0
    assert intent["reservation_id"] == reservation["reservation_id"]


def test_state_repository_releases_reserve_on_cancel(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    create_intent(repo, client_order_id="client-cancel")

    reservation = repo.cancel_order("client-cancel")

    assert reservation["status"] == "RELEASED"
    assert reservation["remaining_reserved_capital"] == 0


def test_state_repository_releases_reserve_on_reject(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    create_intent(repo, client_order_id="client-reject")

    reservation = repo.reject_order("client-reject")

    assert reservation["status"] == "RELEASED"
    assert reservation["remaining_reserved_capital"] == 0


def test_state_repository_adjusts_reserve_on_partial_fill(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    intent = create_intent(repo, client_order_id="client-partial", reserved_capital=120.0, requested_notional=120.0)
    repo.create_simulated_order(order_intent_id=intent["order_intent_id"], client_order_id="client-partial")

    reservation = repo.adjust_reserve_on_partial_fill(client_order_id="client-partial", filled_notional=45.0)

    assert reservation["status"] == "PARTIAL_FILLED"
    assert reservation["filled_notional"] == 45.0
    assert reservation["remaining_reserved_capital"] == 75.0
    assert audit(tmp_path)["partial_fill_inconsistency_count"] == 0


def test_state_repository_marks_dispatch_unknown_on_timeout(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    intent = create_intent(repo, client_order_id="client-timeout")
    repo.create_simulated_order(order_intent_id=intent["order_intent_id"], client_order_id="client-timeout")

    updated = repo.mark_dispatch_unknown_on_timeout("client-timeout")

    assert updated["status"] == "DISPATCH_UNKNOWN"
    report = audit(tmp_path)
    assert report["status"] == "blocked"
    assert report["dispatch_unknown_count"] == 1


def test_reconciliation_blocks_when_dispatch_unknown_exists(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    intent = create_intent(repo, client_order_id="client-dispatch")
    repo.create_simulated_order(order_intent_id=intent["order_intent_id"], client_order_id="client-dispatch")
    repo.mark_dispatch_unknown_on_timeout("client-dispatch")

    report = audit(tmp_path)

    assert report["status"] == "blocked"
    assert report["reconciliation_required"] is True
    assert report["recommended_mode"] == "RECONCILING"
    assert "dispatch_unknown_active" in report["blocking_findings"]


def test_reconciliation_blocks_duplicate_client_order_id(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    original = create_intent(repo, client_order_id="dup-client")
    duplicate = dict(original)
    duplicate["order_intent_id"] = "manual-duplicate"
    with sqlite3.connect(repo.path) as connection:
        columns = list(duplicate)
        connection.execute(
            f"INSERT INTO order_intents ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [int(value) if isinstance(value, bool) else value for value in duplicate.values()],
        )

    report = audit(tmp_path)

    assert report["status"] == "blocked"
    assert report["duplicate_client_order_id_count"] == 1
    assert "duplicate_client_order_id_detected" in report["blocking_findings"]


def test_reconciliation_blocks_negative_reserved_capital(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    create_intent(repo, client_order_id="negative-reserve")
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "UPDATE capital_reservations SET reserved_capital = -1 WHERE client_order_id = ?",
            ("negative-reserve",),
        )

    report = audit(tmp_path)

    assert report["status"] == "blocked"
    assert report["negative_reserved_capital_count"] == 1
    assert "negative_reserved_capital_detected" in report["blocking_findings"]


def test_reconciliation_blocks_position_quantity_divergence(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.upsert_position(symbol="BTCUSDT", side="long", quantity=1.0)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"positions": [{"symbol": "BTCUSDT", "side": "long", "quantity": 2.0}]}), encoding="utf-8")

    report = audit(tmp_path, snapshot=snapshot)

    assert report["status"] == "blocked"
    assert report["state_divergence_count"] == 1
    assert any(item.startswith("position_quantity_divergence") for item in report["blocking_findings"])


def test_reconciliation_blocks_position_side_divergence(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.upsert_position(symbol="ETHUSDT", side="long", quantity=1.0)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"positions": [{"symbol": "ETHUSDT", "side": "short", "quantity": 1.0}]}), encoding="utf-8")

    report = audit(tmp_path, snapshot=snapshot)

    assert report["status"] == "blocked"
    assert report["state_divergence_count"] == 2
    assert report["recommended_mode"] == "RECONCILING"


def test_reconciliation_enters_reconciling_on_state_divergence(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.upsert_position(symbol="BTCUSDT", side="long", quantity=1.0, state_hash="local")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"positions": [{"symbol": "BTCUSDT", "side": "long", "quantity": 1.0, "state_hash": "external"}]}), encoding="utf-8")

    report = audit(tmp_path, snapshot=snapshot)

    assert report["status"] == "blocked"
    assert report["recommended_mode"] == "RECONCILING"
    assert any(item.startswith("state_hash_divergence") for item in report["blocking_findings"])


def test_reconciliation_accepts_clean_state(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.upsert_position(symbol="BTCUSDT", side="long", quantity=1.0, state_hash="same")
    create_intent(repo, client_order_id="clean-client")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"positions": [{"symbol": "BTCUSDT", "side": "long", "quantity": 1.0, "state_hash": "same"}]}), encoding="utf-8")

    report = audit(tmp_path, snapshot=snapshot)

    assert report["status"] == "ok"
    assert report["reason"] == "state_reconciled"
    assert report["reconciliation_required"] is False
    assert report["recommended_mode"] == "NORMAL"


def test_reconciliation_blocks_unsafe_safety_flags(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    intent = create_intent(repo, client_order_id="unsafe-client")
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "UPDATE order_intents SET live_trading_enabled = 1 WHERE order_intent_id = ?",
            (intent["order_intent_id"],),
        )

    report = audit(tmp_path, safety_overrides={"exchange_private_access": True})

    assert report["status"] == "blocked"
    assert "unsafe_safety_flag:exchange_private_access" in report["blocking_findings"]
    assert report["exchange_private_access"] is True


def test_cli_run_state_reconciliation_audit_runs_successfully(tmp_path: Path, capsys) -> None:
    repo = repository(tmp_path)
    repo.upsert_position(symbol="BTCUSDT", side="long", quantity=1.0)
    report_path = tmp_path / "reports" / "state_reconciliation.json"

    rc = cli.main([
        "--repository",
        str(repo.path),
        "--report",
        str(report_path),
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "ok"
    assert payload["repository_path"] == str(repo.path)
    assert report_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    create_intent(repo)
    training_dataset = tmp_path / "data" / "features" / "training_dataset.parquet"
    trades_master = tmp_path / "data" / "trades" / "trades_master.xlsx"
    training_dataset.parent.mkdir(parents=True, exist_ok=True)
    trades_master.parent.mkdir(parents=True, exist_ok=True)
    training_dataset.write_text("training-stays", encoding="utf-8")
    trades_master.write_text("master-stays", encoding="utf-8")

    report = audit(tmp_path)

    assert report["status"] == "ok"
    assert training_dataset.read_text(encoding="utf-8") == "training-stays"
    assert trades_master.read_text(encoding="utf-8") == "master-stays"


def test_does_not_touch_freqtrade_db_registry_models_or_signal_producer(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    create_intent(repo)
    protected = {
        tmp_path / "freqtrade" / "user_data" / "tradesv3.paper.sqlite": "freqtrade-db-stays",
        tmp_path / "data" / "models" / "registry" / "model_registry.json": "registry-stays",
        tmp_path / "data" / "models" / "shadow" / "model.joblib": "model-stays",
        tmp_path / "scripts" / "phase13_generate_active_signals.py": "signal-producer-stays",
    }
    for path, content in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    report = audit(tmp_path)

    assert report["status"] == "ok"
    for path, content in protected.items():
        assert path.read_text(encoding="utf-8") == content

    module_text = (
        (ROOT / "smartcrypto" / "state" / "state_repository.py").read_text(encoding="utf-8")
        + (ROOT / "smartcrypto" / "state" / "reconciliation_guard.py").read_text(encoding="utf-8")
        + (ROOT / "scripts" / "run_state_reconciliation_audit.py").read_text(encoding="utf-8")
    ).lower()
    for forbidden in ("ccxt", "freqtradeapi", "create_order(", "fetch_balance"):
        assert forbidden not in module_text
