"""Isolated deterministic testnet E2E harness for B06.

The harness never imports an exchange SDK and never accesses a production
endpoint. A real external testnet adapter may implement the same protocols in a
separate, explicitly authorized execution environment.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class TestnetSignal:
    signal_id: str
    symbol: str
    side: str
    quantity: float
    limit_price: float

    @property
    def notional(self) -> float:
        return self.quantity * self.limit_price


@dataclass
class TestnetOrder:
    order_id: str
    signal_id: str
    symbol: str
    side: str
    quantity: float
    limit_price: float
    filled_quantity: float = 0.0
    status: str = "open"


class TestnetRiskGate(Protocol):
    def approve(self, signal: TestnetSignal) -> bool:
        """Return whether an isolated testnet signal is admissible."""


class TestnetGateway(Protocol):
    environment: str
    endpoint_class: str
    real_order: bool
    evidence_class: str
    testnet_order_submitted: bool

    def submit(self, signal: TestnetSignal) -> TestnetOrder: ...

    def partial_fill(self, order_id: str, quantity: float) -> TestnetOrder: ...

    def cancel(self, order_id: str) -> TestnetOrder: ...

    def reconcile(self) -> Mapping[str, Any]: ...

    def restart_and_recover(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ConservativeTestnetRiskGate:
    maximum_notional: float = 10_000.0
    allowed_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")

    def approve(self, signal: TestnetSignal) -> bool:
        return (
            signal.signal_id != ""
            and signal.symbol in self.allowed_symbols
            and signal.side in {"long", "short"}
            and signal.quantity > 0
            and signal.limit_price > 0
            and signal.notional <= self.maximum_notional
        )


class InMemoryTestnetGateway:
    """Deterministic isolated gateway used by tests and sandbox probes."""

    environment = "testnet"
    endpoint_class = "testnet"
    real_order = False
    evidence_class = "isolated_harness"
    testnet_order_submitted = False

    def __init__(self) -> None:
        self._orders: dict[str, TestnetOrder] = {}
        self._signal_index: dict[str, str] = {}

    def submit(self, signal: TestnetSignal) -> TestnetOrder:
        if signal.signal_id in self._signal_index:
            return self._orders[self._signal_index[signal.signal_id]]
        order_id = _stable_order_id(signal)
        order = TestnetOrder(
            order_id=order_id,
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.side,
            quantity=signal.quantity,
            limit_price=signal.limit_price,
        )
        self._orders[order_id] = order
        self._signal_index[signal.signal_id] = order_id
        return order

    def partial_fill(self, order_id: str, quantity: float) -> TestnetOrder:
        order = self._orders[order_id]
        if order.status != "open":
            raise ValueError("order_not_open")
        if quantity <= 0 or quantity >= order.quantity:
            raise ValueError("partial_fill_quantity_invalid")
        order.filled_quantity = quantity
        order.status = "partially_filled"
        return order

    def cancel(self, order_id: str) -> TestnetOrder:
        order = self._orders[order_id]
        if order.status not in {"open", "partially_filled"}:
            raise ValueError("order_not_cancellable")
        order.status = "cancelled"
        return order

    def reconcile(self) -> Mapping[str, Any]:
        return self._snapshot("reconciled")

    def restart_and_recover(self) -> Mapping[str, Any]:
        serialized = json.dumps(
            [asdict(order) for order in self._orders.values()],
            sort_keys=True,
        )
        recovered = json.loads(serialized)
        recovered_ids = sorted(str(item["order_id"]) for item in recovered)
        return {
            "status": "recovered",
            "order_count": len(recovered),
            "order_ids": recovered_ids,
            "duplicate_order_count": len(recovered_ids) - len(set(recovered_ids)),
        }

    def _snapshot(self, status: str) -> dict[str, Any]:
        orders = [asdict(order) for order in self._orders.values()]
        order_ids = [str(item["order_id"]) for item in orders]
        return {
            "status": status,
            "order_count": len(orders),
            "orders": orders,
            "duplicate_order_count": len(order_ids) - len(set(order_ids)),
        }


def _stable_order_id(signal: TestnetSignal) -> str:
    payload = json.dumps(asdict(signal), sort_keys=True, separators=(",", ":"))
    return "testnet-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def run_isolated_testnet_e2e(
    *,
    run_id: str,
    signal: TestnetSignal,
    risk_gate: TestnetRiskGate | None = None,
    gateway: TestnetGateway | None = None,
    partial_fill_ratio: float = 0.40,
) -> dict[str, Any]:
    """Execute one complete isolated testnet lifecycle and return evidence."""

    resolved_risk_gate = risk_gate or ConservativeTestnetRiskGate()
    resolved_gateway = gateway or InMemoryTestnetGateway()
    stages = {
        stage: False
        for stage in (
            "signal_created",
            "risk_approved",
            "order_submitted_testnet",
            "partial_fill_observed",
            "cancel_observed",
            "reconciliation_complete",
            "restart_recovery_complete",
        )
    }
    blockers: list[str] = []

    if not run_id:
        blockers.append("run_id_missing")
    if resolved_gateway.environment != "testnet":
        blockers.append("gateway_environment_not_testnet")
    if resolved_gateway.endpoint_class != "testnet":
        blockers.append("gateway_endpoint_not_testnet")
    if resolved_gateway.real_order is not False:
        blockers.append("real_order_gateway_forbidden")
    if blockers:
        return _evidence(run_id, resolved_gateway, stages, blockers)

    stages["signal_created"] = True
    if not resolved_risk_gate.approve(signal):
        blockers.append("risk_gate_rejected_signal")
        return _evidence(run_id, resolved_gateway, stages, blockers)
    stages["risk_approved"] = True

    try:
        order = resolved_gateway.submit(signal)
        stages["order_submitted_testnet"] = True
        partial_quantity = signal.quantity * partial_fill_ratio
        filled = resolved_gateway.partial_fill(order.order_id, partial_quantity)
        stages["partial_fill_observed"] = 0 < filled.filled_quantity < filled.quantity
        cancelled = resolved_gateway.cancel(order.order_id)
        stages["cancel_observed"] = cancelled.status == "cancelled"
        reconciliation = dict(resolved_gateway.reconcile())
        stages["reconciliation_complete"] = (
            reconciliation.get("status") == "reconciled"
            and reconciliation.get("duplicate_order_count") == 0
        )
        recovery = dict(resolved_gateway.restart_and_recover())
        stages["restart_recovery_complete"] = (
            recovery.get("status") == "recovered"
            and recovery.get("duplicate_order_count") == 0
            and recovery.get("order_count") == reconciliation.get("order_count")
        )
    except (KeyError, TypeError, ValueError) as exc:
        blockers.append(f"testnet_harness_error:{exc.__class__.__name__}")

    blockers.extend(
        f"stage_failed:{name}"
        for name, completed in stages.items()
        if completed is not True
    )
    return _evidence(run_id, resolved_gateway, stages, blockers)


def _evidence(
    run_id: str,
    gateway: TestnetGateway,
    stages: Mapping[str, bool],
    blockers: list[str],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "environment": gateway.environment,
        "endpoint_class": gateway.endpoint_class,
        "real_order": gateway.real_order,
        "evidence_class": gateway.evidence_class,
        "testnet_order_submitted": gateway.testnet_order_submitted,
        "active_runtime_touched": False,
        "status": "pass" if not blockers else "blocked",
        "stages": dict(stages),
        "blockers": sorted(set(blockers)),
        "sends_orders": False,
        "exchange_private_access": False,
        "production_endpoint_accessed": False,
    }
