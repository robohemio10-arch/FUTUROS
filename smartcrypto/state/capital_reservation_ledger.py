from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from smartcrypto.state.state_repository import (
    DEFAULT_STATE_PATH,
    StateRepository,
    recompute_capital,
    utc_timestamp,
)


RESERVATION_STATUSES = {"RESERVED", "RELEASED", "FILLED", "REJECTED", "CANCELLED"}
RELEASING_STATUSES = {"RELEASED", "REJECTED", "CANCELLED"}


class CapitalReservationError(RuntimeError):
    pass


class DuplicateReservationError(CapitalReservationError):
    pass


class InsufficientCapitalError(CapitalReservationError):
    pass


class InvalidReservationStateError(CapitalReservationError):
    pass


@dataclass(frozen=True)
class CapitalReservation:
    reservation_id: str
    correlation_id: str
    client_order_id: str
    symbol: str
    side: str
    notional: float
    status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CapitalReservationLedger:
    def __init__(
        self,
        repository: StateRepository | None = None,
        *,
        state_path: str | Path = DEFAULT_STATE_PATH,
        runtime_mode: str = "paper",
        max_capital_global: float = 0.0,
    ) -> None:
        self.repository = repository or StateRepository(
            state_path,
            runtime_mode=runtime_mode,
            max_capital_global=max_capital_global,
        )

    def reserve(
        self,
        *,
        correlation_id: str,
        client_order_id: str,
        symbol: str,
        side: str,
        notional: float,
    ) -> CapitalReservation:
        clean_client_order_id = require_text(client_order_id, "client_order_id")
        clean_symbol = require_text(symbol, "symbol").upper()
        clean_side = normalize_side(side)
        clean_correlation_id = require_text(correlation_id, "correlation_id")
        clean_notional = require_positive_notional(notional)
        now = utc_timestamp()
        reservation_id = str(uuid.uuid4())
        reservation = CapitalReservation(
            reservation_id=reservation_id,
            correlation_id=clean_correlation_id,
            client_order_id=clean_client_order_id,
            symbol=clean_symbol,
            side=clean_side,
            notional=clean_notional,
            status="RESERVED",
            created_at=now,
            updated_at=now,
        )

        def mutate(state: dict[str, Any]) -> None:
            reservations = state.setdefault("reservations", {})
            if find_by_client_order_id(state, clean_client_order_id) is not None:
                raise DuplicateReservationError(
                    f"duplicate client_order_id reservation: {clean_client_order_id}"
                )
            recompute_capital(state)
            available = float(state["capital"]["available_notional"])
            if clean_notional > available:
                raise InsufficientCapitalError(
                    f"insufficient available capital: requested={clean_notional}, "
                    f"available={available}"
                )
            reservations[reservation_id] = reservation.to_dict()
            append_ledger_event(
                state,
                "capital_reserved",
                reservation_id=reservation_id,
                client_order_id=clean_client_order_id,
                notional=clean_notional,
            )
            recompute_capital(state)

        self.repository.update(mutate)
        return reservation

    def release(
        self,
        reservation_id: str,
        *,
        status: str = "RELEASED",
        reason: str | None = None,
    ) -> CapitalReservation:
        next_status = normalize_status(status)
        if next_status not in RELEASING_STATUSES:
            raise InvalidReservationStateError(
                f"release status must be one of {sorted(RELEASING_STATUSES)}"
            )
        return self._transition(reservation_id, next_status, reason=reason)

    def mark_filled(self, reservation_id: str) -> CapitalReservation:
        return self._transition(reservation_id, "FILLED")

    def get(self, reservation_id: str) -> CapitalReservation | None:
        reservation = self.repository.load().get("reservations", {}).get(reservation_id)
        return CapitalReservation(**reservation) if isinstance(reservation, dict) else None

    def state(self) -> dict[str, Any]:
        return self.repository.load()

    def available_capital(self) -> float:
        state = self.repository.load()
        return float(state["capital"]["available_notional"])

    def _transition(
        self,
        reservation_id: str,
        next_status: str,
        *,
        reason: str | None = None,
    ) -> CapitalReservation:
        clean_reservation_id = require_text(reservation_id, "reservation_id")
        normalized_status = normalize_status(next_status)
        updated: dict[str, Any] = {}

        def mutate(state: dict[str, Any]) -> None:
            reservations = state.setdefault("reservations", {})
            current = reservations.get(clean_reservation_id)
            if not isinstance(current, dict):
                raise InvalidReservationStateError(
                    f"reservation not found: {clean_reservation_id}"
                )
            current_status = normalize_status(current.get("status"))
            if current_status != "RESERVED":
                raise InvalidReservationStateError(
                    f"reservation {clean_reservation_id} is not RESERVED: {current_status}"
                )
            current["status"] = normalized_status
            current["updated_at"] = utc_timestamp()
            if reason:
                current["reason"] = reason
            if normalized_status == "FILLED":
                upsert_position(state, current)
            append_ledger_event(
                state,
                f"capital_{normalized_status.lower()}",
                reservation_id=clean_reservation_id,
                client_order_id=str(current["client_order_id"]),
                notional=float(current["notional"]),
            )
            recompute_capital(state)
            updated.update(current)

        self.repository.update(mutate)
        return CapitalReservation(**strip_extra_reservation_fields(updated))


