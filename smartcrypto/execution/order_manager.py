from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from smartcrypto.risk.risk_manager import RiskLimits, RiskManager, env_enabled
from smartcrypto.risk.kill_switch_guard import KillSwitchGuard
from smartcrypto.state.capital_reservation_ledger import (
    CapitalReservationLedger,
    DuplicateReservationError,
    InvalidReservationStateError,
)
from smartcrypto.state.financial_event_log import KNOWN_EVENT_TYPES, FinancialEventLogger
from smartcrypto.state.state_repository import (
    DEFAULT_STATE_PATH,
    SAFE_RUNTIME_MODES,
    StateRepository,
    utc_timestamp,
)


ORDER_STATES = {
    "INTENT_CREATED",
    "RISK_APPROVED",
    "RISK_REJECTED",
    "CAPITAL_RESERVED",
    "PAPER_SUBMITTED",
    "PAPER_FILLED",
    "PAPER_CANCELLED",
    "PAPER_REJECTED",
}
TERMINAL_ORDER_STATES = {"RISK_REJECTED", "PAPER_FILLED", "PAPER_CANCELLED", "PAPER_REJECTED"}


class OrderManagerError(RuntimeError):
    pass


class OrderSafetyError(OrderManagerError):
    pass


class DuplicateClientOrderError(OrderManagerError):
    pass


class InvalidOrderIntentError(OrderManagerError):
    pass


class InvalidOrderStateError(OrderManagerError):
    pass


