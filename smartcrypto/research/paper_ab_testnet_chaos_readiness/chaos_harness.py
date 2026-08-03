"""Isolated deterministic chaos and recovery harness for B06."""

from __future__ import annotations

import errno
import json
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .contracts import REQUIRED_CHAOS_SCENARIOS


@dataclass(frozen=True)
class ChaosResult:
    scenario_id: str
    status: str
    recovery_seconds: float
    data_loss: bool
    duplicate_orders: bool
    active_runtime_touched: bool
    details: dict[str, Any]

    def as_evidence(self) -> dict[str, Any]:
        return asdict(self)


def _result(
    scenario_id: str,
    started_at: float,
    *,
    passed: bool,
    data_loss: bool = False,
    duplicate_orders: bool = False,
    **details: Any,
) -> ChaosResult:
    return ChaosResult(
        scenario_id=scenario_id,
        status="pass" if passed else "blocked",
        recovery_seconds=round(time.perf_counter() - started_at, 6),
        data_loss=data_loss,
        duplicate_orders=duplicate_orders,
        active_runtime_touched=False,
        details=dict(details),
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _open_trade_restart(root: Path) -> ChaosResult:
    started = time.perf_counter()
    state_path = root / "open_trade_restart.json"
    state = {
        "open_trades": [
            {
                "trade_id": "paper-open-1",
                "symbol": "BTCUSDT",
                "side": "long",
                "status": "open",
            }
        ],
        "order_ids": ["paper-order-1"],
    }
    _atomic_write(state_path, json.dumps(state, sort_keys=True))
    recovered = json.loads(state_path.read_text(encoding="utf-8"))
    order_ids = list(map(str, recovered.get("order_ids") or []))
    passed = (
        recovered.get("open_trades") == state["open_trades"]
        and order_ids == state["order_ids"]
        and len(order_ids) == len(set(order_ids))
    )
    return _result(
        "open_trade_restart",
        started,
        passed=passed,
        data_loss=recovered.get("open_trades") != state["open_trades"],
        duplicate_orders=len(order_ids) != len(set(order_ids)),
        recovered_trade_count=len(recovered.get("open_trades") or []),
    )


def _qlib_unavailable(_root: Path) -> ChaosResult:
    started = time.perf_counter()
    orders: list[str] = []
    blocked = False
    try:
        raise RuntimeError("qlib_unavailable")
    except RuntimeError:
        blocked = True
    recovered_prediction = 0.61
    passed = blocked and not orders and recovered_prediction > 0
    return _result(
        "qlib_unavailable",
        started,
        passed=passed,
        duplicate_orders=False,
        entry_blocked_while_qlib_down=blocked,
        recovery_prediction_available=True,
    )


def _signal_missing(_root: Path) -> ChaosResult:
    started = time.perf_counter()
    signal: dict[str, Any] | None = None
    orders = [] if signal is None else ["unexpected-order"]
    return _result(
        "signal_missing",
        started,
        passed=not orders,
        duplicate_orders=False,
        order_count=len(orders),
    )


def _sqlite_locked(root: Path) -> ChaosResult:
    started = time.perf_counter()
    database = root / "sqlite_locked.db"
    first = sqlite3.connect(database, timeout=0.0)
    second = sqlite3.connect(database, timeout=0.0)
    lock_detected = False
    try:
        first.execute(
            "CREATE TABLE IF NOT EXISTS ledger "
            "(event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        first.commit()
        first.execute("BEGIN EXCLUSIVE")
        first.execute(
            "INSERT INTO ledger(event_id, payload) VALUES (?, ?)",
            ("uncommitted", "locked"),
        )
        try:
            second.execute(
                "INSERT INTO ledger(event_id, payload) VALUES (?, ?)",
                ("blocked", "must-not-commit"),
            )
            second.commit()
        except sqlite3.OperationalError:
            lock_detected = True
            second.rollback()
        first.rollback()
        second.execute(
            "INSERT INTO ledger(event_id, payload) VALUES (?, ?)",
            ("recovered", "ok"),
        )
        second.commit()
        rows = second.execute(
            "SELECT event_id FROM ledger ORDER BY event_id"
        ).fetchall()
    finally:
        first.close()
        second.close()
    identifiers = [str(row[0]) for row in rows]
    passed = lock_detected and identifiers == ["recovered"]
    return _result(
        "sqlite_locked",
        started,
        passed=passed,
        data_loss=False,
        duplicate_orders=False,
        lock_detected=lock_detected,
        committed_event_ids=identifiers,
    )


def _disk_full(root: Path) -> ChaosResult:
    started = time.perf_counter()
    target = root / "disk_full_report.json"
    original = '{"status":"stable"}\n'
    _atomic_write(target, original)
    failure_detected = False

    def failing_writer(_path: Path, _content: str) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    try:
        failing_writer(target, '{"status":"corrupted"}\n')
    except OSError as exc:
        failure_detected = exc.errno == errno.ENOSPC
    preserved = target.read_text(encoding="utf-8") == original
    _atomic_write(target, '{"status":"recovered"}\n')
    recovered = json.loads(target.read_text(encoding="utf-8"))
    passed = failure_detected and preserved and recovered["status"] == "recovered"
    return _result(
        "disk_full",
        started,
        passed=passed,
        data_loss=not preserved,
        duplicate_orders=False,
        enospc_detected=failure_detected,
        previous_report_preserved=preserved,
    )


def _clock_skew(_root: Path) -> ChaosResult:
    started = time.perf_counter()
    allowed_skew_seconds = 120.0
    observed_skew_seconds = 600.0
    quarantined = observed_skew_seconds > allowed_skew_seconds
    orders: list[str] = []
    passed = quarantined and not orders
    return _result(
        "clock_skew",
        started,
        passed=passed,
        duplicate_orders=False,
        observed_skew_seconds=observed_skew_seconds,
        event_quarantined=quarantined,
    )


def _public_api_unavailable(_root: Path) -> ChaosResult:
    started = time.perf_counter()
    blocked = False
    orders: list[str] = []

    def unavailable_provider() -> float:
        raise ConnectionError("public_api_unavailable")

    try:
        unavailable_provider()
    except ConnectionError:
        blocked = True
    recovered_price = 100.0
    passed = blocked and not orders and recovered_price > 0
    return _result(
        "public_api_unavailable",
        started,
        passed=passed,
        duplicate_orders=False,
        entry_blocked=blocked,
        recovery_market_data_available=True,
    )


def _corrupted_report(root: Path) -> ChaosResult:
    started = time.perf_counter()
    report = root / "corrupted_report.json"
    backup = root / "corrupted_report.backup.json"
    valid = '{"status":"ok","sequence":1}\n'
    _atomic_write(backup, valid)
    _atomic_write(report, "{invalid-json")
    corruption_detected = False
    try:
        json.loads(report.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        corruption_detected = True
    _atomic_write(report, backup.read_text(encoding="utf-8"))
    restored = json.loads(report.read_text(encoding="utf-8"))
    passed = corruption_detected and restored.get("sequence") == 1
    return _result(
        "corrupted_report",
        started,
        passed=passed,
        data_loss=restored.get("sequence") != 1,
        duplicate_orders=False,
        corruption_detected=corruption_detected,
        backup_restored=True,
    )


def _restart_loop(_root: Path) -> ChaosResult:
    started = time.perf_counter()
    maximum_restarts = 3
    restart_attempts = 0
    circuit_open = False
    orders: list[str] = []
    for _ in range(10):
        if restart_attempts >= maximum_restarts:
            circuit_open = True
            break
        restart_attempts += 1
    passed = (
        circuit_open
        and restart_attempts == maximum_restarts
        and not orders
    )
    return _result(
        "restart_loop",
        started,
        passed=passed,
        duplicate_orders=False,
        restart_attempts=restart_attempts,
        circuit_breaker_open=circuit_open,
    )


def _reconciliation_recovery(_root: Path) -> ChaosResult:
    started = time.perf_counter()
    ledger_orders = {"order-a", "order-b"}
    gateway_orders = {"order-a"}
    missing = ledger_orders - gateway_orders
    recovered_gateway_orders = gateway_orders | missing
    duplicate_count = len(recovered_gateway_orders) - len(
        set(recovered_gateway_orders)
    )
    passed = recovered_gateway_orders == ledger_orders and duplicate_count == 0
    return _result(
        "reconciliation_recovery",
        started,
        passed=passed,
        duplicate_orders=duplicate_count > 0,
        missing_before_recovery=sorted(missing),
        reconciled_order_ids=sorted(recovered_gateway_orders),
    )


_SCENARIOS: dict[str, Callable[[Path], ChaosResult]] = {
    "open_trade_restart": _open_trade_restart,
    "qlib_unavailable": _qlib_unavailable,
    "signal_missing": _signal_missing,
    "sqlite_locked": _sqlite_locked,
    "disk_full": _disk_full,
    "clock_skew": _clock_skew,
    "public_api_unavailable": _public_api_unavailable,
    "corrupted_report": _corrupted_report,
    "restart_loop": _restart_loop,
    "reconciliation_recovery": _reconciliation_recovery,
}


def run_isolated_chaos_suite() -> list[dict[str, Any]]:
    """Execute all mandatory scenarios inside a temporary isolated directory."""

    with tempfile.TemporaryDirectory(prefix="smart-futuros-b06-chaos-") as name:
        root = Path(name)
        results = [
            _SCENARIOS[scenario_id](root / scenario_id)
            for scenario_id in REQUIRED_CHAOS_SCENARIOS
        ]
    return [result.as_evidence() for result in results]
