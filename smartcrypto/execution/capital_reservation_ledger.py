from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


DEFAULT_LEDGER_PATH = Path("data/runtime/order_intent_capital_ledger.sqlite")
TRUE_VALUES = {"1", "true", "yes", "y", "on"}
SAFE_RUNTIME_MODES = {"paper", "shadow", "research", "backtest"}
RESERVATION_STATUSES = {
    "RESERVED",
    "PARTIALLY_CONSUMED",
    "CONSUMED",
    "RELEASED",
    "CANCELLED_RELEASED",
    "REJECTED_RELEASED",
    "EXPIRED",
    "RECONCILIATION_REQUIRED",
}
ACTIVE_RESERVATION_STATUSES = {"RESERVED", "PARTIALLY_CONSUMED"}
SAFETY_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)


class CapitalReservationLedgerError(RuntimeError):
    pass


class CapitalReservationSafetyError(CapitalReservationLedgerError):
    pass


class CapitalReservationValidationError(CapitalReservationLedgerError):
    pass


@dataclass(frozen=True)
class CapitalReservationRecord:
    reservation_id: str
    order_intent_id: str
    client_order_id: str
    idempotency_key: str
    symbol: str
    quote_asset: str
    reserved_amount: float
    consumed_amount: float
    released_amount: float
    status: str
    created_at_utc: str
    updated_at_utc: str
    reason: str | None = None
    paper_only: bool = True
    shadow_only: bool = True
    live_trading_enabled: bool = False
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False
    exchange_private_access: bool = False
    sends_orders: bool = False
    changes_risk: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CapitalReservationLedger:
    def __init__(
        self,
        repository_path: str | Path = DEFAULT_LEDGER_PATH,
        *,
        runtime_mode: str = "paper",
        max_capital_global: float = 1_000_000.0,
        max_capital_by_symbol: Mapping[str, float] | None = None,
    ) -> None:
        assert_runtime_safe(runtime_mode)
        self.path = Path(repository_path)
        self.runtime_mode = normalize_runtime_mode(runtime_mode)
        self.max_capital_global = positive_number(max_capital_global, "max_capital_global")
        self.max_capital_by_symbol = {
            normalize_symbol(symbol): positive_number(limit, f"max_capital_by_symbol:{symbol}")
            for symbol, limit in dict(max_capital_by_symbol or {}).items()
        }
        ensure_schema(self.path)

    def reserve(
        self,
        *,
        order_intent_id: str,
        client_order_id: str,
        idempotency_key: str,
        symbol: str,
        reserved_amount: float,
        quote_asset: str = "USDT",
        reason: str | None = None,
        safety_overrides: Mapping[str, Any] | None = None,
    ) -> CapitalReservationRecord:
        assert_runtime_safe(self.runtime_mode)
        clean_order_intent_id = require_text(order_intent_id, "order_intent_id")
        clean_client_order_id = require_text(client_order_id, "client_order_id")
        clean_idempotency_key = require_text(idempotency_key, "idempotency_key")
        clean_symbol = normalize_symbol(symbol)
        clean_quote_asset = require_text(quote_asset, "quote_asset").upper()
        clean_reserved_amount = positive_number(reserved_amount, "reserved_amount")
        safety = safety_payload(safety_overrides)
        ensure_safety_flags(safety)
        now = utc_timestamp()
        reservation_id = str(uuid.uuid4())

        with connect(self.path) as connection:
            if active_reservation_by_idempotency_key(connection, clean_idempotency_key):
                raise CapitalReservationValidationError(
                    f"active_reservation_duplicate_idempotency_key:{clean_idempotency_key}"
                )
            if active_reserved_amount(connection) + clean_reserved_amount > self.max_capital_global:
                raise CapitalReservationValidationError("insufficient_global_capital")
            symbol_limit = self.max_capital_by_symbol.get(clean_symbol)
            if (
                symbol_limit is not None
                and active_reserved_amount(connection, symbol=clean_symbol) + clean_reserved_amount > symbol_limit
            ):
                raise CapitalReservationValidationError(f"insufficient_symbol_capital:{clean_symbol}")
            connection.execute(
                """
                INSERT INTO capital_reservations (
                    reservation_id, order_intent_id, client_order_id, idempotency_key,
                    symbol, quote_asset, reserved_amount, consumed_amount,
                    released_amount, status, created_at_utc, updated_at_utc, reason,
                    paper_only, shadow_only, live_trading_enabled,
                    order_submission_enabled, real_order_submission_enabled,
                    exchange_private_access, sends_orders, changes_risk
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation_id,
                    clean_order_intent_id,
                    clean_client_order_id,
                    clean_idempotency_key,
                    clean_symbol,
                    clean_quote_asset,
                    clean_reserved_amount,
                    "RESERVED",
                    now,
                    now,
                    reason,
                    int(safety["paper_only"]),
                    int(safety["shadow_only"]),
                    int(safety["live_trading_enabled"]),
                    int(safety["order_submission_enabled"]),
                    int(safety["real_order_submission_enabled"]),
                    int(safety["exchange_private_access"]),
                    int(safety["sends_orders"]),
                    int(safety["changes_risk"]),
                ),
            )
            append_reservation_event(
                connection,
                reservation_id=reservation_id,
                order_intent_id=clean_order_intent_id,
                client_order_id=clean_client_order_id,
                event_type="RESERVED",
                amount=clean_reserved_amount,
                reason=reason,
            )
            connection.commit()
        return self.get_reservation(reservation_id)

    def consume(
        self,
        reservation_id: str,
        amount: float,
        *,
        reason: str | None = None,
    ) -> CapitalReservationRecord:
        clean_reservation_id = require_text(reservation_id, "reservation_id")
        clean_amount = positive_number(amount, "consume_amount")
        with connect(self.path) as connection:
            current = reservation_row(connection, clean_reservation_id)
            if current is None:
                raise CapitalReservationValidationError(f"reservation_not_found:{clean_reservation_id}")
            if current["status"] not in ACTIVE_RESERVATION_STATUSES:
                raise CapitalReservationValidationError(f"reservation_not_active:{current['status']}")
            remaining = remaining_amount(current)
            if clean_amount > remaining + 1e-9:
                raise CapitalReservationValidationError("consume_amount_exceeds_remaining_reserve")
            consumed = float(current["consumed_amount"]) + clean_amount
            released = float(current["released_amount"])
            status = "CONSUMED" if abs(consumed + released - float(current["reserved_amount"])) <= 1e-9 else "PARTIALLY_CONSUMED"
            now = utc_timestamp()
            connection.execute(
                """
                UPDATE capital_reservations
                SET consumed_amount = ?, status = ?, updated_at_utc = ?, reason = ?
                WHERE reservation_id = ?
                """,
                (consumed, status, now, reason, clean_reservation_id),
            )
            append_reservation_event(
                connection,
                reservation_id=clean_reservation_id,
                order_intent_id=current["order_intent_id"],
                client_order_id=current["client_order_id"],
                event_type=status,
                amount=clean_amount,
                reason=reason,
            )
            connection.commit()
        return self.get_reservation(clean_reservation_id)

    def release(
        self,
        reservation_id: str,
        *,
        status: str = "RELEASED",
        amount: float | None = None,
        reason: str | None = None,
    ) -> CapitalReservationRecord:
        clean_reservation_id = require_text(reservation_id, "reservation_id")
        clean_status = normalize_reservation_status(status)
        if clean_status not in {"RELEASED", "CANCELLED_RELEASED", "REJECTED_RELEASED", "EXPIRED"}:
            raise CapitalReservationValidationError(f"invalid_release_status:{status}")
        with connect(self.path) as connection:
            current = reservation_row(connection, clean_reservation_id)
            if current is None:
                raise CapitalReservationValidationError(f"reservation_not_found:{clean_reservation_id}")
            remaining = remaining_amount(current)
            release_amount = remaining if amount is None else positive_number(amount, "release_amount")
            if release_amount > remaining + 1e-9:
                raise CapitalReservationValidationError("release_amount_exceeds_remaining_reserve")
            released = float(current["released_amount"]) + release_amount
            next_status = clean_status if abs(released + float(current["consumed_amount"]) - float(current["reserved_amount"])) <= 1e-9 else "PARTIALLY_CONSUMED"
            now = utc_timestamp()
            connection.execute(
                """
                UPDATE capital_reservations
                SET released_amount = ?, status = ?, updated_at_utc = ?, reason = ?
                WHERE reservation_id = ?
                """,
                (released, next_status, now, reason, clean_reservation_id),
            )
            append_reservation_event(
                connection,
                reservation_id=clean_reservation_id,
                order_intent_id=current["order_intent_id"],
                client_order_id=current["client_order_id"],
                event_type=next_status,
                amount=release_amount,
                reason=reason,
            )
            connection.commit()
        return self.get_reservation(clean_reservation_id)

    def release_for_cancel(self, reservation_id: str, *, reason: str | None = "cancelled") -> CapitalReservationRecord:
        return self.release(reservation_id, status="CANCELLED_RELEASED", reason=reason)

    def release_for_reject(self, reservation_id: str, *, reason: str | None = "rejected") -> CapitalReservationRecord:
        return self.release(reservation_id, status="REJECTED_RELEASED", reason=reason)

    def expire(self, reservation_id: str, *, reason: str | None = "expired") -> CapitalReservationRecord:
        return self.release(reservation_id, status="EXPIRED", reason=reason)

    def get_reservation(self, reservation_id: str) -> CapitalReservationRecord:
        with connect(self.path) as connection:
            row = reservation_row(connection, reservation_id)
        if row is None:
            raise CapitalReservationValidationError(f"reservation_not_found:{reservation_id}")
        return CapitalReservationRecord(**reservation_to_dict(row))

    def get_by_order_intent_id(self, order_intent_id: str) -> CapitalReservationRecord | None:
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM capital_reservations WHERE order_intent_id = ? ORDER BY created_at_utc DESC LIMIT 1",
                (order_intent_id,),
            ).fetchone()
        return CapitalReservationRecord(**reservation_to_dict(row)) if row else None

    def list_reservations(self) -> list[CapitalReservationRecord]:
        with connect(self.path) as connection:
            rows = connection.execute("SELECT * FROM capital_reservations ORDER BY created_at_utc").fetchall()
        return [CapitalReservationRecord(**reservation_to_dict(row)) for row in rows]


def ensure_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS order_intents (
                order_intent_id TEXT PRIMARY KEY,
                correlation_id TEXT NOT NULL,
                client_order_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                requested_notional REAL NOT NULL,
                requested_quantity REAL NOT NULL,
                requested_price REAL,
                reserved_capital REAL NOT NULL,
                leverage REAL NOT NULL,
                risk_decision_id TEXT,
                risk_mode TEXT,
                status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                state_before TEXT,
                state_after TEXT,
                reason TEXT,
                paper_only INTEGER NOT NULL DEFAULT 1,
                shadow_only INTEGER NOT NULL DEFAULT 1,
                live_trading_enabled INTEGER NOT NULL DEFAULT 0,
                order_submission_enabled INTEGER NOT NULL DEFAULT 0,
                real_order_submission_enabled INTEGER NOT NULL DEFAULT 0,
                exchange_private_access INTEGER NOT NULL DEFAULT 0,
                sends_orders INTEGER NOT NULL DEFAULT 0,
                changes_risk INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS order_intent_events (
                event_id TEXT PRIMARY KEY,
                order_intent_id TEXT NOT NULL,
                client_order_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                valid_transition INTEGER NOT NULL,
                reason TEXT,
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capital_reservations (
                reservation_id TEXT PRIMARY KEY,
                order_intent_id TEXT NOT NULL,
                client_order_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quote_asset TEXT NOT NULL,
                reserved_amount REAL NOT NULL,
                consumed_amount REAL NOT NULL,
                released_amount REAL NOT NULL,
                status TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                reason TEXT,
                paper_only INTEGER NOT NULL DEFAULT 1,
                shadow_only INTEGER NOT NULL DEFAULT 1,
                live_trading_enabled INTEGER NOT NULL DEFAULT 0,
                order_submission_enabled INTEGER NOT NULL DEFAULT 0,
                real_order_submission_enabled INTEGER NOT NULL DEFAULT 0,
                exchange_private_access INTEGER NOT NULL DEFAULT 0,
                sends_orders INTEGER NOT NULL DEFAULT 0,
                changes_risk INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS capital_reservation_events (
                event_id TEXT PRIMARY KEY,
                reservation_id TEXT NOT NULL,
                order_intent_id TEXT NOT NULL,
                client_order_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                amount REAL NOT NULL,
                reason TEXT,
                created_at_utc TEXT NOT NULL
            );
            """
        )
        connection.commit()


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def append_reservation_event(
    connection: sqlite3.Connection,
    *,
    reservation_id: str,
    order_intent_id: str,
    client_order_id: str,
    event_type: str,
    amount: float,
    reason: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO capital_reservation_events (
            event_id, reservation_id, order_intent_id, client_order_id,
            event_type, amount, reason, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            reservation_id,
            order_intent_id,
            client_order_id,
            event_type,
            float(amount),
            reason,
            utc_timestamp(),
        ),
    )


