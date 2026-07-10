from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.ops.notification_channels import (
    NotificationDispatcher,
    NotificationMessage,
    NotificationSettings,
    NtfyConfig,
    TelegramConfig,
    preflight_notification_channels,
    settings_from_env,
)

WATCHED_PAIRS: dict[str, str] = {
    "BTC/USDT:USDT": "https://www.binance.com/en/futures/BTCUSDT",
    "ETH/USDT:USDT": "https://www.binance.com/en/futures/ETHUSDT",
}

VALID_CHANNEL_MODES = {"telegram", "ntfy", "all"}
VALID_CHANNEL_NAMES = {"telegram", "ntfy"}
COMPLETE_STATUSES = {"sent", "baseline", "dry_run"}

DEFAULT_STATE_DB_PATH = Path("data/runtime/trade_event_notifications.sqlite")
DEFAULT_REPORT_PATH = Path("data/reports/trade_event_notifications_report.json")

SAFE_FLAGS: dict[str, Any] = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
}


@dataclass(frozen=True)
class TradeEvent:
    notification_key: str
    trade_id: int
    event_type: str
    side: str
    pair: str
    binance_futures_url: str
    event_time_utc: str | None
    open_date: str | None
    close_date: str | None
    open_rate: float | None
    close_rate: float | None
    stake_amount: float | None
    close_profit_abs: float | None
    exit_reason: str | None
    is_open: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int | float):
        return bool(value)
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "short"}


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "nat", "null"}:
        return None
    return text


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_pair(value: Any) -> str:
    return str(value or "").strip().upper()


def normalize_channels(channels: str) -> str:
    value = str(channels or "").strip().lower()
    if value not in VALID_CHANNEL_MODES:
        raise ValueError(f"invalid_channels:{channels}")
    return value


def required_channels(channels: str) -> tuple[str, ...]:
    mode = normalize_channels(channels)
    if mode == "all":
        return ("ntfy", "telegram")
    return (mode,)


def dispatch_mode_for_channels(channels: tuple[str, ...] | list[str] | set[str]) -> str:
    normalized = tuple(sorted({str(channel).strip().lower() for channel in channels}))
    invalid = [channel for channel in normalized if channel not in VALID_CHANNEL_NAMES]
    if invalid:
        raise ValueError(f"invalid_channel_names:{','.join(invalid)}")
    if normalized == ("ntfy", "telegram"):
        return "all"
    if normalized == ("ntfy",):
        return "ntfy"
    if normalized == ("telegram",):
        return "telegram"
    raise ValueError("empty_dispatch_channels")


def detect_side(row: Mapping[str, Any]) -> str:
    for key in ("is_short", "short"):
        if key in row:
            return "SHORT" if boolish(row.get(key)) else "LONG"

    for key in ("side", "trade_direction", "direction", "position_side"):
        raw = str(row.get(key) or "").strip().upper()
        if raw in {"SHORT", "SELL"}:
            return "SHORT"
        if raw in {"LONG", "BUY"}:
            return "LONG"

    return "LONG"


def event_type_for(*, action: str, side: str) -> str:
    return f"{action}_{side}"


def row_to_events(row: Mapping[str, Any]) -> list[TradeEvent]:
    pair = normalize_pair(row.get("pair"))
    if pair not in WATCHED_PAIRS:
        return []

    trade_id_raw = row.get("id", row.get("trade_id"))
    if trade_id_raw is None:
        return []

    trade_id = int(trade_id_raw)
    side = detect_side(row)
    is_open = boolish(row.get("is_open"))
    open_date = optional_str(row.get("open_date"))
    close_date = optional_str(row.get("close_date"))

    base = {
        "trade_id": trade_id,
        "side": side,
        "pair": pair,
        "binance_futures_url": WATCHED_PAIRS[pair],
        "open_date": open_date,
        "close_date": close_date,
        "open_rate": optional_float(row.get("open_rate")),
        "close_rate": optional_float(row.get("close_rate")),
        "stake_amount": optional_float(row.get("stake_amount")),
        "close_profit_abs": optional_float(row.get("close_profit_abs")),
        "exit_reason": optional_str(row.get("exit_reason")),
        "is_open": is_open,
    }

    events: list[TradeEvent] = []

    if open_date:
        event_type = event_type_for(action="OPEN", side=side)
        events.append(
            TradeEvent(
                notification_key=f"{trade_id}:{event_type}",
                event_type=event_type,
                event_time_utc=open_date,
                **base,
            )
        )

    if close_date and not is_open:
        event_type = event_type_for(action="CLOSE", side=side)
        events.append(
            TradeEvent(
                notification_key=f"{trade_id}:{event_type}",
                event_type=event_type,
                event_time_utc=close_date,
                **base,
            )
        )

    return events


