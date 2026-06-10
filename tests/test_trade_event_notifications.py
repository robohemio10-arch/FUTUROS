from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from smartcrypto.ops.trade_event_notifications import (
    WATCHED_PAIRS,
    dispatch_trade_events,
    load_trade_events,
    run_trade_event_notification_scan,
)


class FakeResponse:
    status = 200

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return b"ok"


def fake_urlopen(_request: Any, _timeout: float) -> FakeResponse:
    return FakeResponse()


def telegram_env() -> dict[str, str]:
    return {
        "SMARTCRYPTO_TELEGRAM_ENABLED": "true",
        "SMARTCRYPTO_TELEGRAM_BOT_TOKEN": "123456:SECRET_TOKEN",
        "SMARTCRYPTO_TELEGRAM_CHAT_ID": "987654321",
        "SMARTCRYPTO_TELEGRAM_API_BASE_URL": "https://api.telegram.org",
        "SMARTCRYPTO_TELEGRAM_PARSE_MODE": "",
        "SMARTCRYPTO_TELEGRAM_DISABLE_NOTIFICATION": "false",
        "SMARTCRYPTO_NOTIFICATION_TIMEOUT_SECONDS": "10",
    }


def create_trade_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                pair TEXT NOT NULL,
                is_open INTEGER NOT NULL,
                is_short INTEGER NOT NULL,
                open_date TEXT,
                close_date TEXT,
                open_rate REAL,
                close_rate REAL,
                stake_amount REAL,
                close_profit_abs REAL,
                exit_reason TEXT
            )
            """
        )
        rows = [
            (
                1,
                "BTC/USDT:USDT",
                1,
                1,
                "2026-06-10T10:00:00+00:00",
                None,
                65000.0,
                None,
                30.0,
                None,
                None,
            ),
            (
                2,
                "ETH/USDT:USDT",
                0,
                0,
                "2026-06-10T11:00:00+00:00",
                "2026-06-10T12:00:00+00:00",
                3500.0,
                3520.0,
                25.0,
                1.25,
                "roi",
            ),
            (
                3,
                "SOL/USDT:USDT",
                0,
                0,
                "2026-06-10T11:00:00+00:00",
                "2026-06-10T12:00:00+00:00",
                150.0,
                151.0,
                10.0,
                0.1,
                "ignored",
            ),
        ]
        connection.executemany(
            """
            INSERT INTO trades (
                id,
                pair,
                is_open,
                is_short,
                open_date,
                close_date,
                open_rate,
                close_rate,
                stake_amount,
                close_profit_abs,
                exit_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()
    return path


def test_load_trade_events_detects_open_and_close_for_watched_pairs(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")

    events = load_trade_events(db)

    assert {event.event_type for event in events} == {"OPEN_SHORT", "OPEN_LONG", "CLOSE_LONG"}
    assert {event.pair for event in events} == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
    assert all(event.binance_futures_url == WATCHED_PAIRS[event.pair] for event in events)
    assert all("SOL" not in event.pair for event in events)


def test_dispatch_dry_run_does_not_persist_by_default(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")
    state_db = tmp_path / "state.sqlite"
    events = load_trade_events(db)

    report = dispatch_trade_events(
        events,
        state_db_path=state_db,
        dry_run=True,
        env=telegram_env(),
    )

    assert report["status"] == "ok"
    assert report["dry_run"] is True
    assert report["events_detected"] == 3
    assert report["events_dispatched"] == 3
    assert report["events_marked_sent"] == 0
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["exchange_private_access"] is False


def test_dispatch_real_persists_idempotency_and_avoids_duplicates(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")
    state_db = tmp_path / "state.sqlite"
    events = load_trade_events(db)

    first = dispatch_trade_events(
        events,
        state_db_path=state_db,
        dry_run=False,
        env=telegram_env(),
        telegram_opener=fake_urlopen,
    )
    second = dispatch_trade_events(
        events,
        state_db_path=state_db,
        dry_run=False,
        env=telegram_env(),
        telegram_opener=fake_urlopen,
    )

    assert first["events_dispatched"] == 3
    assert first["events_marked_sent"] == 3
    assert second["events_pending"] == 0
    assert second["events_dispatched"] == 0


def test_run_scan_writes_report_and_keeps_secrets_out(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")
    report_path = tmp_path / "report.json"

    report = run_trade_event_notification_scan(
        source_db_path=db,
        state_db_path=tmp_path / "state.sqlite",
        report_path=report_path,
        dry_run=True,
        env=telegram_env(),
    )

    assert report["status"] == "ok"
    assert report_path.exists()

    serialized = report_path.read_text(encoding="utf-8")
    assert "SECRET_TOKEN" not in serialized
    assert "987654321" not in serialized
    assert "sends_orders" in serialized


def test_missing_source_db_raises_without_private_exchange_access(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite"

    try:
        run_trade_event_notification_scan(source_db_path=missing)
    except FileNotFoundError as exc:
        assert "source_db_not_found" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")
