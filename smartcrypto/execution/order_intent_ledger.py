from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.execution.capital_reservation_ledger import (
    DEFAULT_LEDGER_PATH,
    CapitalReservationLedger,
    connect,
    ensure_schema,
    safety_payload,
    unsafe_safety_flags,
    utc_timestamp,
)


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
SAFE_RUNTIME_MODES = {"paper", "shadow", "research", "backtest"}
ORDER_INTENT_STATUSES = {
    "CREATED",
    "CAPITAL_RESERVED",
    "SIMULATED_SUBMITTED",
    "SIMULATED_ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELLED",
    "REJECTED",
    "TIMEOUT",
    "DISPATCH_UNKNOWN",
    "RECONCILIATION_REQUIRED",
}
ACTIVE_INTENT_STATUSES = {
    "CREATED",
    "CAPITAL_RESERVED",
    "SIMULATED_SUBMITTED",
    "SIMULATED_ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "TIMEOUT",
    "DISPATCH_UNKNOWN",
    "RECONCILIATION_REQUIRED",
}
TERMINAL_INTENT_STATUSES = {"FILLED", "CANCELLED", "REJECTED"}
VALID_TRANSITIONS = {
    "CREATED": {"CAPITAL_RESERVED", "REJECTED", "CANCELLED", "RECONCILIATION_REQUIRED"},
    "CAPITAL_RESERVED": {"SIMULATED_SUBMITTED", "CANCELLED", "REJECTED", "TIMEOUT", "DISPATCH_UNKNOWN", "RECONCILIATION_REQUIRED"},
    "SIMULATED_SUBMITTED": {"SIMULATED_ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "TIMEOUT", "DISPATCH_UNKNOWN", "CANCELLED", "REJECTED", "RECONCILIATION_REQUIRED"},
    "SIMULATED_ACKNOWLEDGED": {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "TIMEOUT", "DISPATCH_UNKNOWN", "RECONCILIATION_REQUIRED"},
    "PARTIALLY_FILLED": {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "TIMEOUT", "DISPATCH_UNKNOWN", "RECONCILIATION_REQUIRED"},
    "TIMEOUT": {"DISPATCH_UNKNOWN", "RECONCILIATION_REQUIRED"},
    "DISPATCH_UNKNOWN": {"RECONCILIATION_REQUIRED"},
    "RECONCILIATION_REQUIRED": set(),
    "FILLED": set(),
    "CANCELLED": set(),
    "REJECTED": set(),
}
SAFETY_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)


class OrderIntentLedgerError(RuntimeError):
    pass


class OrderIntentSafetyError(OrderIntentLedgerError):
    pass


class OrderIntentValidationError(OrderIntentLedgerError):
    pass


@dataclass(frozen=True)
class OrderIntentRecord:
    order_intent_id: str
    correlation_id: str
    client_order_id: str
    idempotency_key: str
    symbol: str
    side: str
    order_type: str
    requested_notional: float
    requested_quantity: float
    requested_price: float | None
    reserved_capital: float
    leverage: float
    risk_decision_id: str | None
    risk_mode: str | None
    status: str
    created_at_utc: str
    updated_at_utc: str
    state_before: str | None = None
    state_after: str | None = None
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