def find_by_client_order_id(state: dict[str, Any], client_order_id: str) -> dict[str, Any] | None:
    for reservation in state.get("reservations", {}).values():
        if (
            isinstance(reservation, dict)
            and str(reservation.get("client_order_id")) == client_order_id
        ):
            return reservation
    return None


def append_ledger_event(
    state: dict[str, Any],
    event_type: str,
    *,
    reservation_id: str,
    client_order_id: str,
    notional: float,
) -> None:
    state.setdefault("events", []).append(
        {
            "event_type": event_type,
            "reservation_id": reservation_id,
            "client_order_id": client_order_id,
            "notional": float(notional),
            "created_at": utc_timestamp(),
        }
    )


def upsert_position(state: dict[str, Any], reservation: dict[str, Any]) -> None:
    positions = state.setdefault("positions", {})
    key = f"{reservation['symbol']}:{reservation['side']}"
    current = positions.get(key, {})
    positions[key] = {
        "symbol": reservation["symbol"],
        "side": reservation["side"],
        "notional": float(current.get("notional", 0.0)) + float(reservation["notional"]),
        "updated_at": utc_timestamp(),
    }


def strip_extra_reservation_fields(reservation: dict[str, Any]) -> dict[str, Any]:
    return {
        "reservation_id": reservation["reservation_id"],
        "correlation_id": reservation["correlation_id"],
        "client_order_id": reservation["client_order_id"],
        "symbol": reservation["symbol"],
        "side": reservation["side"],
        "notional": float(reservation["notional"]),
        "status": reservation["status"],
        "created_at": reservation["created_at"],
        "updated_at": reservation["updated_at"],
    }


def require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CapitalReservationError(f"{field_name} is required")
    return text


def require_positive_notional(value: Any) -> float:
    try:
        notional = float(value)
    except (TypeError, ValueError):
        raise CapitalReservationError("notional must be a positive number") from None
    if notional <= 0:
        raise CapitalReservationError("notional must be a positive number")
    return notional


def normalize_side(value: Any) -> str:
    side = require_text(value, "side").upper()
    if side in {"BUY", "LONG"}:
        return "LONG"
    if side in {"SELL", "SHORT"}:
        return "SHORT"
    raise CapitalReservationError(f"unsupported side: {value}")


def normalize_status(value: Any) -> str:
    status = require_text(value, "status").upper()
    if status not in RESERVATION_STATUSES:
        raise InvalidReservationStateError(f"unsupported reservation status: {value}")
    return status
