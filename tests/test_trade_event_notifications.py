from __future__ import annotations

import io
import json
import sqlite3
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from smartcrypto.ops.trade_event_notifications import (
    WATCHED_PAIRS,
    baseline_trade_events,
    dispatch_trade_events,
    ensure_state_db,
    load_completed_event_keys,
    load_delivered_channels,
    load_trade_events,
    run_trade_event_notification_daemon,
    run_trade_event_notification_scan,
)


class FakeResponse:
    def __init__(self, *, status: int = 200, body: bytes = b"ok") -> None:
        self.status = status
        self.code = status
        self._body = body

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class CountingOpener:
    def __init__(self, *, status: int = 200, body: bytes = b"ok", name: str = "opener") -> None:
        self.status = status
        self.body = body
        self.name = name
        self.calls = 0

    def __call__(self, request: Any, timeout: float) -> FakeResponse:
        assert timeout > 0
        assert request is not None
        self.calls += 1
        return FakeResponse(status=self.status, body=self.body)


class FailingIfCalledOpener:
    def __call__(self, request: Any, timeout: float) -> FakeResponse:
        raise AssertionError(f"unexpected opener call: {request=} {timeout=}")


def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
    assert request is not None
    assert timeout > 0
    return FakeResponse(status=200, body=b"ok")


def failing_urlopen(request: Any, timeout: float) -> FakeResponse:
    assert request is not None
    assert timeout > 0
    return FakeResponse(status=500, body=b"fail")


def http_error_urlopen(request: Any, timeout: float) -> FakeResponse:
    assert request is not None
    assert timeout > 0
    raise urllib.error.HTTPError(
        url=getattr(request, "full_url", "https://example.invalid"),
        code=500,
        msg="boom",
        hdrs=None,
        fp=io.BytesIO(b"boom"),
    )


def notification_env(
    *,
    ntfy_enabled: bool = True,
    telegram_enabled: bool = True,
) -> dict[str, str]:
    return {
        "SMARTCRYPTO_NTFY_ENABLED": "true" if ntfy_enabled else "false",
        "SMARTCRYPTO_NTFY_TOPIC": "test-topic",
        "SMARTCRYPTO_NTFY_SERVER_URL": "https://ntfy.sh",
        "SMARTCRYPTO_NTFY_TOKEN": "",
        "SMARTCRYPTO_NTFY_USERNAME": "",
        "SMARTCRYPTO_NTFY_PASSWORD": "",
        "SMARTCRYPTO_TELEGRAM_ENABLED": "true" if telegram_enabled else "false",
        "SMARTCRYPTO_TELEGRAM_BOT_TOKEN": "test-telegram-token",
        "SMARTCRYPTO_TELEGRAM_CHAT_ID": "test-chat-id",
        "SMARTCRYPTO_TELEGRAM_API_BASE_URL": "https://api.telegram.org",
        "SMARTCRYPTO_TELEGRAM_PARSE_MODE": "",
        "SMARTCRYPTO_TELEGRAM_DISABLE_NOTIFICATION": "false",
        "SMARTCRYPTO_NOTIFICATION_TIMEOUT_SECONDS": "10",
    }


def create_trade_db(path: Path) -> None:
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
            [
                (
                    1,
                    "ETH/USDT:USDT",
                    0,
                    0,
                    "2026-06-01 20:15:07.045313",
                    "2026-06-01 22:01:49.990000",
                    2002.32,
                    1987.16,
                    49.05684,
                    -0.81114816,
                    "stop_loss",
                ),
                (
                    2,
                    "BTC/USDT:USDT",
                    1,
                    1,
                    "2026-06-01 23:00:00.000000",
                    None,
                    61100.0,
                    None,
                    30.55,
                    None,
                    None,
                ),
                (
                    3,
                    "SOL/USDT:USDT",
                    1,
                    0,
                    "2026-06-02 00:00:00.000000",
                    None,
                    142.0,
                    None,
                    20.0,
                    None,
                    None,
                ),
            ],
        )
        connection.commit()


def state_rows(path: Path, table: str) -> list[tuple[Any, ...]]:
    with sqlite3.connect(str(path)) as connection:
        return connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()


def status_counts(path: Path, table: str) -> dict[str, int]:
    with sqlite3.connect(str(path)) as connection:
        rows = connection.execute(f"SELECT status, COUNT(*) FROM {table} GROUP BY status").fetchall()
    return {str(status): int(count) for status, count in rows}


