from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from smartcrypto.dashboard import freqtrade_snapshot_reader as reader


def create_freqtrade_db(path: Path) -> Path:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            create table trades (
                id integer primary key,
                pair text,
                is_open integer,
                close_profit_abs real,
                realized_profit real
            )
            """
        )
        conn.executemany(
            "insert into trades values (?, ?, ?, ?, ?)",
            [
                (1, "BTC/USDT:USDT", 0, 2.0, 2.0),
                (2, "ETH/USDT:USDT", 0, -1.0, -1.0),
                (3, "BTC/USDT:USDT", 1, 0.0, 0.0),
            ],
        )
    return path


def test_dashboard_reads_freqtrade_sqlite_via_snapshot(tmp_path) -> None:
    db_path = create_freqtrade_db(tmp_path / "tradesv3.paper.sqlite")

    state = reader.load_freqtrade_trades_snapshot([str(db_path)])

    assert state["status"] == "ok"
    assert state["db_snapshot_used"] is True
    assert state["db_path"] == str(db_path)
    assert state["db_last_read_at"]
    assert list(state["trades"]["id"]) == [3, 2, 1]


def test_dashboard_reader_passes_use_snapshot_true(monkeypatch, tmp_path) -> None:
    db_path = create_freqtrade_db(tmp_path / "tradesv3.paper.sqlite")
    calls = []

    def fake_read_trades(path, *, use_snapshot):
        calls.append((Path(path), use_snapshot))
        return pd.DataFrame({"id": [1], "is_open": [0], "close_profit_abs": [1.0]})

    monkeypatch.setattr(reader, "read_trades", fake_read_trades)

    state = reader.load_freqtrade_trades_snapshot([str(db_path)])

    assert state["status"] == "ok"
    assert state["db_snapshot_used"] is True
    assert calls == [(db_path, True)]


def test_dashboard_metrics_are_calculated_from_trades() -> None:
    trades = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "is_open": [0, 0, 1],
            "close_profit_abs": [2.0, -1.0, 0.0],
        }
    )

    metrics = reader.perf_metrics(trades)

    assert metrics["trades"] == 3
    assert metrics["closed"] == 2
    assert metrics["open"] == 1
    assert metrics["pnl"] == 1.0
    assert metrics["win_rate"] == 0.5
    assert metrics["profit_factor"] == 2.0


def test_dashboard_reader_reports_missing_database(tmp_path) -> None:
    missing = tmp_path / "missing.sqlite"

    state = reader.load_freqtrade_trades_snapshot([str(missing)])

    assert state["status"] == "missing"
    assert state["error"] == "freqtrade_db_not_found"
    assert state["db_snapshot_used"] is False
    assert state["trades"].empty


def test_dashboard_reader_reports_snapshot_error(monkeypatch, tmp_path) -> None:
    db_path = create_freqtrade_db(tmp_path / "tradesv3.paper.sqlite")

    def fake_read_trades(path, *, use_snapshot):
        raise reader.PaperTradeLifecycleError("sqlite_read_failed:snapshot:test")

    monkeypatch.setattr(reader, "read_trades", fake_read_trades)

    state = reader.load_freqtrade_trades_snapshot([str(db_path)])

    assert state["status"] == "error"
    assert "sqlite_read_failed" in state["error"]
    assert state["db_snapshot_used"] is True
    assert state["trades"].empty


def test_status_payload_is_serializable(tmp_path) -> None:
    db_path = create_freqtrade_db(tmp_path / "tradesv3.paper.sqlite")
    state = reader.load_freqtrade_trades_snapshot([str(db_path)])

    payload = reader.status_payload(state)

    assert payload["db_snapshot_used"] is True
    assert payload["db_last_read_at"]


def test_no_exchange_private_or_environment_mutation_references() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/dashboard/freqtrade_snapshot_reader.py").read_text(encoding="utf-8"),
            Path("smartcrypto/dashboard/app.py").read_text(encoding="utf-8"),
        ]
    )
    forbidden = [
        "ccxt",
        "create_order",
        "fetch_balance",
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
        ".env",
        "docker-compose",
        "START_PAPER_24H",
    ]
    assert all(token not in text for token in forbidden)