def connect_read_only(db_path: str | Path) -> sqlite3.Connection:
    target = Path(db_path)
    if not target.exists():
        raise FileNotFoundError(f"source_db_not_found:{target}")
    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def trades_columns(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute("PRAGMA table_info(trades)").fetchall()
    return [str(row["name"]) for row in rows]


def load_trade_rows(source_db_path: str | Path, *, watched_pairs: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    watched = watched_pairs or WATCHED_PAIRS
    with connect_read_only(source_db_path) as connection:
        columns = trades_columns(connection)
        if not columns:
            raise RuntimeError("trades_table_missing_or_empty_schema")

        required = ["id", "pair"]
        missing = [column for column in required if column not in columns]
        if missing:
            raise RuntimeError(f"trades_table_missing_required_columns:{','.join(missing)}")

        selected = [
            column
            for column in (
                "id",
                "pair",
                "is_open",
                "is_short",
                "short",
                "side",
                "trade_direction",
                "position_side",
                "open_date",
                "close_date",
                "open_rate",
                "close_rate",
                "stake_amount",
                "close_profit_abs",
                "exit_reason",
            )
            if column in columns
        ]

        placeholders = ",".join("?" for _ in watched)
        sql = f"SELECT {', '.join(selected)} FROM trades WHERE upper(pair) IN ({placeholders}) ORDER BY id ASC"
        return [dict(row) for row in connection.execute(sql, tuple(watched.keys())).fetchall()]


def load_trade_events(source_db_path: str | Path) -> list[TradeEvent]:
    events: list[TradeEvent] = []
    for row in load_trade_rows(source_db_path):
        events.extend(row_to_events(row))
    return sorted(events, key=lambda event: (event.event_time_utc or "", event.trade_id, event.event_type))


def ensure_state_db(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(target)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_event_notifications (
                notification_key TEXT PRIMARY KEY,
                trade_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                pair TEXT NOT NULL,
                side TEXT NOT NULL,
                event_time_utc TEXT,
                status TEXT NOT NULL,
                dry_run INTEGER NOT NULL,
                created_at_utc TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_event_notification_channels (
                notification_key TEXT NOT NULL,
                channel TEXT NOT NULL,
                trade_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                pair TEXT NOT NULL,
                side TEXT NOT NULL,
                event_time_utc TEXT,
                status TEXT NOT NULL,
                dry_run INTEGER NOT NULL,
                created_at_utc TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (notification_key, channel)
            )
            """
        )
        connection.commit()

    return target


def load_legacy_completed_keys(path: str | Path) -> set[str]:
    target = ensure_state_db(path)
    with sqlite3.connect(str(target)) as connection:
        rows = connection.execute(
            """
            SELECT notification_key
            FROM trade_event_notifications
            WHERE status IN ('sent', 'baseline', 'dry_run')
            """
        ).fetchall()
    return {str(row[0]) for row in rows}


def load_channel_completed_keys(path: str | Path, *, channels: str) -> set[str]:
    target = ensure_state_db(path)
    required = set(required_channels(channels))
    if not required:
        return set()

    grouped: dict[str, set[str]] = {}

    with sqlite3.connect(str(target)) as connection:
        rows = connection.execute(
            """
            SELECT notification_key, channel, status
            FROM trade_event_notification_channels
            WHERE status IN ('sent', 'baseline', 'dry_run')
            """
        ).fetchall()

    for notification_key, channel, status in rows:
        channel_name = str(channel)
        if channel_name in required and str(status) in COMPLETE_STATUSES:
            grouped.setdefault(str(notification_key), set()).add(channel_name)

    return {key for key, completed in grouped.items() if required.issubset(completed)}


def load_completed_event_keys(path: str | Path, *, channels: str) -> set[str]:
    return load_legacy_completed_keys(path) | load_channel_completed_keys(path, channels=channels)


def load_delivered_channels(path: str | Path, event: TradeEvent, *, channels: str) -> set[str]:
    target = ensure_state_db(path)
    required = set(required_channels(channels))

    with sqlite3.connect(str(target)) as connection:
        legacy = connection.execute(
            """
            SELECT status
            FROM trade_event_notifications
            WHERE notification_key = ?
            """,
            (event.notification_key,),
        ).fetchone()
        if legacy and str(legacy[0]) in COMPLETE_STATUSES:
            return set(required)

        rows = connection.execute(
            """
            SELECT channel, status
            FROM trade_event_notification_channels
            WHERE notification_key = ?
            """,
            (event.notification_key,),
        ).fetchall()

    delivered = {
        str(channel)
        for channel, status in rows
        if str(channel) in required and str(status) in COMPLETE_STATUSES
    }
    return delivered


def record_event_state(
    path: str | Path,
    event: TradeEvent,
    *,
    status: str,
    dry_run: bool,
    payload: Mapping[str, Any],
) -> None:
    target = ensure_state_db(path)
    with sqlite3.connect(str(target)) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO trade_event_notifications (
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
                status,
                int(bool(dry_run)),
                utc_now(),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        connection.commit()


def record_channel_state(
    path: str | Path,
    event: TradeEvent,
    *,
    channel: str,
    status: str,
    dry_run: bool,
    payload: Mapping[str, Any],
) -> None:
    channel_name = str(channel).strip().lower()
    if channel_name not in VALID_CHANNEL_NAMES:
        raise ValueError(f"invalid_channel:{channel}")

    target = ensure_state_db(path)

    with sqlite3.connect(str(target)) as connection:
        connection.execute(
            """
            INSERT INTO trade_event_notification_channels (
                notification_key,
                channel,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(notification_key, channel) DO UPDATE SET
                status = excluded.status,
                dry_run = excluded.dry_run,
                created_at_utc = excluded.created_at_utc,
                payload_json = excluded.payload_json
            """,
            (
                event.notification_key,
                channel_name,
                event.trade_id,
                event.event_type,
                event.pair,
                event.side,
                event.event_time_utc,
                status,
                int(bool(dry_run)),
                utc_now(),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        connection.commit()


def channel_settings(env: Mapping[str, str] | None = None, *, channels: str = "telegram") -> NotificationSettings:
    mode = normalize_channels(channels)
    settings = settings_from_env(env if env is not None else os.environ)

    if mode == "telegram":
        return NotificationSettings(ntfy=NtfyConfig(enabled=False), telegram=settings.telegram)

    if mode == "ntfy":
        return NotificationSettings(ntfy=settings.ntfy, telegram=TelegramConfig(enabled=False))

    return settings


def notification_channel_preflight(
    env: Mapping[str, str] | None = None,
    *,
    channels: str = "telegram",
) -> dict[str, Any]:
    mode = normalize_channels(channels)
    return preflight_notification_channels(
        settings_from_env(env if env is not None else os.environ),
        channels=mode,
    ).to_dict()


def blocked_preflight_report(preflight: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": str(preflight.get("reason") or "notification_channel_preflight_blocked"),
        "created_at": utc_now(),
        "channels": str(preflight.get("channels") or ""),
        "failed_checks": list(preflight.get("failed_checks") or []),
        "ntfy_enabled": bool(preflight.get("ntfy_enabled")),
        "telegram_enabled": bool(preflight.get("telegram_enabled")),
        "auth_mode": str(preflight.get("auth_mode") or "none"),
        "notification_preflight": "blocked",
        "baseline": False,
        "dry_run": True,
        "events_detected": 0,
        "events_pending": 0,
        "events_dispatched": 0,
        "events_marked_sent": 0,
        "events_baselined": 0,
        "dispatches": [],
        **SAFE_FLAGS,
    }


def format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.8f}".rstrip("0").rstrip(".")


def format_amount(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.8f}".rstrip("0").rstrip(".")


def build_message(event: TradeEvent) -> NotificationMessage:
    title = f"FUTUROS PAPER — {event.event_type} {event.pair}"
    lines = [
        f"Par: {event.pair}",
        f"Trade ID: {event.trade_id}",
        f"Lado: {event.side}",
        f"Evento: {event.event_type}",
        f"Horário UTC: {event.event_time_utc or 'n/a'}",
        f"Entrada: {format_rate(event.open_rate)}",
        f"Saída: {format_rate(event.close_rate)}",
        f"Stake: {format_amount(event.stake_amount)} USDT",
        f"PnL realizado: {format_amount(event.close_profit_abs)} USDT",
        f"Motivo saída: {event.exit_reason or 'n/a'}",
        f"Binance Futures: {event.binance_futures_url}",
        "",
        "Safety:",
        "paper_only=true",
        "shadow_only=true",
        "live_trading_enabled=false",
        "order_submission_enabled=false",
        "real_order_submission_enabled=false",
        "exchange_private_access=false",
        "sends_orders=false",
        "changes_risk=false",
    ]
    return NotificationMessage(
        title=title,
        body="\n".join(lines),
        priority="default",
        tags=("futuros", "trade"),
        click_url=event.binance_futures_url,
        correlation_id=event.notification_key,
        event_type=event.event_type,
        severity="info",
    ).normalized()


def results_by_channel(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("channel")): row for row in results}


def required_delivery_succeeded(results: list[dict[str, Any]], *, channels: str) -> bool:
    indexed = results_by_channel(results)
    for channel in required_channels(channels):
        row = indexed.get(channel)
        if not row or row.get("status") != "sent":
            return False
    return True


def required_delivery_failed(results: list[dict[str, Any]], *, channels: str) -> bool:
    return not required_delivery_succeeded(results, channels=channels)


def baseline_trade_events(
    events: list[TradeEvent],
    *,
    state_db_path: str | Path = DEFAULT_STATE_DB_PATH,
    limit: int | None = None,
) -> dict[str, Any]:
    completed_keys = load_completed_event_keys(state_db_path, channels="all")
    pending = [event for event in events if event.notification_key not in completed_keys]
    if limit is not None:
        pending = pending[: max(0, int(limit))]

    baselined: list[dict[str, Any]] = []

    for event in pending:
        row = {
            "event": asdict(event),
            "baseline": True,
            "results": [],
            "required_channels": required_channels("all"),
            **SAFE_FLAGS,
        }
        record_event_state(state_db_path, event, status="baseline", dry_run=True, payload=row)
        for channel in required_channels("all"):
            record_channel_state(
                state_db_path,
                event,
                channel=channel,
                status="baseline",
                dry_run=True,
                payload={**row, "channel": channel},
            )
        baselined.append(row)

    return {
        "status": "ok",
        "reason": "baseline_completed",
        "created_at": utc_now(),
        "telegram_only": False,
        "channels": "none",
        "baseline": True,
        "dry_run": True,
        "state_db_path": str(state_db_path),
        "events_detected": len(events),
        "events_pending": len(pending),
        "events_dispatched": 0,
        "events_marked_sent": 0,
        "events_baselined": len(baselined),
        "watched_pairs": WATCHED_PAIRS,
        "dispatches": baselined,
        **SAFE_FLAGS,
    }


def dispatch_trade_events(
    events: list[TradeEvent],
    *,
    state_db_path: str | Path = DEFAULT_STATE_DB_PATH,
    dry_run: bool = True,
    env: Mapping[str, str] | None = None,
    limit: int | None = None,
    persist_dry_run: bool = False,
    channels: str = "telegram",
    baseline: bool = False,
    ntfy_opener: Any = None,
    telegram_opener: Any = None,
) -> dict[str, Any]:
    mode = normalize_channels(channels)
    preflight = notification_channel_preflight(env, channels=mode)
    if preflight["status"] != "ok":
        return blocked_preflight_report(preflight)

    if baseline:
        return baseline_trade_events(events, state_db_path=state_db_path, limit=limit)

    completed_keys = load_completed_event_keys(state_db_path, channels=mode)
    pending = [event for event in events if event.notification_key not in completed_keys]
    if limit is not None:
        pending = pending[: max(0, int(limit))]

    dispatches: list[dict[str, Any]] = []
    marked_sent = 0
    blocked_or_failed = False

    for event in pending:
        required = tuple(required_channels(mode))
        delivered_before = load_delivered_channels(state_db_path, event, channels=mode)
        channels_to_send = tuple(channel for channel in required if channel not in delivered_before)

        if not channels_to_send:
            continue

        dispatch_mode = dispatch_mode_for_channels(channels_to_send)
        dispatcher = NotificationDispatcher(
            channel_settings(env, channels=dispatch_mode),
            ntfy_opener=ntfy_opener,
            telegram_opener=telegram_opener,
        )

        message = build_message(event)
        results = [result.to_dict() for result in dispatcher.send(message, dry_run=bool(dry_run))]
        dispatch_ok = required_delivery_succeeded(results, channels=dispatch_mode)

        row = {
            "event": asdict(event),
            "message": {
                "title": message.title,
                "event_type": message.event_type,
                "correlation_id": message.correlation_id,
                "click_url": message.click_url,
            },
            "results": results,
            "required_channels": required,
            "delivered_channels_before": sorted(delivered_before),
            "attempted_channels": channels_to_send,
            "dispatch_mode": dispatch_mode,
            **SAFE_FLAGS,
        }
        dispatches.append(row)

        successful_channels = {
            str(result.get("channel"))
            for result in results
            if str(result.get("channel")) in channels_to_send and result.get("status") == "sent"
        }

        if not dispatch_ok:
            blocked_or_failed = True

        if successful_channels and (not dry_run or persist_dry_run):
            status = "sent" if not dry_run else "dry_run"
            for channel in successful_channels:
                record_channel_state(
                    state_db_path,
                    event,
                    channel=channel,
                    status=status,
                    dry_run=dry_run,
                    payload={**row, "channel": channel},
                )

        delivered_after = delivered_before | (successful_channels if (not dry_run or persist_dry_run) else set())
        row["successful_channels"] = sorted(successful_channels)
        row["delivered_channels_after"] = sorted(delivered_after)
        row["remaining_channels_after"] = sorted(set(required) - delivered_after)

        if set(required).issubset(delivered_after):
            record_event_state(
                state_db_path,
                event,
                status="sent" if not dry_run else "dry_run",
                dry_run=dry_run,
                payload=row,
            )
            marked_sent += 1

    status = "ok"
    reason = "processed"
    if blocked_or_failed:
        status = "blocked"
        reason = "required_channel_delivery_blocked_or_failed"
    elif not dispatches:
        reason = "no_pending_events"

    return {
        "status": status,
        "reason": reason,
        "created_at": utc_now(),
        "telegram_only": mode == "telegram",
        "channels": mode,
        "baseline": False,
        "dry_run": bool(dry_run),
        "state_db_path": str(state_db_path),
        "events_detected": len(events),
        "events_pending": len(pending),
        "events_dispatched": len(dispatches),
        "events_marked_sent": marked_sent,
        "events_baselined": 0,
        "watched_pairs": WATCHED_PAIRS,
        "dispatches": dispatches,
        **SAFE_FLAGS,
    }


def write_report(report: Mapping[str, Any], report_path: str | Path) -> None:
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_trade_event_notification_scan(
    *,
    source_db_path: str | Path,
    state_db_path: str | Path = DEFAULT_STATE_DB_PATH,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    dry_run: bool = True,
    env: Mapping[str, str] | None = None,
    limit: int | None = None,
    persist_dry_run: bool = False,
    channels: str = "telegram",
    baseline: bool = False,
    ntfy_opener: Any = None,
    telegram_opener: Any = None,
) -> dict[str, Any]:
    preflight = notification_channel_preflight(env, channels=channels)
    if preflight["status"] != "ok":
        return blocked_preflight_report(preflight)

    events = load_trade_events(source_db_path)
    report = dispatch_trade_events(
        events,
        state_db_path=state_db_path,
        dry_run=dry_run,
        env=env,
        limit=limit,
        persist_dry_run=persist_dry_run,
        channels=channels,
        baseline=baseline,
        ntfy_opener=ntfy_opener,
        telegram_opener=telegram_opener,
    )
    write_report(report, report_path)
    return report


def run_trade_event_notification_daemon(
    *,
    source_db_path: str | Path,
    state_db_path: str | Path = DEFAULT_STATE_DB_PATH,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    dry_run: bool = True,
    env: Mapping[str, str] | None = None,
    limit: int | None = None,
    channels: str = "all",
    poll_seconds: float = 10.0,
    max_iterations: int | None = None,
) -> dict[str, Any]:
    preflight = notification_channel_preflight(env, channels=channels)
    if preflight["status"] != "ok":
        return blocked_preflight_report(preflight)

    iterations = 0
    last_report: dict[str, Any] = {
        "status": "ok",
        "reason": "daemon_not_started",
        **SAFE_FLAGS,
    }

    while True:
        iterations += 1
        last_report = run_trade_event_notification_scan(
            source_db_path=source_db_path,
            state_db_path=state_db_path,
            report_path=report_path,
            dry_run=dry_run,
            env=env,
            limit=limit,
            channels=channels,
            baseline=False,
        )
        last_report["daemon"] = True
        last_report["daemon_iteration"] = iterations
        write_report(last_report, report_path)

        if max_iterations is not None and iterations >= max_iterations:
            last_report["reason"] = f"{last_report.get('reason')};daemon_max_iterations_reached"
            write_report(last_report, report_path)
            return last_report

        time.sleep(max(1.0, float(poll_seconds)))