def channel_rows(path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(str(path)) as connection:
        return [
            (str(notification_key), str(channel), str(status))
            for notification_key, channel, status in connection.execute(
                """
                SELECT notification_key, channel, status
                FROM trade_event_notification_channels
                ORDER BY notification_key, channel
                """
            ).fetchall()
        ]


def test_load_trade_events_detects_open_and_close_for_watched_pairs(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.sqlite"
    create_trade_db(db_path)

    events = load_trade_events(db_path)

    assert [event.notification_key for event in events] == [
        "1:OPEN_LONG",
        "1:CLOSE_LONG",
        "2:OPEN_SHORT",
    ]
    assert {event.pair for event in events} == set(WATCHED_PAIRS)
    assert events[0].binance_futures_url == WATCHED_PAIRS["ETH/USDT:USDT"]
    assert events[2].side == "SHORT"


def test_dispatch_dry_run_telegram_only_does_not_persist(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.sqlite"
    state_db = tmp_path / "state.sqlite"
    create_trade_db(db_path)

    report = dispatch_trade_events(
        load_trade_events(db_path),
        state_db_path=state_db,
        dry_run=True,
        env=notification_env(),
        channels="telegram",
        limit=1,
    )

    assert report["status"] == "ok"
    assert report["channels"] == "telegram"
    assert report["telegram_only"] is True
    assert report["events_dispatched"] == 1
    assert report["events_marked_sent"] == 0
    assert load_completed_event_keys(state_db, channels="telegram") == set()
    assert report["dispatches"][0]["required_channels"] == ("telegram",)
    assert report["dispatches"][0]["results"][1]["channel"] == "telegram"
    assert report["dispatches"][0]["results"][1]["status"] == "sent"
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False


def test_dispatch_dry_run_ntfy_only_requires_ntfy(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.sqlite"
    state_db = tmp_path / "state.sqlite"
    create_trade_db(db_path)

    report = dispatch_trade_events(
        load_trade_events(db_path),
        state_db_path=state_db,
        dry_run=True,
        env=notification_env(),
        channels="ntfy",
        limit=1,
    )

    assert report["status"] == "ok"
    assert report["channels"] == "ntfy"
    assert report["telegram_only"] is False
    assert report["dispatches"][0]["required_channels"] == ("ntfy",)
    results = {row["channel"]: row for row in report["dispatches"][0]["results"]}
    assert results["ntfy"]["status"] == "sent"
    assert results["ntfy"]["reason"] == "dry_run"
    assert results["telegram"]["status"] == "disabled"


def test_dispatch_real_all_channels_persists_event_and_channel_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.sqlite"
    state_db = tmp_path / "state.sqlite"
    create_trade_db(db_path)

    events = load_trade_events(db_path)
    target_events = [events[0]]

    report = dispatch_trade_events(
        target_events,
        state_db_path=state_db,
        dry_run=False,
        env=notification_env(),
        channels="all",
        limit=1,
        ntfy_opener=fake_urlopen,
        telegram_opener=fake_urlopen,
    )

    assert report["status"] == "ok"
    assert report["events_detected"] == 1
    assert report["events_dispatched"] == 1
    assert report["events_marked_sent"] == 1
    assert report["dispatches"][0]["successful_channels"] == ["ntfy", "telegram"]
    assert report["dispatches"][0]["remaining_channels_after"] == []

    assert status_counts(state_db, "trade_event_notifications") == {"sent": 1}
    assert status_counts(state_db, "trade_event_notification_channels") == {"sent": 2}
    assert channel_rows(state_db) == [
        ("1:OPEN_LONG", "ntfy", "sent"),
        ("1:OPEN_LONG", "telegram", "sent"),
    ]
    assert load_completed_event_keys(state_db, channels="all") == {"1:OPEN_LONG"}

    second_report = dispatch_trade_events(
        target_events,
        state_db_path=state_db,
        dry_run=False,
        env=notification_env(),
        channels="all",
        limit=1,
        ntfy_opener=FailingIfCalledOpener(),
        telegram_opener=FailingIfCalledOpener(),
    )

    assert second_report["status"] == "ok"
    assert second_report["reason"] == "no_pending_events"
    assert second_report["events_detected"] == 1
    assert second_report["events_pending"] == 0
    assert second_report["events_dispatched"] == 0
    assert second_report["events_marked_sent"] == 0


def test_all_channels_records_partial_success_and_retries_only_missing_channel(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.sqlite"
    state_db = tmp_path / "state.sqlite"
    create_trade_db(db_path)

    ntfy_success = CountingOpener(status=200, body=b"ntfy-ok", name="ntfy")
    telegram_failure = CountingOpener(status=500, body=b"telegram-fail", name="telegram")

    first_report = dispatch_trade_events(
        load_trade_events(db_path),
        state_db_path=state_db,
        dry_run=False,
        env=notification_env(),
        channels="all",
        limit=1,
        ntfy_opener=ntfy_success,
        telegram_opener=telegram_failure,
    )

    assert first_report["status"] == "blocked"
    assert first_report["reason"] == "required_channel_delivery_blocked_or_failed"
    assert first_report["events_dispatched"] == 1
    assert first_report["events_marked_sent"] == 0
    assert first_report["dispatches"][0]["successful_channels"] == ["ntfy"]
    assert first_report["dispatches"][0]["remaining_channels_after"] == ["telegram"]
    assert ntfy_success.calls == 1
    assert telegram_failure.calls == 1

    assert status_counts(state_db, "trade_event_notification_channels") == {"sent": 1}
    assert channel_rows(state_db) == [("1:OPEN_LONG", "ntfy", "sent")]
    assert load_delivered_channels(state_db, load_trade_events(db_path)[0], channels="all") == {"ntfy"}
    assert load_completed_event_keys(state_db, channels="all") == set()

    ntfy_should_not_run = FailingIfCalledOpener()
    telegram_success = CountingOpener(status=200, body=b"telegram-ok", name="telegram")

    second_report = dispatch_trade_events(
        load_trade_events(db_path),
        state_db_path=state_db,
        dry_run=False,
        env=notification_env(),
        channels="all",
        limit=1,
        ntfy_opener=ntfy_should_not_run,
        telegram_opener=telegram_success,
    )

    assert second_report["status"] == "ok"
    assert second_report["events_dispatched"] == 1
    assert second_report["events_marked_sent"] == 1
    assert second_report["dispatches"][0]["delivered_channels_before"] == ["ntfy"]
    assert second_report["dispatches"][0]["attempted_channels"] == ("telegram",)
    assert second_report["dispatches"][0]["successful_channels"] == ["telegram"]
    assert second_report["dispatches"][0]["remaining_channels_after"] == []
    assert telegram_success.calls == 1

    assert status_counts(state_db, "trade_event_notifications") == {"sent": 1}
    assert status_counts(state_db, "trade_event_notification_channels") == {"sent": 2}
    assert channel_rows(state_db) == [
        ("1:OPEN_LONG", "ntfy", "sent"),
        ("1:OPEN_LONG", "telegram", "sent"),
    ]
    assert load_completed_event_keys(state_db, channels="all") == {"1:OPEN_LONG"}


def test_channel_idempotency_prevents_duplicate_telegram_when_ntfy_is_disabled(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.sqlite"
    state_db = tmp_path / "state.sqlite"
    create_trade_db(db_path)

    telegram_success = CountingOpener(status=200, body=b"telegram-ok")

    first_report = dispatch_trade_events(
        load_trade_events(db_path),
        state_db_path=state_db,
        dry_run=False,
        env=notification_env(ntfy_enabled=False, telegram_enabled=True),
        channels="all",
        limit=1,
        ntfy_opener=FailingIfCalledOpener(),
        telegram_opener=telegram_success,
    )

    assert first_report["status"] == "blocked"
    assert first_report["events_marked_sent"] == 0
    assert first_report["dispatches"][0]["successful_channels"] == ["telegram"]
    assert first_report["dispatches"][0]["remaining_channels_after"] == ["ntfy"]
    assert telegram_success.calls == 1

    second_report = dispatch_trade_events(
        load_trade_events(db_path),
        state_db_path=state_db,
        dry_run=False,
        env=notification_env(ntfy_enabled=False, telegram_enabled=True),
        channels="all",
        limit=1,
        ntfy_opener=FailingIfCalledOpener(),
        telegram_opener=FailingIfCalledOpener(),
    )

    assert second_report["status"] == "blocked"
    assert second_report["events_dispatched"] == 1
    assert second_report["events_marked_sent"] == 0
    assert second_report["dispatches"][0]["delivered_channels_before"] == ["telegram"]
    assert second_report["dispatches"][0]["attempted_channels"] == ("ntfy",)
    assert second_report["dispatches"][0]["successful_channels"] == []
    assert second_report["dispatches"][0]["remaining_channels_after"] == ["ntfy"]
    assert channel_rows(state_db) == [("1:OPEN_LONG", "telegram", "sent")]


def test_legacy_event_state_is_treated_as_completed_for_backward_compatibility(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.sqlite"
    state_db = tmp_path / "state.sqlite"
    create_trade_db(db_path)

    event = load_trade_events(db_path)[0]
    ensure_state_db(state_db)

    with sqlite3.connect(str(state_db)) as connection:
        connection.execute(
            """
            INSERT INTO trade_event_notifications (
                notification_key,
                trade_id,
                event_type,
                pair,
                side,
                event_time_utc,
                status,
                dry_run,
                created_at_utc,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.notification_key,
                event.trade_id,
                event.event_type,
                event.pair,
                event.side,
                event.event_time_utc,
                "sent",
                0,
                "2026-06-10T00:00:00+00:00",
                "{}",
            ),
        )
        connection.commit()

    report = dispatch_trade_events(
        [event],
        state_db_path=state_db,
        dry_run=False,
        env=notification_env(),
        channels="all",
        limit=1,
        ntfy_opener=FailingIfCalledOpener(),
        telegram_opener=FailingIfCalledOpener(),
    )

    assert report["status"] == "ok"
    assert report["reason"] == "no_pending_events"
    assert report["events_dispatched"] == 0
    assert load_completed_event_keys(state_db, channels="all") == {event.notification_key}
    assert load_delivered_channels(state_db, event, channels="all") == {"ntfy", "telegram"}


def test_baseline_marks_history_in_event_and_channel_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.sqlite"
    state_db = tmp_path / "state.sqlite"
    create_trade_db(db_path)

    events = load_trade_events(db_path)
    report = baseline_trade_events(events, state_db_path=state_db)

    assert report["status"] == "ok"
    assert report["baseline"] is True
    assert report["dry_run"] is True
    assert report["channels"] == "none"
    assert report["events_detected"] == 3
    assert report["events_baselined"] == 3
    assert report["events_dispatched"] == 0

    assert status_counts(state_db, "trade_event_notifications") == {"baseline": 3}
    assert status_counts(state_db, "trade_event_notification_channels") == {"baseline": 6}
    assert load_completed_event_keys(state_db, channels="all") == {
        "1:OPEN_LONG",
        "1:CLOSE_LONG",
        "2:OPEN_SHORT",
    }

    second_report = baseline_trade_events(events, state_db_path=state_db)
    assert second_report["events_baselined"] == 0
    assert second_report["events_pending"] == 0


def test_run_scan_writes_report_and_keeps_secrets_out(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.sqlite"
    state_db = tmp_path / "state.sqlite"
    report_path = tmp_path / "report.json"
    create_trade_db(db_path)

    report = run_trade_event_notification_scan(
        source_db_path=db_path,
        state_db_path=state_db,
        report_path=report_path,
        dry_run=True,
        env=notification_env(),
        channels="all",
        limit=1,
    )

    assert report["status"] == "ok"
    assert report_path.exists()

    serialized = report_path.read_text(encoding="utf-8")
    assert "test-telegram-token" not in serialized
    assert "test-chat-id" not in serialized
    assert "test-topic" not in serialized

    payload = json.loads(serialized)
    assert payload["events_dispatched"] == 1
    assert payload["dispatches"][0]["message"]["click_url"] == WATCHED_PAIRS["ETH/USDT:USDT"]
    assert payload["sends_orders"] is False
    assert payload["changes_risk"] is False


def test_run_daemon_single_iteration_writes_report(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.sqlite"
    state_db = tmp_path / "state.sqlite"
    report_path = tmp_path / "report.json"
    create_trade_db(db_path)

    baseline_trade_events(load_trade_events(db_path), state_db_path=state_db)

    report = run_trade_event_notification_daemon(
        source_db_path=db_path,
        state_db_path=state_db,
        report_path=report_path,
        dry_run=True,
        env=notification_env(),
        channels="all",
        poll_seconds=1,
        max_iterations=1,
    )

    assert report["status"] == "ok"
    assert report["daemon"] is True
    assert report["daemon_iteration"] == 1
    assert report["events_pending"] == 0
    assert report["events_dispatched"] == 0
    assert report["events_marked_sent"] == 0
    assert "daemon_max_iterations_reached" in report["reason"]

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["daemon"] is True
    assert payload["daemon_iteration"] == 1


def test_missing_source_db_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_trade_event_notification_scan(
            source_db_path=tmp_path / "missing.sqlite",
            state_db_path=tmp_path / "state.sqlite",
            report_path=tmp_path / "report.json",
            dry_run=True,
        )

def test_trade_event_notifications_compose_service_uses_permission_bootstrap() -> None:
    compose_path = Path("docker-compose.paper.yml")
    payload = compose_path.read_text(encoding="utf-8")

    assert "trade-event-notifications-paper:" in payload
    assert 'user: "0:0"' in payload
    assert "scripts/docker_runtime_permissions_bootstrap.py" in payload
    assert "- /app/data/reports" in payload
    assert "- /app/data/runtime" in payload
    assert "scripts/run_trade_event_notifications.py" in payload
    assert "--daemon" in payload
    assert "--send-real" in payload
    assert "ORDER_SUBMISSION_ENABLED: \"false\"" in payload
    assert "REAL_ORDER_SUBMISSION_ENABLED: \"false\"" in payload
    assert "SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS: \"false\"" in payload