@dataclass(frozen=True)
class PaperOrderIntent:
    order_intent_id: str
    correlation_id: str
    client_order_id: str
    symbol: str
    side: str
    notional: float
    score: float
    status: str
    reservation_id: str | None
    risk_reasons: list[str]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OrderManager:
    def __init__(
        self,
        *,
        repository: StateRepository | None = None,
        ledger: CapitalReservationLedger | None = None,
        event_logger: FinancialEventLogger | None = None,
        risk_manager: RiskManager | None = None,
        state_path: str | Path = DEFAULT_STATE_PATH,
        event_log_path: str | Path = "data/runtime/order_manager_financial_events.jsonl",
        kill_switch_path: str | Path = "data/runtime/kill_switch_guard.json",
        runtime_mode: str = "paper",
        max_capital_global: float = 0.0,
        risk_limits: RiskLimits | None = None,
    ) -> None:
        assert_order_runtime_safe(runtime_mode)
        self.runtime_mode = str(runtime_mode).strip().lower()
        self.repository = repository or StateRepository(
            state_path,
            runtime_mode=self.runtime_mode,
            max_capital_global=max_capital_global,
        )
        self.ledger = ledger or CapitalReservationLedger(repository=self.repository)
        self.event_logger = event_logger or FinancialEventLogger(
            event_log_path,
            runtime_mode=self.runtime_mode,
            source="order_manager",
            allowed_event_types=set(KNOWN_EVENT_TYPES),
        )
        self.kill_switch_guard = KillSwitchGuard(
            state_path=kill_switch_path,
            event_logger=self.event_logger,
            runtime_mode=self.runtime_mode,
        )
        self.risk_manager = risk_manager or RiskManager(
            risk_limits
            or RiskLimits(
                runtime_mode=self.runtime_mode,
                max_position_usdt=max_capital_global,
                allowed_pairs=(),
            )
        )

    def create_order_intent(
        self,
        *,
        correlation_id: str,
        client_order_id: str,
        symbol: str,
        side: str,
        notional: float,
        score: float,
        payload: dict[str, Any] | None = None,
    ) -> PaperOrderIntent:
        assert_order_runtime_safe(self.runtime_mode)
        clean_correlation_id = require_text(correlation_id, "correlation_id")
        clean_client_order_id = require_text(client_order_id, "client_order_id")
        clean_symbol = normalize_symbol(symbol)
        clean_side = normalize_side(side)
        clean_notional = require_positive_number(notional, "notional")
        clean_score = float(score)
        order_intent_id = str(uuid.uuid4())
        now = utc_timestamp()
        intent = PaperOrderIntent(
            order_intent_id=order_intent_id,
            correlation_id=clean_correlation_id,
            client_order_id=clean_client_order_id,
            symbol=clean_symbol,
            side=clean_side,
            notional=clean_notional,
            score=clean_score,
            status="INTENT_CREATED",
            reservation_id=None,
            risk_reasons=[],
            created_at=now,
            updated_at=now,
        )
        kill_switch_result = self.kill_switch_guard.evaluate(clean_symbol)
        if kill_switch_result.block_operation:
            rejected = PaperOrderIntent(
                order_intent_id=order_intent_id,
                correlation_id=clean_correlation_id,
                client_order_id=clean_client_order_id,
                symbol=clean_symbol,
                side=clean_side,
                notional=clean_notional,
                score=clean_score,
                status="RISK_REJECTED",
                reservation_id=None,
                risk_reasons=[f"kill_switch_{kill_switch_result.status.lower()}"],
                created_at=now,
                updated_at=utc_timestamp(),
            )
            self.event_logger.record(
                "risk_rejected",
                correlation_id=clean_correlation_id,
                symbol=clean_symbol,
                payload={
                    "order_intent_id": order_intent_id,
                    "client_order_id": clean_client_order_id,
                    "status": rejected.status,
                    "reasons": rejected.risk_reasons,
                    "kill_switch": kill_switch_result.to_dict(),
                },
            )
            return rejected

        def create_intent(state: dict[str, Any]) -> None:
            order_intents = state.setdefault("order_intents", {})
            if find_intent_by_client_order_id(state, clean_client_order_id) is not None:
                raise DuplicateClientOrderError(
                    f"duplicate client_order_id order intent: {clean_client_order_id}"
                )
            order_intents[order_intent_id] = intent.to_dict()
            append_order_event(state, order_intent_id, "INTENT_CREATED")

        self.repository.update(create_intent)
        self.event_logger.record(
            "signal_generated",
            correlation_id=clean_correlation_id,
            symbol=clean_symbol,
            payload={
                "order_intent_id": order_intent_id,
                "client_order_id": clean_client_order_id,
                "side": clean_side,
                "notional": clean_notional,
                "score": clean_score,
                **(payload or {}),
            },
        )

        risk_decision = self.risk_manager.approve(
            {
                "pair": clean_symbol,
                "symbol": clean_symbol,
                "side": clean_side.lower(),
                "score": clean_score,
                "notional": clean_notional,
                "client_order_id": clean_client_order_id,
                "correlation_id": clean_correlation_id,
            }
        )
        if not risk_decision.approved:
            return self._risk_rejected(order_intent_id, risk_decision.reasons)

        self._set_status(order_intent_id, "RISK_APPROVED")
        self.event_logger.record(
            "risk_approved",
            correlation_id=clean_correlation_id,
            symbol=clean_symbol,
            payload={
                "order_intent_id": order_intent_id,
                "client_order_id": clean_client_order_id,
                "risk_decision": risk_decision.to_dict(),
            },
        )

        try:
            reservation = self.ledger.reserve(
                correlation_id=clean_correlation_id,
                client_order_id=clean_client_order_id,
                symbol=clean_symbol,
                side=clean_side,
                notional=clean_notional,
            )
        except DuplicateReservationError as exc:
            self._set_status(order_intent_id, "PAPER_REJECTED", risk_reasons=[str(exc)])
            self.event_logger.record(
                "paper_trade_adjusted",
                correlation_id=clean_correlation_id,
                symbol=clean_symbol,
                payload={
                    "order_intent_id": order_intent_id,
                    "client_order_id": clean_client_order_id,
                    "status": "PAPER_REJECTED",
                    "reason": str(exc),
                },
            )
            raise DuplicateClientOrderError(str(exc)) from exc

        self._attach_reservation(order_intent_id, reservation.reservation_id)
        self._set_status(order_intent_id, "CAPITAL_RESERVED")
        submitted = self._set_status(order_intent_id, "PAPER_SUBMITTED")
        self.event_logger.record(
            "paper_trade_adjusted",
            correlation_id=clean_correlation_id,
            symbol=clean_symbol,
            payload={
                "order_intent_id": order_intent_id,
                "client_order_id": clean_client_order_id,
                "reservation_id": reservation.reservation_id,
                "status": "PAPER_SUBMITTED",
            },
        )
        return submitted

    def fill_order(self, client_order_id: str) -> PaperOrderIntent:
        intent = self._get_by_client_order_id(client_order_id)
        if intent.status != "PAPER_SUBMITTED":
            raise InvalidOrderStateError(
                f"order must be PAPER_SUBMITTED to fill: {intent.status}"
            )
        if not intent.reservation_id:
            raise InvalidOrderStateError("order has no reservation_id")
        self.ledger.mark_filled(intent.reservation_id)
        updated = self._set_status(intent.order_intent_id, "PAPER_FILLED")
        self._record_paper_lifecycle(updated, "PAPER_FILLED")
        return updated

    def cancel_order(self, client_order_id: str) -> PaperOrderIntent:
        return self._release_order(client_order_id, "PAPER_CANCELLED", "CANCELLED")

    def reject_order(
        self,
        client_order_id: str,
        reason: str = "paper_rejected",
    ) -> PaperOrderIntent:
        return self._release_order(client_order_id, "PAPER_REJECTED", "REJECTED", reason=reason)

    def get_order(self, client_order_id: str) -> PaperOrderIntent | None:
        state = self.repository.load()
        raw = find_intent_by_client_order_id(state, client_order_id)
        return PaperOrderIntent(**raw) if isinstance(raw, dict) else None

    def list_orders(self) -> list[PaperOrderIntent]:
        state = self.repository.load()
        return [
            PaperOrderIntent(**raw)
            for raw in state.get("order_intents", {}).values()
            if isinstance(raw, dict)
        ]

    def _risk_rejected(self, order_intent_id: str, reasons: list[str]) -> PaperOrderIntent:
        updated = self._set_status(order_intent_id, "RISK_REJECTED", risk_reasons=reasons)
        self.event_logger.record(
            "risk_rejected",
            correlation_id=updated.correlation_id,
            symbol=updated.symbol,
            payload={
                "order_intent_id": updated.order_intent_id,
                "client_order_id": updated.client_order_id,
                "status": updated.status,
                "reasons": reasons,
            },
        )
        return updated

    def _release_order(
        self,
        client_order_id: str,
        order_status: str,
        reservation_status: str,
        *,
        reason: str | None = None,
    ) -> PaperOrderIntent:
        intent = self._get_by_client_order_id(client_order_id)
        if intent.status != "PAPER_SUBMITTED":
            raise InvalidOrderStateError(
                f"order must be PAPER_SUBMITTED to release: {intent.status}"
            )
        if not intent.reservation_id:
            raise InvalidOrderStateError("order has no reservation_id")
        try:
            self.ledger.release(intent.reservation_id, status=reservation_status, reason=reason)
        except InvalidReservationStateError as exc:
            raise InvalidOrderStateError(str(exc)) from exc
        updated = self._set_status(intent.order_intent_id, order_status)
        self._record_paper_lifecycle(updated, order_status, reason=reason)
        return updated

    def _get_by_client_order_id(self, client_order_id: str) -> PaperOrderIntent:
        clean_client_order_id = require_text(client_order_id, "client_order_id")
        intent = self.get_order(clean_client_order_id)
        if intent is None:
            raise InvalidOrderIntentError(f"order intent not found: {clean_client_order_id}")
        return intent

    def _set_status(
        self,
        order_intent_id: str,
        status: str,
        *,
        risk_reasons: list[str] | None = None,
    ) -> PaperOrderIntent:
        clean_status = normalize_status(status)
        updated: dict[str, Any] = {}

        def mutate(state: dict[str, Any]) -> None:
            order_intents = state.setdefault("order_intents", {})
            current = order_intents.get(order_intent_id)
            if not isinstance(current, dict):
                raise InvalidOrderIntentError(f"order intent not found: {order_intent_id}")
            if current.get("status") in TERMINAL_ORDER_STATES:
                raise InvalidOrderStateError(
                    f"order is terminal: {current.get('status')}"
                )
            current["status"] = clean_status
            current["updated_at"] = utc_timestamp()
            if risk_reasons is not None:
                current["risk_reasons"] = list(risk_reasons)
            append_order_event(state, order_intent_id, clean_status)
            updated.update(current)

        self.repository.update(mutate)
        return PaperOrderIntent(**updated)

    def _attach_reservation(self, order_intent_id: str, reservation_id: str) -> None:
        def mutate(state: dict[str, Any]) -> None:
            current = state.setdefault("order_intents", {}).get(order_intent_id)
            if not isinstance(current, dict):
                raise InvalidOrderIntentError(f"order intent not found: {order_intent_id}")
            current["reservation_id"] = reservation_id
            current["updated_at"] = utc_timestamp()

        self.repository.update(mutate)

    def _record_paper_lifecycle(
        self,
        intent: PaperOrderIntent,
        status: str,
        *,
        reason: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "order_intent_id": intent.order_intent_id,
            "client_order_id": intent.client_order_id,
            "reservation_id": intent.reservation_id,
            "status": status,
        }
        if reason:
            payload["reason"] = reason
        self.event_logger.record(
            "paper_trade_adjusted",
            correlation_id=intent.correlation_id,
            symbol=intent.symbol,
            payload=payload,
        )


