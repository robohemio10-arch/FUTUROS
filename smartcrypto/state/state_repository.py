from __future__ import annotations

import json
import os
import sqlite3
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}
DEFAULT_STATE_PATH = "data/runtime/state_repository.json"
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


class StateRepositoryError(RuntimeError):
    pass


class StateSafetyError(StateRepositoryError):
    pass


class StatePersistenceError(StateRepositoryError):
    pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUE_VALUES


def assert_runtime_safe(runtime_mode: str) -> None:
    normalized_mode = str(runtime_mode or "").strip().lower()
    reasons: list[str] = []
    if normalized_mode not in SAFE_RUNTIME_MODES:
        reasons.append(f"runtime_mode_not_allowed:{runtime_mode}")
    if env_enabled("LIVE_ENABLED"):
        reasons.append("LIVE_ENABLED=true")
    if env_enabled("ORDER_SUBMISSION_ENABLED"):
        reasons.append("ORDER_SUBMISSION_ENABLED=true")
    if env_enabled("REAL_ORDER_SUBMISSION_ENABLED"):
        reasons.append("REAL_ORDER_SUBMISSION_ENABLED=true")
    if reasons:
        raise StateSafetyError("unsafe state repository runtime: " + ",".join(reasons))


def default_state(runtime_mode: str = "paper", max_capital_global: float = 0.0) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "runtime_mode": runtime_mode,
        "created_at": utc_timestamp(),
        "updated_at": utc_timestamp(),
        "capital": {
            "max_capital_global": float(max_capital_global),
            "reserved_notional": 0.0,
            "filled_notional": 0.0,
            "available_notional": float(max_capital_global),
        },
        "reservations": {},
        "positions": {},
        "events": [],
    }