def active_reservation_by_idempotency_key(connection: sqlite3.Connection, idempotency_key: str) -> bool:
    row = connection.execute(
        """
        SELECT reservation_id FROM capital_reservations
        WHERE idempotency_key = ? AND status IN ('RESERVED', 'PARTIALLY_CONSUMED')
        LIMIT 1
        """,
        (idempotency_key,),
    ).fetchone()
    return row is not None


def active_reserved_amount(connection: sqlite3.Connection, *, symbol: str | None = None) -> float:
    query = """
        SELECT COALESCE(SUM(reserved_amount - consumed_amount - released_amount), 0)
        FROM capital_reservations
        WHERE status IN ('RESERVED', 'PARTIALLY_CONSUMED')
    """
    params: tuple[Any, ...] = ()
    if symbol is not None:
        query += " AND symbol = ?"
        params = (symbol,)
    value = connection.execute(query, params).fetchone()[0]
    return float(value or 0.0)


def reservation_row(connection: sqlite3.Connection, reservation_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM capital_reservations WHERE reservation_id = ?",
        (reservation_id,),
    ).fetchone()


def reservation_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    for key in (
        "paper_only",
        "shadow_only",
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
    ):
        payload[key] = bool(payload[key])
    return payload


def remaining_amount(row: sqlite3.Row | Mapping[str, Any]) -> float:
    return float(row["reserved_amount"]) - float(row["consumed_amount"]) - float(row["released_amount"])


