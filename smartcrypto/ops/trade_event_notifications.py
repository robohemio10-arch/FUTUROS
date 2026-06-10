from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.ops.notification_channels import (
    NotificationDispatcher,
    NotificationMessage,
    NotificationSettings,
    NtfyConfig,
    settings_from_env,
)

WATCHED_PAIRS: dict[str, str] = {
    "BTC/USDT:USDT": "https://www.binance.com/en/futures/BTCUSDT",
    "ETH/USDT:USDT": "https://www.binance.com/en/futures/ETHUSDT",
}

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
        connection.commit()
    return target


def load_sent_keys(path: str | Path) -> set[str]:
    target = ensure_state_db(path)
    with sqlite3.connect(str(target)) as connection:
        rows = connection.execute("SELECT notification_key FROM trade_event_notifications").fetchall()
    return {str(row[0]) for row in rows}


def record_sent_event(path: str | Path, event: TradeEvent, *, dry_run: bool, payload: Mapping[str, Any]) -> None:
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
                "sent",
                int(bool(dry_run)),
                utc_now(),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        connection.commit()


def telegram_only_settings(env: Mapping[str, str] | None = None) -> NotificationSettings:
    settings = settings_from_env(env if env is not None else os.environ)
    return NotificationSettings(ntfy=NtfyConfig(enabled=False), telegram=settings.telegram)


def format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.8f}".rstrip("0").rstrip(".")


def format_amount(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.8f}".rstrip("0").rstrip(".")


def build_message(event: TradeEvent) -> NotificationMessage:
    title = f"FUTUROS PAPER — {event.event_type} {event.pair}"
    lines = [
        title,
        "",
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


def telegram_sent(results: list[dict[str, Any]]) -> bool:
    return any(row.get("channel") == "telegram" and row.get("status") == "sent" for row in results)


def dispatch_trade_events(
    events: list[TradeEvent],
    *,
    state_db_path: str | Path = DEFAULT_STATE_DB_PATH,
    dry_run: bool = True,
    env: Mapping[str, str] | None = None,
    limit: int | None = None,
    persist_dry_run: bool = False,
    telegram_opener: Any = None,
) -> dict[str, Any]:
    sent_keys = load_sent_keys(state_db_path)
    pending = [event for event in events if event.notification_key not in sent_keys]
    if limit is not None:
        pending = pending[: max(0, int(limit))]

    dispatcher = NotificationDispatcher(telegram_only_settings(env), telegram_opener=telegram_opener)

    dispatches: list[dict[str, Any]] = []
    marked_sent = 0

    for event in pending:
        message = build_message(event)
        results = [result.to_dict() for result in dispatcher.send(message, dry_run=bool(dry_run))]
        row = {
            "event": asdict(event),
            "message": {
                "title": message.title,
                "event_type": message.event_type,
                "correlation_id": message.correlation_id,
                "click_url": message.click_url,
            },
            "results": results,
            **SAFE_FLAGS,
        }
        dispatches.append(row)

        if telegram_sent(results) and (not dry_run or persist_dry_run):
            record_sent_event(state_db_path, event, dry_run=dry_run, payload=row)
            marked_sent += 1

    status = "ok"
    reason = "processed"
    if any(any(result.get("channel") == "telegram" and result.get("status") in {"blocked", "failed"} for result in row["results"]) for row in dispatches):
        status = "blocked"
        reason = "telegram_delivery_blocked_or_failed"

    return {
        "status": status,
        "reason": reason,
        "created_at": utc_now(),
        "telegram_only": True,
        "dry_run": bool(dry_run),
        "state_db_path": str(state_db_path),
        "events_detected": len(events),
        "events_pending": len(pending),
        "events_dispatched": len(dispatches),
        "events_marked_sent": marked_sent,
        "watched_pairs": WATCHED_PAIRS,
        "dispatches": dispatches,
        **SAFE_FLAGS,
    }


def run_trade_event_notification_scan(
    *,
    source_db_path: str | Path,
    state_db_path: str | Path = DEFAULT_STATE_DB_PATH,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    dry_run: bool = True,
    env: Mapping[str, str] | None = None,
    limit: int | None = None,
    persist_dry_run: bool = False,
    telegram_opener: Any = None,
) -> dict[str, Any]:
    events = load_trade_events(source_db_path)
    report = dispatch_trade_events(
        events,
        state_db_path=state_db_path,
        dry_run=dry_run,
        env=env,
        limit=limit,
        persist_dry_run=persist_dry_run,
        telegram_opener=telegram_opener,
    )
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
