from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from smartcrypto.ops.trade_event_notifications import (
    WATCHED_PAIRS,
    baseline_trade_events,
    dispatch_trade_events,
    load_trade_events,
    run_trade_event_notification_daemon,
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


def notification_env() -> dict[str, str]:
    return {
        "SMARTCRYPTO_NTFY_ENABLED": "true",
        "SMARTCRYPTO_NTFY_TOPIC": "dummy-topic",
        "SMARTCRYPTO_NTFY_SERVER_URL": "https://ntfy.sh",
        "SMARTCRYPTO_NTFY_TOKEN": "dummy-ntfy-token",
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


def channel_result(report: dict[str, Any], channel: str) -> dict[str, Any]:
    results = report["dispatches"][0]["results"]
    matches = [row for row in results if row["channel"] == channel]
    assert matches
    return matches[0]


def test_load_trade_events_detects_open_and_close_for_watched_pairs(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")

    events = load_trade_events(db)

    assert {event.event_type for event in events} == {"OPEN_SHORT", "OPEN_LONG", "CLOSE_LONG"}
    assert {event.pair for event in events} == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
    assert all(event.binance_futures_url == WATCHED_PAIRS[event.pair] for event in events)
    assert all("SOL" not in event.pair for event in events)


def test_dispatch_dry_run_telegram_only_does_not_persist_by_default(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")
    state_db = tmp_path / "state.sqlite"
    events = load_trade_events(db)

    report = dispatch_trade_events(
        events,
        state_db_path=state_db,
        dry_run=True,
        env=notification_env(),
        channels="telegram",
    )

    assert report["status"] == "ok"
    assert report["channels"] == "telegram"
    assert report["telegram_only"] is True
    assert report["dry_run"] is True
    assert report["events_detected"] == 3
    assert report["events_dispatched"] == 3
    assert report["events_marked_sent"] == 0
    assert channel_result(report, "telegram")["status"] == "sent"
    assert channel_result(report, "ntfy")["status"] == "disabled"
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["exchange_private_access"] is False


def test_dispatch_dry_run_ntfy_only_requires_ntfy(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")
    events = load_trade_events(db)

    report = dispatch_trade_events(
        events,
        state_db_path=tmp_path / "state.sqlite",
        dry_run=True,
        env=notification_env(),
        channels="ntfy",
        limit=1,
    )

    assert report["status"] == "ok"
    assert report["channels"] == "ntfy"
    assert report["events_dispatched"] == 1
    assert report["events_marked_sent"] == 0
    assert channel_result(report, "ntfy")["status"] == "sent"
    assert channel_result(report, "telegram")["status"] == "disabled"


def test_dispatch_real_all_channels_requires_ntfy_and_telegram_before_persisting(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")
    state_db = tmp_path / "state.sqlite"
    events = load_trade_events(db)

    first = dispatch_trade_events(
        events,
        state_db_path=state_db,
        dry_run=False,
        env=notification_env(),
        channels="all",
        ntfy_opener=fake_urlopen,
        telegram_opener=fake_urlopen,
    )
    second = dispatch_trade_events(
        events,
        state_db_path=state_db,
        dry_run=False,
        env=notification_env(),
        channels="all",
        ntfy_opener=fake_urlopen,
        telegram_opener=fake_urlopen,
    )

    assert first["status"] == "ok"
    assert first["channels"] == "all"
    assert first["events_dispatched"] == 3
    assert first["events_marked_sent"] == 3
    assert channel_result(first, "ntfy")["status"] == "sent"
    assert channel_result(first, "telegram")["status"] == "sent"
    assert second["events_pending"] == 0
    assert second["events_dispatched"] == 0


def test_dispatch_all_blocks_when_one_required_channel_is_disabled(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")
    events = load_trade_events(db)
    env = notification_env()
    env["SMARTCRYPTO_NTFY_ENABLED"] = "false"

    report = dispatch_trade_events(
        events,
        state_db_path=tmp_path / "state.sqlite",
        dry_run=True,
        env=env,
        channels="all",
        limit=1,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "required_channel_delivery_blocked_or_failed"
    assert report["events_marked_sent"] == 0
    assert channel_result(report, "ntfy")["status"] == "disabled"
    assert channel_result(report, "telegram")["status"] == "sent"


def test_baseline_marks_history_without_dispatching(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")
    state_db = tmp_path / "state.sqlite"
    events = load_trade_events(db)

    report = baseline_trade_events(events, state_db_path=state_db)
    after = dispatch_trade_events(
        events,
        state_db_path=state_db,
        dry_run=False,
        env=notification_env(),
        channels="all",
        ntfy_opener=fake_urlopen,
        telegram_opener=fake_urlopen,
    )

    assert report["status"] == "ok"
    assert report["baseline"] is True
    assert report["events_baselined"] == 3
    assert report["events_dispatched"] == 0
    assert after["events_pending"] == 0
    assert after["events_dispatched"] == 0


def test_run_scan_writes_report_and_keeps_secrets_out(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")
    report_path = tmp_path / "report.json"

    report = run_trade_event_notification_scan(
        source_db_path=db,
        state_db_path=tmp_path / "state.sqlite",
        report_path=report_path,
        dry_run=True,
        env=notification_env(),
        channels="all",
        limit=1,
    )

    assert report["status"] == "ok"
    assert report_path.exists()

    serialized = report_path.read_text(encoding="utf-8")
    assert "SECRET_TOKEN" not in serialized
    assert "987654321" not in serialized
    assert "sends_orders" in serialized
    assert "changes_risk" in serialized


def test_daemon_single_iteration_writes_report(tmp_path: Path) -> None:
    db = create_trade_db(tmp_path / "trades.sqlite")
    report_path = tmp_path / "daemon_report.json"

    report = run_trade_event_notification_daemon(
        source_db_path=db,
        state_db_path=tmp_path / "state.sqlite",
        report_path=report_path,
        dry_run=True,
        env=notification_env(),
        channels="telegram",
        limit=1,
        poll_seconds=1,
        max_iterations=1,
    )

    assert report["status"] == "ok"
    assert report["daemon"] is True
    assert report["daemon_iteration"] == 1
    assert report_path.exists()
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["daemon"] is True


def test_missing_source_db_raises_without_private_exchange_access(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite"

    try:
        run_trade_event_notification_scan(source_db_path=missing)
    except FileNotFoundError as exc:
        assert "source_db_not_found" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")