def assert_runtime_safe(runtime_mode: str) -> None:
    normalized = normalize_runtime_mode(runtime_mode)
    reasons: list[str] = []
    if normalized not in SAFE_RUNTIME_MODES:
        reasons.append(f"runtime_mode_not_allowed:{runtime_mode}")
    if env_enabled("LIVE_ENABLED"):
        reasons.append("LIVE_ENABLED=true")
    if env_enabled("ORDER_SUBMISSION_ENABLED"):
        reasons.append("ORDER_SUBMISSION_ENABLED=true")
    if env_enabled("REAL_ORDER_SUBMISSION_ENABLED"):
        reasons.append("REAL_ORDER_SUBMISSION_ENABLED=true")
    if reasons:
        raise CapitalReservationSafetyError("unsafe capital reservation ledger runtime: " + ",".join(reasons))


def env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUE_VALUES


def normalize_runtime_mode(value: Any) -> str:
    return require_text(value, "runtime_mode").lower()


def require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CapitalReservationValidationError(f"{field_name}_required")
    return text


def normalize_symbol(value: Any) -> str:
    return require_text(value, "symbol").upper().replace("/", "").replace(":USDT", "")


def positive_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise CapitalReservationValidationError(f"{field_name}_must_be_positive_number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CapitalReservationValidationError(f"{field_name}_must_be_positive_number") from None
    if number <= 0:
        raise CapitalReservationValidationError(f"{field_name}_must_be_positive_number")
    return number


def normalize_reservation_status(value: Any) -> str:
    status = require_text(value, "status").upper()
    if status not in RESERVATION_STATUSES:
        raise CapitalReservationValidationError(f"invalid_reservation_status:{value}")
    return status


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safety_payload(overrides: Mapping[str, Any] | None = None) -> dict[str, bool]:
    payload = {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    if overrides:
        payload.update({key: bool(value) for key, value in overrides.items() if key in payload})
    return payload


def ensure_safety_flags(payload: Mapping[str, Any]) -> None:
    unsafe = unsafe_safety_flags(payload)
    if unsafe:
        raise CapitalReservationSafetyError("unsafe capital reservation ledger flags: " + ",".join(unsafe))


def unsafe_safety_flags(payload: Mapping[str, Any]) -> list[str]:
    unsafe: list[str] = []
    if payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    for flag in SAFETY_FALSE_FLAGS:
        if payload.get(flag):
            unsafe.append(flag)
    return unsafe
