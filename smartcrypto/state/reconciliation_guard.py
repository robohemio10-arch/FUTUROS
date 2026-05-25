from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from smartcrypto.state.financial_event_log import (
    MINIMUM_EVENT_TYPES,
    RECONCILIATION_EVENT_TYPES,
    FinancialEventLogger,
)
from smartcrypto.state.state_repository import (
    DEFAULT_STATE_PATH,
    StateRepository,
    assert_runtime_safe,
    utc_timestamp,
)


RECONCILED = "RECONCILED"
DIVERGED = "DIVERGED"
CORRUPTED = "CORRUPTED"
VALID_STATUSES = {"RESERVED", "RELEASED", "FILLED", "REJECTED", "CANCELLED"}
FLOAT_TOLERANCE = 1e-9


class ReconciliationGuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReconciliationResult:
    status: str
    block_operation: bool
    divergences: list[str] = field(default_factory=list)
    corruptions: list[str] = field(default_factory=list)
    checked_at: str = field(default_factory=utc_timestamp)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReconciliationGuard:
    def __init__(
        self,
        *,
        repository: StateRepository | None = None,
        state_path: str | Path = DEFAULT_STATE_PATH,
        event_logger: FinancialEventLogger | None = None,
        event_log_path: str | Path = "data/runtime/reconciliation_guard_events.jsonl",
        runtime_mode: str = "paper",
        max_capital_global: float = 0.0,
    ) -> None:
        assert_runtime_safe(runtime_mode)
        self.runtime_mode = str(runtime_mode).strip().lower()
        self.repository = repository or StateRepository(
            state_path,
            runtime_mode=self.runtime_mode,
            max_capital_global=max_capital_global,
        )
        self.event_logger = event_logger or FinancialEventLogger(
            event_log_path,
            runtime_mode=self.runtime_mode,
            source="reconciliation_guard",
            allowed_event_types=MINIMUM_EVENT_TYPES | RECONCILIATION_EVENT_TYPES,
        )

    def reconcile(self) -> ReconciliationResult:
        try:
            state = self._load_raw_state()
            divergences: list[str] = []
            corruptions: list[str] = []
            self._validate_shape(state, corruptions)
            if corruptions:
                result = ReconciliationResult(
                    status=CORRUPTED,
                    block_operation=True,
                    corruptions=corruptions,
                )
                self._record_result(result)
                return result

            self._check_duplicate_client_order_ids(state, divergences)
            self._check_reservation_order_links(state, divergences)
            self._check_capital_consistency(state, divergences, corruptions)

            if corruptions:
                result = ReconciliationResult(
                    status=CORRUPTED,
                    block_operation=True,
                    divergences=divergences,
                    corruptions=corruptions,
                )
            elif divergences:
                result = ReconciliationResult(
                    status=DIVERGED,
                    block_operation=True,
                    divergences=divergences,
                )
            else:
                result = ReconciliationResult(status=RECONCILED, block_operation=False)
            self._record_result(result)
            return result
        except Exception as exc:
            result = ReconciliationResult(
                status=CORRUPTED,
                block_operation=True,
                corruptions=[f"reconciliation_exception:{exc}"],
            )
            self._record_result(result)
            return result

    def assert_reconciled(self) -> ReconciliationResult:
        result = self.reconcile()
        if result.block_operation:
            raise ReconciliationGuardError(
                f"state reconciliation blocked operation: {result.status}"
            )
        return result

    def _load_raw_state(self) -> dict[str, Any]:
        path = self.repository.path
        if not path.exists():
            return self.repository.load()
        try:
            with path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception as exc:
            raise ReconciliationGuardError(f"state_json_unreadable:{exc}") from exc
        if not isinstance(state, dict):
            raise ReconciliationGuardError("state_root_not_object")
        return state

    def _validate_shape(self, state: dict[str, Any], corruptions: list[str]) -> None:
        if not isinstance(state.get("capital"), dict):
            corruptions.append("capital_not_object")
        if not isinstance(state.get("reservations"), dict):
            corruptions.append("reservations_not_object")
            return
        if not isinstance(state.get("order_intents", {}), dict):
            corruptions.append("order_intents_not_object")
        for reservation_id, reservation in state.get("reservations", {}).items():
            if not isinstance(reservation, dict):
                corruptions.append(f"reservation_not_object:{reservation_id}")
                continue
            required = {
                "reservation_id",
                "correlation_id",
                "client_order_id",
                "symbol",
                "side",
                "notional",
                "status",
                "created_at",
            }
            missing = required - set(reservation)
            if missing:
                corruptions.append(
                    f"reservation_missing_fields:{reservation_id}:{sorted(missing)}"
                )
            if str(reservation.get("status", "")).upper() not in VALID_STATUSES:
                corruptions.append(
                    f"reservation_invalid_status:{reservation_id}:{reservation.get('status')}"
                )
            try:
                notional = float(reservation.get("notional"))
                if notional < 0:
                    corruptions.append(f"reservation_negative_notional:{reservation_id}")
            except (TypeError, ValueError):
                corruptions.append(f"reservation_invalid_notional:{reservation_id}")

    def _check_duplicate_client_order_ids(
        self,
        state: dict[str, Any],
        divergences: list[str],
    ) -> None:
        for collection_name in ("reservations", "order_intents"):
            seen: dict[str, str] = {}
            collection = state.get(collection_name, {})
            if not isinstance(collection, dict):
                continue
            for object_id, item in collection.items():
                if not isinstance(item, dict):
                    continue
                client_order_id = str(item.get("client_order_id") or "").strip()
                if not client_order_id:
                    continue
                if client_order_id in seen:
                    divergences.append(
                        f"duplicate_client_order_id:{collection_name}:"
                        f"{client_order_id}:{seen[client_order_id]}:{object_id}"
                    )
                else:
                    seen[client_order_id] = str(object_id)

    def _check_reservation_order_links(
        self,
        state: dict[str, Any],
        divergences: list[str],
    ) -> None:
        order_by_reservation_id = {}
        for order in state.get("order_intents", {}).values():
            if isinstance(order, dict) and order.get("reservation_id"):
                order_by_reservation_id[str(order["reservation_id"])] = order

        for reservation_id, reservation in state.get("reservations", {}).items():
            if not isinstance(reservation, dict):
                continue
            status = str(reservation.get("status", "")).upper()
            order = order_by_reservation_id.get(str(reservation_id))
            if status == "RESERVED":
                if not order:
                    divergences.append(f"open_reservation_without_order:{reservation_id}")
                elif order.get("status") != "PAPER_SUBMITTED":
                    divergences.append(
                        f"open_reservation_order_status_mismatch:"
                        f"{reservation_id}:{order.get('status')}"
                    )
            if status == "FILLED":
                if not order:
                    divergences.append(f"filled_reservation_without_order:{reservation_id}")
                elif order.get("status") != "PAPER_FILLED":
                    divergences.append(
                        f"filled_reservation_order_status_mismatch:"
                        f"{reservation_id}:{order.get('status')}"
                    )

    def _check_capital_consistency(
        self,
        state: dict[str, Any],
        divergences: list[str],
        corruptions: list[str],
    ) -> None:
        capital = state.get("capital")
        reservations = state.get("reservations")
        if not isinstance(capital, dict) or not isinstance(reservations, dict):
            return
        try:
            max_capital = float(capital.get("max_capital_global"))
            actual_reserved = float(capital.get("reserved_notional"))
            actual_filled = float(capital.get("filled_notional"))
            actual_available = float(capital.get("available_notional"))
        except (TypeError, ValueError):
            corruptions.append("capital_numeric_fields_invalid")
            return

        expected_reserved = 0.0
        expected_filled = 0.0
        for reservation in reservations.values():
            if not isinstance(reservation, dict):
                continue
            status = str(reservation.get("status", "")).upper()
            try:
                notional = float(reservation.get("notional", 0.0))
            except (TypeError, ValueError):
                continue
            if status == "RESERVED":
                expected_reserved += notional
            elif status == "FILLED":
                expected_filled += notional
        expected_available = max_capital - expected_reserved - expected_filled
        if not nearly_equal(actual_reserved, expected_reserved):
            divergences.append(
                f"reserved_notional_mismatch:actual={actual_reserved}:"
                f"expected={expected_reserved}"
            )
        if not nearly_equal(actual_filled, expected_filled):
            divergences.append(
                f"filled_notional_mismatch:actual={actual_filled}:expected={expected_filled}"
            )
        if not nearly_equal(actual_available, expected_available):
            divergences.append(
                f"available_notional_mismatch:actual={actual_available}:"
                f"expected={expected_available}"
            )

    def _record_result(self, result: ReconciliationResult) -> None:
        event_type = {
            RECONCILED: "state_reconciled",
            DIVERGED: "state_divergence_detected",
            CORRUPTED: "reconciliation_failed",
        }[result.status]
        self.event_logger.record(
            event_type,
            correlation_id=f"reconciliation-{result.checked_at}",
            payload=result.to_dict(),
        )


def nearly_equal(left: float, right: float) -> bool:
    return abs(left - right) <= FLOAT_TOLERANCE