class StateRepository:
    def __init__(
        self,
        path: str | Path = DEFAULT_STATE_PATH,
        *,
        runtime_mode: str = "paper",
        max_capital_global: float = 0.0,
    ) -> None:
        assert_runtime_safe(runtime_mode)
        self.path = Path(path)
        self.runtime_mode = str(runtime_mode).strip().lower()
        self.max_capital_global = float(max_capital_global)
        self.uses_sqlite = self.path.suffix.lower() in SQLITE_SUFFIXES
        if self.uses_sqlite:
            self._ensure_sqlite_schema()

    def load(self) -> dict[str, Any]:
        if self.uses_sqlite:
            return self.export_state()
        if not self.path.exists():
            state = default_state(self.runtime_mode, self.max_capital_global)
            self.save(state)
            return state

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception as exc:
            raise StatePersistenceError(
                f"failed to read state repository {self.path}: {exc}"
            ) from exc

        if not isinstance(state, dict):
            raise StatePersistenceError("state repository root must be a JSON object")
        return self._normalize_state(state)

    def save(self, state: dict[str, Any]) -> None:
        if self.uses_sqlite:
            raise StatePersistenceError("direct save is not supported for sqlite state repositories")
        assert_runtime_safe(str(state.get("runtime_mode", self.runtime_mode)))
        normalized = self._normalize_state(state)
        normalized["updated_at"] = utc_timestamp()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(normalized, handle, ensure_ascii=False, indent=2, sort_keys=True)
            temp_path.replace(self.path)
        except Exception as exc:
            raise StatePersistenceError(
                f"failed to write state repository {self.path}: {exc}"
            ) from exc

    def update(self, mutator: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        if self.uses_sqlite:
            state = self.export_state()
            working_state = deepcopy(state)
            mutator(working_state)
            return working_state
        state = self.load()
        working_state = deepcopy(state)
        mutator(working_state)
        self.save(working_state)
        return working_state

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if self.uses_sqlite:
            self.append_audit_event(
                event_type=str(event.get("event_type", "audit_event")),
                correlation_id=str(event.get("correlation_id", "")),
                payload=event,
            )
            return self.export_state()

        def mutate(state: dict[str, Any]) -> None:
            state.setdefault("events", []).append(event)

        return self.update(mutate)

    def create_order_intent(
        self,
        *,
        correlation_id: str,
        client_order_id: str,
        symbol: str,
        side: str,
        order_type: str = "market",
        requested_notional: float,
        requested_quantity: float = 0.0,
        reserved_capital: float | None = None,
        risk_decision_id: str | None = None,
        state_before: dict[str, Any] | None = None,
        status: str = "ORDER_INTENT_CREATED",
        safety_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.uses_sqlite:
            return self._create_order_intent_json(
                correlation_id=correlation_id,
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                order_type=order_type,
                requested_notional=requested_notional,
                requested_quantity=requested_quantity,
                reserved_capital=reserved_capital,
                risk_decision_id=risk_decision_id,
                state_before=state_before,
                status=status,
                safety_overrides=safety_overrides,
            )

        assert_runtime_safe(self.runtime_mode)
        clean_correlation_id = require_text(correlation_id, "correlation_id")
        clean_client_order_id = require_text(client_order_id, "client_order_id")
        clean_symbol = normalize_symbol(symbol)
        clean_side = normalize_side(side)
        clean_order_type = require_text(order_type, "order_type").lower()
        clean_notional = require_non_negative_number(requested_notional, "requested_notional")
        clean_quantity = require_non_negative_number(requested_quantity, "requested_quantity")
        clean_reserved = require_non_negative_number(
            clean_notional if reserved_capital is None else reserved_capital,
            "reserved_capital",
        )
        safety = safety_payload(safety_overrides)
        ensure_safe_payload(safety)
        order_intent_id = str(uuid.uuid4())
        reservation_id = str(uuid.uuid4())
        now = utc_timestamp()
        with self._connect() as connection:
            if self._sqlite_client_order_exists(connection, clean_client_order_id):
                raise StatePersistenceError(f"duplicate_client_order_id:{clean_client_order_id}")
            connection.execute(
                """
                INSERT INTO capital_reservations (
                    reservation_id, correlation_id, client_order_id, symbol, side,
                    reserved_capital, remaining_reserved_capital, status,
                    created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation_id,
                    clean_correlation_id,
                    clean_client_order_id,
                    clean_symbol,
                    clean_side,
                    clean_reserved,
                    clean_reserved,
                    "RESERVED",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO order_intents (
                    order_intent_id, correlation_id, client_order_id, symbol, side,
                    order_type, requested_notional, requested_quantity,
                    reserved_capital, risk_decision_id, state_before, status,
                    created_at_utc, updated_at_utc, reservation_id,
                    paper_only, shadow_only, live_trading_enabled,
                    order_submission_enabled, real_order_submission_enabled,
                    exchange_private_access, sends_orders, changes_risk
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_intent_id,
                    clean_correlation_id,
                    clean_client_order_id,
                    clean_symbol,
                    clean_side,
                    clean_order_type,
                    clean_notional,
                    clean_quantity,
                    clean_reserved,
                    risk_decision_id,
                    json.dumps(state_before or {}, ensure_ascii=False, sort_keys=True),
                    status,
                    now,
                    now,
                    reservation_id,
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
            connection.commit()
        event = {
            "event_type": "order_intent_created",
            "correlation_id": clean_correlation_id,
            "client_order_id": clean_client_order_id,
            "order_intent_id": order_intent_id,
            "reservation_id": reservation_id,
            "created_at_utc": now,
        }
        self.append_audit_event(
            event_type="order_intent_created",
            correlation_id=clean_correlation_id,
            payload=event,
        )
        return self.get_order_intent(order_intent_id) or event

    def create_simulated_order(
        self,
        *,
        order_intent_id: str,
        client_order_id: str,
        status: str = "SUBMITTED",
        submitted_at_utc: str | None = None,
    ) -> dict[str, Any]:
        self._require_sqlite("create_simulated_order")
        clean_order_intent_id = require_text(order_intent_id, "order_intent_id")
        clean_client_order_id = require_text(client_order_id, "client_order_id")
        now = submitted_at_utc or utc_timestamp()
        simulated_order_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO simulated_orders (
                    simulated_order_id, order_intent_id, client_order_id,
                    status, submitted_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (simulated_order_id, clean_order_intent_id, clean_client_order_id, status, now, now),
            )
            connection.execute(
                "UPDATE order_intents SET status = ?, updated_at_utc = ? WHERE order_intent_id = ?",
                ("ORDER_SUBMITTED_SIMULATED", now, clean_order_intent_id),
            )
            connection.commit()
        return self.get_simulated_order(simulated_order_id) or {}

    def cancel_order(self, client_order_id: str) -> dict[str, Any]:
        return self._release_order(client_order_id, intent_status="CANCELLED", reservation_status="RELEASED")

    def reject_order(self, client_order_id: str) -> dict[str, Any]:
        return self._release_order(client_order_id, intent_status="REJECTED", reservation_status="RELEASED")

    def adjust_reserve_on_partial_fill(
        self,
        *,
        client_order_id: str,
        filled_notional: float,
        remaining_reserved_capital: float | None = None,
    ) -> dict[str, Any]:
        self._require_sqlite("adjust_reserve_on_partial_fill")
        clean_client_order_id = require_text(client_order_id, "client_order_id")
        clean_filled = require_non_negative_number(filled_notional, "filled_notional")
        now = utc_timestamp()
        with self._connect() as connection:
            reservation = self._sqlite_get_reservation(connection, clean_client_order_id)
            if reservation is None:
                raise StatePersistenceError(f"reservation_not_found:{clean_client_order_id}")
            original_reserved = float(reservation["reserved_capital"])
            remaining = original_reserved - clean_filled if remaining_reserved_capital is None else require_non_negative_number(
                remaining_reserved_capital,
                "remaining_reserved_capital",
            )
            if remaining < -1e-9 or remaining > original_reserved + 1e-9:
                raise StatePersistenceError(f"partial_fill_remaining_invalid:{clean_client_order_id}")
            connection.execute(
                """
                UPDATE capital_reservations
                SET filled_notional = ?, remaining_reserved_capital = ?, status = ?, updated_at_utc = ?
                WHERE client_order_id = ?
                """,
                (clean_filled, remaining, "PARTIAL_FILLED", now, clean_client_order_id),
            )
            connection.execute(
                "UPDATE order_intents SET status = ?, updated_at_utc = ? WHERE client_order_id = ?",
                ("PARTIAL_FILLED", now, clean_client_order_id),
            )
            connection.execute(
                """
                UPDATE simulated_orders
                SET status = ?, filled_notional = ?, remaining_reserved_capital = ?, updated_at_utc = ?
                WHERE client_order_id = ?
                """,
                ("PARTIAL_FILLED", clean_filled, remaining, now, clean_client_order_id),
            )
            connection.commit()
        return self.get_reservation(clean_client_order_id) or {}

    def mark_dispatch_unknown_on_timeout(self, client_order_id: str) -> dict[str, Any]:
        self._require_sqlite("mark_dispatch_unknown_on_timeout")
        clean_client_order_id = require_text(client_order_id, "client_order_id")
        now = utc_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE order_intents SET status = ?, updated_at_utc = ? WHERE client_order_id = ?",
                ("DISPATCH_UNKNOWN", now, clean_client_order_id),
            )
            connection.execute(
                """
                UPDATE simulated_orders
                SET status = ?, dispatch_unknown = 1, updated_at_utc = ?
                WHERE client_order_id = ?
                """,
                ("DISPATCH_UNKNOWN", now, clean_client_order_id),
            )
            connection.commit()
        return self.get_order_intent_by_client_order_id(clean_client_order_id) or {}

    def upsert_position(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        state_hash: str | None = None,
    ) -> dict[str, Any]:
        self._require_sqlite("upsert_position")
        clean_symbol = normalize_symbol(symbol)
        clean_side = normalize_side(side)
        clean_quantity = require_non_negative_number(quantity, "quantity")
        position_id = f"{clean_symbol}:{clean_side}"
        now = utc_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO positions (position_id, symbol, side, quantity, state_hash, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    side = excluded.side,
                    quantity = excluded.quantity,
                    state_hash = excluded.state_hash,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (position_id, clean_symbol, clean_side, clean_quantity, state_hash, now),
            )
            connection.commit()
        return self.get_position(position_id) or {}

    def create_state_lock(self, *, lock_id: str, expires_at_utc: str, status: str = "ACTIVE") -> None:
        self._require_sqlite("create_state_lock")
        now = utc_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO state_locks (lock_id, status, expires_at_utc, created_at_utc)
                VALUES (?, ?, ?, ?)
                """,
                (require_text(lock_id, "lock_id"), status, expires_at_utc, now),
            )
            connection.commit()

    def create_dispatch_lock(self, *, lock_id: str, expires_at_utc: str, status: str = "ACTIVE") -> None:
        self._require_sqlite("create_dispatch_lock")
        now = utc_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO dispatch_locks (lock_id, status, expires_at_utc, created_at_utc)
                VALUES (?, ?, ?, ?)
                """,
                (require_text(lock_id, "lock_id"), status, expires_at_utc, now),
            )
            connection.commit()

    def append_audit_event(
        self,
        *,
        event_type: str,
        correlation_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not self.uses_sqlite:
            return
        now = utc_timestamp()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (event_id, event_type, correlation_id, payload, created_at_utc)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    require_text(event_type, "event_type"),
                    str(correlation_id or "").strip(),
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            connection.commit()

    def export_state(self) -> dict[str, Any]:
        self._require_sqlite("export_state")
        with self._connect() as connection:
            return {
                "schema_version": 2,
                "runtime_mode": self.runtime_mode,
                "repository_path": str(self.path),
                "positions": self._fetch_all(connection, "positions"),
                "order_intents": self._fetch_all(connection, "order_intents"),
                "simulated_orders": self._fetch_all(connection, "simulated_orders"),
                "capital_reservations": self._fetch_all(connection, "capital_reservations"),
                "reconciliation_snapshots": self._fetch_all(connection, "reconciliation_snapshots"),
                "state_locks": self._fetch_all(connection, "state_locks"),
                "dispatch_locks": self._fetch_all(connection, "dispatch_locks"),
                "audit_events": self._fetch_all(connection, "audit_events"),
            }

    def get_order_intent(self, order_intent_id: str) -> dict[str, Any] | None:
        self._require_sqlite("get_order_intent")
        with self._connect() as connection:
            return self._fetch_one(
                connection,
                "SELECT * FROM order_intents WHERE order_intent_id = ?",
                (order_intent_id,),
            )

    def get_order_intent_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        self._require_sqlite("get_order_intent_by_client_order_id")
        with self._connect() as connection:
            return self._fetch_one(
                connection,
                "SELECT * FROM order_intents WHERE client_order_id = ?",
                (client_order_id,),
            )

    def get_simulated_order(self, simulated_order_id: str) -> dict[str, Any] | None:
        self._require_sqlite("get_simulated_order")
        with self._connect() as connection:
            return self._fetch_one(
                connection,
                "SELECT * FROM simulated_orders WHERE simulated_order_id = ?",
                (simulated_order_id,),
            )

    def get_reservation(self, client_order_id: str) -> dict[str, Any] | None:
        self._require_sqlite("get_reservation")
        with self._connect() as connection:
            return self._fetch_one(
                connection,
                "SELECT * FROM capital_reservations WHERE client_order_id = ? ORDER BY created_at_utc DESC LIMIT 1",
                (client_order_id,),
            )

    def get_position(self, position_id: str) -> dict[str, Any] | None:
        self._require_sqlite("get_position")
        with self._connect() as connection:
            return self._fetch_one(
                connection,
                "SELECT * FROM positions WHERE position_id = ?",
                (position_id,),
            )

    def list_order_intents(self) -> list[dict[str, Any]]:
        return self.export_state()["order_intents"]

    def list_capital_reservations(self) -> list[dict[str, Any]]:
        return self.export_state()["capital_reservations"]

    def list_positions(self) -> list[dict[str, Any]]:
        return self.export_state()["positions"]

    def _normalize_state(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(state)
        normalized.setdefault("schema_version", 1)
        normalized.setdefault("runtime_mode", self.runtime_mode)
        assert_runtime_safe(str(normalized["runtime_mode"]))
        normalized.setdefault("created_at", utc_timestamp())
        normalized.setdefault("updated_at", utc_timestamp())
        normalized.setdefault("reservations", {})
        normalized.setdefault("positions", {})
        normalized.setdefault("events", [])
        normalized.setdefault("capital", {})
        capital = normalized["capital"]
        if not isinstance(capital, dict):
            raise StatePersistenceError("state capital must be an object")
        capital.setdefault("max_capital_global", self.max_capital_global)
        capital["max_capital_global"] = float(capital["max_capital_global"])
        recompute_capital(normalized)
        return normalized

    def _create_order_intent_json(
        self,
        *,
        correlation_id: str,
        client_order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        requested_notional: float,
        requested_quantity: float,
        reserved_capital: float | None,
        risk_decision_id: str | None,
        state_before: dict[str, Any] | None,
        status: str,
        safety_overrides: dict[str, Any] | None,
    ) -> dict[str, Any]:
        clean_reserved = require_non_negative_number(
            requested_notional if reserved_capital is None else reserved_capital,
            "reserved_capital",
        )
        safety = safety_payload(safety_overrides)
        ensure_safe_payload(safety)
        order_intent_id = str(uuid.uuid4())
        reservation_id = str(uuid.uuid4())
        now = utc_timestamp()

        def mutate(state: dict[str, Any]) -> None:
            state.setdefault("reservations", {})[reservation_id] = {
                "reservation_id": reservation_id,
                "correlation_id": require_text(correlation_id, "correlation_id"),
                "client_order_id": require_text(client_order_id, "client_order_id"),
                "symbol": normalize_symbol(symbol),
                "side": normalize_side(side),
                "notional": clean_reserved,
                "status": "RESERVED",
                "created_at": now,
                "updated_at": now,
            }
            state.setdefault("order_intents", {})[order_intent_id] = {
                "order_intent_id": order_intent_id,
                "correlation_id": require_text(correlation_id, "correlation_id"),
                "client_order_id": require_text(client_order_id, "client_order_id"),
                "symbol": normalize_symbol(symbol),
                "side": normalize_side(side),
                "order_type": order_type,
                "requested_notional": float(requested_notional),
                "requested_quantity": float(requested_quantity),
                "reserved_capital": clean_reserved,
                "reservation_id": reservation_id,
                "risk_decision_id": risk_decision_id,
                "state_before": state_before or {},
                "status": status,
                "created_at_utc": now,
                **safety,
            }

        state = self.update(mutate)
        return state["order_intents"][order_intent_id]

    def _release_order(self, client_order_id: str, *, intent_status: str, reservation_status: str) -> dict[str, Any]:
        self._require_sqlite("_release_order")
        clean_client_order_id = require_text(client_order_id, "client_order_id")
        now = utc_timestamp()
        with self._connect() as connection:
            connection.execute(
                "UPDATE capital_reservations SET status = ?, remaining_reserved_capital = 0, updated_at_utc = ? WHERE client_order_id = ?",
                (reservation_status, now, clean_client_order_id),
            )
            connection.execute(
                "UPDATE order_intents SET status = ?, updated_at_utc = ? WHERE client_order_id = ?",
                (intent_status, now, clean_client_order_id),
            )
            connection.execute(
                "UPDATE simulated_orders SET status = ?, updated_at_utc = ? WHERE client_order_id = ?",
                (intent_status, now, clean_client_order_id),
            )
            connection.commit()
        return self.get_reservation(clean_client_order_id) or {}

    def _ensure_sqlite_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    state_hash TEXT,
                    updated_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_intents (
                    order_intent_id TEXT PRIMARY KEY,
                    correlation_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    requested_notional REAL NOT NULL,
                    requested_quantity REAL NOT NULL,
                    reserved_capital REAL NOT NULL,
                    risk_decision_id TEXT,
                    state_before TEXT,
                    status TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    reservation_id TEXT,
                    paper_only INTEGER NOT NULL DEFAULT 1,
                    shadow_only INTEGER NOT NULL DEFAULT 1,
                    live_trading_enabled INTEGER NOT NULL DEFAULT 0,
                    order_submission_enabled INTEGER NOT NULL DEFAULT 0,
                    real_order_submission_enabled INTEGER NOT NULL DEFAULT 0,
                    exchange_private_access INTEGER NOT NULL DEFAULT 0,
                    sends_orders INTEGER NOT NULL DEFAULT 0,
                    changes_risk INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS simulated_orders (
                    simulated_order_id TEXT PRIMARY KEY,
                    order_intent_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    submitted_at_utc TEXT,
                    filled_notional REAL NOT NULL DEFAULT 0,
                    remaining_reserved_capital REAL NOT NULL DEFAULT 0,
                    dispatch_unknown INTEGER NOT NULL DEFAULT 0,
                    updated_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capital_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    correlation_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    reserved_capital REAL NOT NULL,
                    remaining_reserved_capital REAL NOT NULL,
                    filled_notional REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reconciliation_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    snapshot_hash TEXT,
                    payload TEXT,
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS state_locks (
                    lock_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dispatch_locks (
                    lock_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    correlation_id TEXT,
                    payload TEXT,
                    created_at_utc TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _require_sqlite(self, operation: str) -> None:
        if not self.uses_sqlite:
            raise StatePersistenceError(f"{operation} requires sqlite state repository")

    def _sqlite_client_order_exists(self, connection: sqlite3.Connection, client_order_id: str) -> bool:
        rows = connection.execute(
            """
            SELECT client_order_id FROM order_intents WHERE client_order_id = ?
            UNION ALL
            SELECT client_order_id FROM capital_reservations WHERE client_order_id = ?
            """,
            (client_order_id, client_order_id),
        ).fetchall()
        return bool(rows)

    def _sqlite_get_reservation(
        self,
        connection: sqlite3.Connection,
        client_order_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM capital_reservations WHERE client_order_id = ? ORDER BY created_at_utc DESC LIMIT 1",
            (client_order_id,),
        ).fetchone()

    def _fetch_all(self, connection: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
        rows = connection.execute(f"SELECT * FROM {table_name}").fetchall()
        return [row_to_dict(row) for row in rows]

    def _fetch_one(
        self,
        connection: sqlite3.Connection,
        query: str,
        params: tuple[Any, ...],
    ) -> dict[str, Any] | None:
        row = connection.execute(query, params).fetchone()
        return row_to_dict(row) if row is not None else None


def recompute_capital(state: dict[str, Any]) -> None:
    reservations = state.get("reservations", {})
    if not isinstance(reservations, dict):
        raise StatePersistenceError("state reservations must be an object")
    capital = state.setdefault("capital", {})
    max_capital = float(capital.get("max_capital_global", 0.0))
    reserved = 0.0
    filled = 0.0
    for reservation in reservations.values():
        if not isinstance(reservation, dict):
            continue
        status = str(reservation.get("status", "")).upper()
        notional = float(reservation.get("notional", 0.0))
        if status == "RESERVED":
            reserved += notional
        elif status == "FILLED":
            filled += notional
    capital["reserved_notional"] = float(reserved)
    capital["filled_notional"] = float(filled)
    capital["available_notional"] = float(max_capital - reserved - filled)


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    for key, value in list(payload.items()):
        if key in {
            "paper_only",
            "shadow_only",
            "live_trading_enabled",
            "order_submission_enabled",
            "real_order_submission_enabled",
            "exchange_private_access",
            "sends_orders",
            "changes_risk",
            "dispatch_unknown",
        }:
            payload[key] = bool(value)
    return payload


def require_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise StatePersistenceError(f"{field_name}_required")
    return normalized


def normalize_symbol(value: Any) -> str:
    return require_text(value, "symbol").upper().replace("/", "").replace(":USDT", "")


def normalize_side(value: Any) -> str:
    normalized = require_text(value, "side").lower()
    if normalized not in {"long", "short", "buy", "sell"}:
        raise StatePersistenceError(f"invalid_side:{value}")
    return {"buy": "long", "sell": "short"}.get(normalized, normalized)


def require_non_negative_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise StatePersistenceError(f"{field_name}_must_be_non_negative_number")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise StatePersistenceError(f"{field_name}_must_be_non_negative_number") from None
    if numeric < 0:
        raise StatePersistenceError(f"{field_name}_must_be_non_negative_number")
    return numeric


def safety_payload(overrides: dict[str, Any] | None = None) -> dict[str, bool]:
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


def ensure_safe_payload(payload: dict[str, bool]) -> None:
    unsafe: list[str] = []
    if payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    for key in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
    ):
        if payload.get(key):
            unsafe.append(key)
    if unsafe:
        raise StateSafetyError("unsafe state repository payload: " + ",".join(unsafe))