def assert_order_runtime_safe(runtime_mode: str) -> None:
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
        raise OrderSafetyError("unsafe order manager runtime: " + ",".join(reasons))


def find_intent_by_client_order_id(
    state: dict[str, Any],
    client_order_id: str,
) -> dict[str, Any] | None:
    for intent in state.get("order_intents", {}).values():
        if isinstance(intent, dict) and str(intent.get("client_order_id")) == client_order_id:
            return intent
    return None


def append_order_event(state: dict[str, Any], order_intent_id: str, status: str) -> None:
    state.setdefault("order_events", []).append(
        {
            "order_intent_id": order_intent_id,
            "status": status,
            "created_at": utc_timestamp(),
        }
    )


def require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise InvalidOrderIntentError(f"{field_name} is required")
    return text


def require_positive_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise InvalidOrderIntentError(f"{field_name} must be a positive number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InvalidOrderIntentError(f"{field_name} must be a positive number") from None
    if number <= 0:
        raise InvalidOrderIntentError(f"{field_name} must be a positive number")
    return number


def normalize_symbol(symbol: Any) -> str:
    return require_text(symbol, "symbol").upper()


def normalize_side(side: Any) -> str:
    normalized = require_text(side, "side").upper()
    if normalized in {"BUY", "LONG"}:
        return "LONG"
    if normalized in {"SELL", "SHORT"}:
        return "SHORT"
    raise InvalidOrderIntentError(f"unsupported side: {side}")


def normalize_status(status: str) -> str:
    normalized = require_text(status, "status").upper()
    if normalized not in ORDER_STATES:
        raise InvalidOrderStateError(f"unsupported order status: {status}")
    return normalized