class OrderIntentLedger:
    def __init__(
        self,
        repository_path: str | Path = DEFAULT_LEDGER_PATH,
        *,
        runtime_mode: str = "paper",
        duplicate_window_seconds: int = 0,
        max_capital_global: float = 1_000_000.0,
        financial_event_log_path: str | Path | None = None,
    ) -> None:
        assert_runtime_safe(runtime_mode)
        self.path = Path(repository_path)
        self.runtime_mode = normalize_runtime_mode(runtime_mode)
        self.duplicate_window_seconds = int(duplicate_window_seconds)
        self.capital_ledger = CapitalReservationLedger(
            self.path,
            runtime_mode=self.runtime_mode,
            max_capital_global=max_capital_global,
        )
        self.financial_event_log_path = Path(financial_event_log_path) if financial_event_log_path else None
        ensure_schema(self.path)

    def create_intent(
        self,
        *,
        correlation_id: str,
        idempotency_key: str,
        symbol: str,
        side: str,
        order_type: str = "market",
        requested_notional: float,
        requested_quantity: float = 0.0,
        requested_price: float | None = None,
        reserved_capital: float | None = None,
        leverage: float = 1.0,
        risk_decision_id: str | None = None,
        risk_mode: str | None = None,
        client_order_id: str | None = None,
        state_before: Mapping[str, Any] | None = None,
        reason: str | None = None,
        safety_overrides: Mapping[str, Any] | None = None,
    ) -> OrderIntentRecord:
        assert_runtime_safe(self.runtime_mode)
        clean_correlation_id = require_text(correlation_id, "correlation_id")
        clean_idempotency_key = require_text(idempotency_key, "idempotency_key")
        clean_symbol = normalize_symbol(symbol)
        clean_side = normalize_side(side)
        clean_order_type = require_text(order_type, "order_type").lower()
        clean_notional = positive_number(requested_notional, "requested_notional")
        clean_quantity = non_negative_number(requested_quantity, "requested_quantity")
        clean_price = None if requested_price is None else positive_number(requested_price, "requested_price")
        clean_reserved_capital = positive_number(
            clean_notional if reserved_capital is None else reserved_capital,
            "reserved_capital",
        )
        clean_leverage = positive_number(leverage, "leverage")
        clean_client_order_id = client_order_id or deterministic_client_order_id(clean_idempotency_key)
        safety = safety_payload(safety_overrides)
        ensure_safety_flags(safety)
        now = utc_timestamp()
        order_intent_id = str(uuid.uuid4())

        with connect(self.path) as connection:
            block_dispatch_unknown_same_symbol_side(connection, clean_symbol, clean_side)
            if active_idempotency_key_exists(connection, clean_idempotency_key):
                raise OrderIntentValidationError(f"active_duplicate_idempotency_key:{clean_idempotency_key}")
            if client_order_id_exists(connection, clean_client_order_id):
                raise OrderIntentValidationError(f"duplicate_client_order_id:{clean_client_order_id}")
            if self.duplicate_window_seconds > 0 and recent_correlation_duplicate(
                connection,
                clean_correlation_id,
                clean_symbol,
                clean_side,
                self.duplicate_window_seconds,
            ):
                raise OrderIntentValidationError("duplicate_correlation_symbol_side_window")
            connection.execute(
                """
                INSERT INTO order_intents (
                    order_intent_id, correlation_id, client_order_id, idempotency_key,
                    symbol, side, order_type, requested_notional, requested_quantity,
                    requested_price, reserved_capital, leverage, risk_decision_id,
                    risk_mode, status, created_at_utc, updated_at_utc, state_before,
                    state_after, reason, paper_only, shadow_only, live_trading_enabled,
                    order_submission_enabled, real_order_submission_enabled,
                    exchange_private_access, sends_orders, changes_risk
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_intent_id,
                    clean_correlation_id,
                    clean_client_order_id,
                    clean_idempotency_key,
                    clean_symbol,
                    clean_side,
                    clean_order_type,
                    clean_notional,
                    clean_quantity,
                    clean_price,
                    clean_reserved_capital,
                    clean_leverage,
                    risk_decision_id,
                    risk_mode,
                    "CREATED",
                    now,
                    now,
                    json.dumps(dict(state_before or {}), ensure_ascii=False, sort_keys=True),
                    None,
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
            append_intent_event(
                connection,
                order_intent_id=order_intent_id,
                client_order_id=clean_client_order_id,
                from_status=None,
                to_status="CREATED",
                valid_transition=True,
                reason=reason,
            )
            connection.commit()
        self._record_financial_event("order_intent_created", clean_correlation_id, clean_symbol, {"order_intent_id": order_intent_id, "client_order_id": clean_client_order_id})
        return self.get_intent(order_intent_id)

    def reserve_capital(self, order_intent_id: str, *, reason: str | None = "capital_reserved") -> OrderIntentRecord:
        intent = self.get_intent(order_intent_id)
        if intent.status != "CREATED":
            raise OrderIntentValidationError(f"capital can only be reserved from CREATED: {intent.status}")
        reservation = self.capital_ledger.reserve(
            order_intent_id=intent.order_intent_id,
            client_order_id=intent.client_order_id,
            idempotency_key=intent.idempotency_key,
            symbol=intent.symbol,
            reserved_amount=intent.reserved_capital,
            reason=reason,
        )
        updated = self.transition_status(order_intent_id, "CAPITAL_RESERVED", reason=reason)
        self._record_financial_event("capital_reserved", updated.correlation_id, updated.symbol, {"reservation_id": reservation.reservation_id, "order_intent_id": order_intent_id})
        return updated

    def submit_simulated(self, order_intent_id: str, *, reason: str | None = "simulated_submit") -> OrderIntentRecord:
        intent = self.get_intent(order_intent_id)
        if self.capital_ledger.get_by_order_intent_id(order_intent_id) is None:
            raise OrderIntentValidationError("capital_must_be_reserved_before_simulated_submit")
        updated = self.transition_status(order_intent_id, "SIMULATED_SUBMITTED", reason=reason)
        self._record_financial_event("order_submitted_simulated", updated.correlation_id, updated.symbol, {"order_intent_id": order_intent_id})
        return updated

    def acknowledge_simulated(self, order_intent_id: str) -> OrderIntentRecord:
        return self.transition_status(order_intent_id, "SIMULATED_ACKNOWLEDGED", reason="simulated_acknowledged")

    def partial_fill(self, order_intent_id: str, *, consumed_amount: float) -> OrderIntentRecord:
        reservation = self.capital_ledger.get_by_order_intent_id(order_intent_id)
        if reservation is None:
            raise OrderIntentValidationError("reservation_not_found")
        self.capital_ledger.consume(reservation.reservation_id, consumed_amount, reason="partial_fill")
        return self.transition_status(order_intent_id, "PARTIALLY_FILLED", reason="partial_fill")

    def fill(self, order_intent_id: str) -> OrderIntentRecord:
        reservation = self.capital_ledger.get_by_order_intent_id(order_intent_id)
        if reservation is None:
            raise OrderIntentValidationError("reservation_not_found")
        remaining = reservation.reserved_amount - reservation.consumed_amount - reservation.released_amount
        if remaining > 1e-9:
            self.capital_ledger.consume(reservation.reservation_id, remaining, reason="full_fill")
        return self.transition_status(order_intent_id, "FILLED", reason="full_fill")

    def cancel(self, order_intent_id: str) -> OrderIntentRecord:
        reservation = self.capital_ledger.get_by_order_intent_id(order_intent_id)
        if reservation is not None:
            self.capital_ledger.release_for_cancel(reservation.reservation_id)
        return self.transition_status(order_intent_id, "CANCELLED", reason="cancelled")

    def reject(self, order_intent_id: str, *, reason: str | None = "rejected") -> OrderIntentRecord:
        reservation = self.capital_ledger.get_by_order_intent_id(order_intent_id)
        if reservation is not None:
            self.capital_ledger.release_for_reject(reservation.reservation_id, reason=reason)
        return self.transition_status(order_intent_id, "REJECTED", reason=reason)

    def mark_timeout(self, order_intent_id: str, *, reason: str | None = "timeout") -> OrderIntentRecord:
        intent = self.transition_status(order_intent_id, "TIMEOUT", reason=reason)
        return self.transition_status(intent.order_intent_id, "DISPATCH_UNKNOWN", reason="dispatch_unknown_after_timeout")

    def transition_status(
        self,
        order_intent_id: str,
        next_status: str,
        *,
        reason: str | None = None,
        state_after: Mapping[str, Any] | None = None,
    ) -> OrderIntentRecord:
        clean_order_intent_id = require_text(order_intent_id, "order_intent_id")
        clean_next_status = normalize_status(next_status)
        with connect(self.path) as connection:
            row = intent_row(connection, clean_order_intent_id)
            if row is None:
                raise OrderIntentValidationError(f"order_intent_not_found:{clean_order_intent_id}")
            current_status = str(row["status"])
            if clean_next_status not in VALID_TRANSITIONS[current_status]:
                append_intent_event(
                    connection,
                    order_intent_id=clean_order_intent_id,
                    client_order_id=row["client_order_id"],
                    from_status=current_status,
                    to_status=clean_next_status,
                    valid_transition=False,
                    reason=reason,
                )
                connection.commit()
                raise OrderIntentValidationError(f"invalid_status_transition:{current_status}->{clean_next_status}")
            now = utc_timestamp()
            connection.execute(
                """
                UPDATE order_intents
                SET status = ?, updated_at_utc = ?, state_after = COALESCE(?, state_after), reason = ?
                WHERE order_intent_id = ?
                """,
                (
                    clean_next_status,
                    now,
                    json.dumps(dict(state_after), ensure_ascii=False, sort_keys=True) if state_after is not None else None,
                    reason,
                    clean_order_intent_id,
                ),
            )
            append_intent_event(
                connection,
                order_intent_id=clean_order_intent_id,
                client_order_id=row["client_order_id"],
                from_status=current_status,
                to_status=clean_next_status,
                valid_transition=True,
                reason=reason,
            )
            connection.commit()
        return self.get_intent(clean_order_intent_id)

    def get_intent(self, order_intent_id: str) -> OrderIntentRecord:
        with connect(self.path) as connection:
            row = intent_row(connection, order_intent_id)
        if row is None:
            raise OrderIntentValidationError(f"order_intent_not_found:{order_intent_id}")
        return OrderIntentRecord(**intent_to_dict(row))

    def get_by_client_order_id(self, client_order_id: str) -> OrderIntentRecord | None:
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM order_intents WHERE client_order_id = ? ORDER BY created_at_utc DESC LIMIT 1",
                (client_order_id,),
            ).fetchone()
        return OrderIntentRecord(**intent_to_dict(row)) if row else None

    def list_intents(self) -> list[OrderIntentRecord]:
        with connect(self.path) as connection:
            rows = connection.execute("SELECT * FROM order_intents ORDER BY created_at_utc").fetchall()
        return [OrderIntentRecord(**intent_to_dict(row)) for row in rows]

    def _record_financial_event(self, event_type: str, correlation_id: str, symbol: str, payload: dict[str, Any]) -> None:
        if self.financial_event_log_path is None:
            return
        self.financial_event_log_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "event_type": event_type,
            "correlation_id": correlation_id,
            "symbol": symbol,
            "payload": payload,
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "created_at_utc": utc_timestamp(),
        }
        with self.financial_event_log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def deterministic_client_order_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(require_text(idempotency_key, "idempotency_key").encode("utf-8")).hexdigest()
    return f"SC-{digest[:24]}"


def append_intent_event(
    connection: sqlite3.Connection,
    *,
    order_intent_id: str,
    client_order_id: str,
    from_status: str | None,
    to_status: str,
    valid_transition: bool,
    reason: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO order_intent_events (
            event_id, order_intent_id, client_order_id, from_status,
            to_status, valid_transition, reason, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            order_intent_id,
            client_order_id,
            from_status,
            to_status,
            int(valid_transition),
            reason,
            utc_timestamp(),
        ),
    )


def intent_row(connection: sqlite3.Connection, order_intent_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM order_intents WHERE order_intent_id = ?",
        (order_intent_id,),
    ).fetchone()


def intent_to_dict(row: sqlite3.Row) -> dict[str, Any]:
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


def client_order_id_exists(connection: sqlite3.Connection, client_order_id: str) -> bool:
    row = connection.execute(
        "SELECT order_intent_id FROM order_intents WHERE client_order_id = ? LIMIT 1",
        (client_order_id,),
    ).fetchone()
    return row is not None


def active_idempotency_key_exists(connection: sqlite3.Connection, idempotency_key: str) -> bool:
    row = connection.execute(
        f"""
        SELECT order_intent_id FROM order_intents
        WHERE idempotency_key = ? AND status IN ({','.join('?' for _ in ACTIVE_INTENT_STATUSES)})
        LIMIT 1
        """,
        (idempotency_key, *sorted(ACTIVE_INTENT_STATUSES)),
    ).fetchone()
    return row is not None


def block_dispatch_unknown_same_symbol_side(connection: sqlite3.Connection, symbol: str, side: str) -> None:
    row = connection.execute(
        """
        SELECT order_intent_id FROM order_intents
        WHERE symbol = ? AND side = ? AND status = 'DISPATCH_UNKNOWN'
        LIMIT 1
        """,
        (symbol, side),
    ).fetchone()
    if row is not None:
        raise OrderIntentValidationError(f"dispatch_unknown_blocks_symbol_side:{symbol}:{side}")


def recent_correlation_duplicate(
    connection: sqlite3.Connection,
    correlation_id: str,
    symbol: str,
    side: str,
    window_seconds: int,
) -> bool:
    threshold = datetime.now(timezone.utc).timestamp() - float(window_seconds)
    rows = connection.execute(
        """
        SELECT created_at_utc FROM order_intents
        WHERE correlation_id = ? AND symbol = ? AND side = ?
        """,
        (correlation_id, symbol, side),
    ).fetchall()
    for row in rows:
        try:
            created = datetime.fromisoformat(str(row["created_at_utc"]).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if created >= threshold:
            return True
    return False


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
        raise OrderIntentSafetyError("unsafe order intent ledger runtime: " + ",".join(reasons))


def env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUE_VALUES


def normalize_runtime_mode(value: Any) -> str:
    return require_text(value, "runtime_mode").lower()


def require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OrderIntentValidationError(f"{field_name}_required")
    return text


def normalize_symbol(value: Any) -> str:
    return require_text(value, "symbol").upper().replace("/", "").replace(":USDT", "")


def normalize_side(value: Any) -> str:
    side = require_text(value, "side").lower()
    if side not in {"long", "short", "buy", "sell"}:
        raise OrderIntentValidationError(f"invalid_side:{value}")
    return {"buy": "long", "sell": "short"}.get(side, side)


def positive_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise OrderIntentValidationError(f"{field_name}_must_be_positive_number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise OrderIntentValidationError(f"{field_name}_must_be_positive_number") from None
    if number <= 0:
        raise OrderIntentValidationError(f"{field_name}_must_be_positive_number")
    return number


def non_negative_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise OrderIntentValidationError(f"{field_name}_must_be_non_negative_number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise OrderIntentValidationError(f"{field_name}_must_be_non_negative_number") from None
    if number < 0:
        raise OrderIntentValidationError(f"{field_name}_must_be_non_negative_number")
    return number


def normalize_status(value: Any) -> str:
    status = require_text(value, "status").upper()
    if status not in ORDER_INTENT_STATUSES:
        raise OrderIntentValidationError(f"invalid_order_intent_status:{value}")
    return status


def ensure_safety_flags(payload: Mapping[str, Any]) -> None:
    unsafe = unsafe_safety_flags(payload)
    if unsafe:
        raise OrderIntentSafetyError("unsafe order intent ledger flags: " + ",".join(unsafe))
