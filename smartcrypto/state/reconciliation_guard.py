from __future__ import annotations

import json
import csv
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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
DEFAULT_AUDIT_REPORT_PATH = Path("data/reports/state_reconciliation_audit_report.json")
SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
    "sends_orders",
    "changes_risk",
)


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

    def audit(
        self,
        *,
        snapshot_path: str | Path | None = None,
        report_path: str | Path | None = DEFAULT_AUDIT_REPORT_PATH,
        strict: bool = False,
    ) -> dict[str, Any]:
        return run_state_reconciliation_audit(
            repository_path=self.repository.path,
            snapshot_path=snapshot_path,
            report_path=report_path,
            strict=strict,
            runtime_mode=self.runtime_mode,
        )

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


def run_state_reconciliation_audit(
    *,
    repository_path: str | Path,
    snapshot_path: str | Path | None = None,
    report_path: str | Path | None = DEFAULT_AUDIT_REPORT_PATH,
    strict: bool = False,
    runtime_mode: str = "paper",
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assert_runtime_safe(runtime_mode)
    report = base_audit_report(
        repository_path=str(repository_path),
        snapshot_path=str(snapshot_path) if snapshot_path else None,
    )
    safety = safety_payload(safety_overrides)
    report.update(safety)
    unsafe_safety = unsafe_safety_flags(safety)
    if unsafe_safety:
        report["blocking_findings"].extend(f"unsafe_safety_flag:{flag}" for flag in unsafe_safety)

    repository_target = Path(repository_path)
    if not repository_target.exists():
        report["status"] = "missing_data"
        report["reason"] = "missing_repository"
        report["reconciliation_required"] = True
        report["recommended_mode"] = "RECONCILING"
        report["blocking_findings"].append(f"missing_repository:{repository_target}")
        write_report(report, report_path)
        return report

    try:
        repository_state = load_repository_state(repository_target, runtime_mode=runtime_mode)
    except Exception as exc:
        report["status"] = "blocked"
        report["reason"] = f"repository_unreadable:{exc}"
        report["reconciliation_required"] = True
        report["recommended_mode"] = "RECONCILING"
        report["blocking_findings"].append(f"repository_unreadable:{exc}")
        write_report(report, report_path)
        return report

    snapshot_positions: list[dict[str, Any]] | None = None
    if snapshot_path:
        snapshot_target = Path(snapshot_path)
        if not snapshot_target.exists():
            report["status"] = "missing_data"
            report["reason"] = "missing_snapshot"
            report["reconciliation_required"] = True
            report["recommended_mode"] = "RECONCILING"
            report["blocking_findings"].append(f"missing_snapshot:{snapshot_target}")
            write_report(report, report_path)
            return report
        snapshot_payload = load_snapshot(snapshot_target)
        snapshot_positions = normalize_positions(extract_collection(snapshot_payload, "positions"))

    positions = normalize_positions(repository_state.get("positions", []))
    order_intents = normalize_collection(repository_state.get("order_intents", []))
    simulated_orders = normalize_collection(repository_state.get("simulated_orders", []))
    reservations = normalize_collection(repository_state.get("capital_reservations", repository_state.get("reservations", [])))
    state_locks = normalize_collection(repository_state.get("state_locks", []))
    dispatch_locks = normalize_collection(repository_state.get("dispatch_locks", []))

    report["positions_count"] = len(positions)
    report["order_intents_count"] = len(order_intents)
    report["capital_reservations_count"] = len(reservations)

    report["duplicate_client_order_id_count"] = count_duplicate_client_order_ids(order_intents)
    report["negative_reserved_capital_count"] = count_negative_reservations(reservations)
    report["dispatch_unknown_count"] = count_dispatch_unknown(order_intents, simulated_orders)
    report["partial_fill_inconsistency_count"] = count_partial_fill_inconsistencies(reservations, simulated_orders)

    local_order_ids = {clean_text(row.get("client_order_id")) for row in order_intents if clean_text(row.get("client_order_id"))}
    for reservation in reservations:
        reservation_order_id = clean_text(reservation.get("client_order_id"))
        if reservation_order_id and reservation_order_id not in local_order_ids:
            report["blocking_findings"].append(f"capital_reserved_without_order:{reservation_order_id}")
    for order in simulated_orders:
        if not clean_text(order.get("client_order_id")):
            report["blocking_findings"].append(f"simulated_order_without_client_order_id:{order.get('simulated_order_id')}")

    report["state_divergence_count"] = count_state_divergences(positions, snapshot_positions, report["blocking_findings"])
    expired_locks = expired_lock_findings(state_locks, "state_lock") + expired_lock_findings(dispatch_locks, "dispatch_lock")
    report["warnings"].extend(expired_locks)
    if strict:
        report["blocking_findings"].extend(f"strict_warning:{warning}" for warning in expired_locks)

    if report["duplicate_client_order_id_count"] > 0:
        report["blocking_findings"].append("duplicate_client_order_id_detected")
    if report["negative_reserved_capital_count"] > 0:
        report["blocking_findings"].append("negative_reserved_capital_detected")
    if report["dispatch_unknown_count"] > 0:
        report["blocking_findings"].append("dispatch_unknown_active")
    if report["partial_fill_inconsistency_count"] > 0:
        report["blocking_findings"].append("partial_fill_inconsistent")
    if report["state_divergence_count"] > 0:
        report["blocking_findings"].append("state_divergence_detected")

    report["blocking_findings"] = sorted(set(report["blocking_findings"]))
    report["warnings"] = sorted(set(report["warnings"]))
    report["reconciliation_required"] = bool(report["blocking_findings"])
    report["recommended_mode"] = recommended_mode(report)
    if report["blocking_findings"]:
        report["status"] = "blocked"
        report["reason"] = "reconciliation_required"
    elif report["warnings"]:
        report["status"] = "warning"
        report["reason"] = "reconciliation_warnings"
    else:
        report["status"] = "ok"
        report["reason"] = "state_reconciled"
    write_report(report, report_path)
    return report


def base_audit_report(*, repository_path: str, snapshot_path: str | None) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": None,
        "generated_at_utc": utc_timestamp(),
        "repository_path": repository_path,
        "snapshot_path": snapshot_path,
        "reconciliation_required": True,
        "recommended_mode": "RECONCILING",
        "positions_count": 0,
        "order_intents_count": 0,
        "capital_reservations_count": 0,
        "dispatch_unknown_count": 0,
        "partial_fill_inconsistency_count": 0,
        "duplicate_client_order_id_count": 0,
        "negative_reserved_capital_count": 0,
        "state_divergence_count": 0,
        "blocking_findings": [],
        "warnings": [],
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def load_repository_state(path: Path, *, runtime_mode: str) -> dict[str, Any]:
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return StateRepository(path, runtime_mode=runtime_mode).export_state()
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ReconciliationGuardError("repository_root_not_object")
    return payload


def load_snapshot(path: Path) -> Any:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    if suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    if suffix == ".parquet":
        import pandas as pd

        return pd.read_parquet(path).to_dict(orient="records")
    raise ReconciliationGuardError(f"unsupported_snapshot_format:{suffix}")


def extract_collection(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get(key, payload.get("data", []))
        return normalize_collection(value)
    return normalize_collection(payload)


def normalize_collection(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [dict(row) for row in value.values() if isinstance(row, dict)]
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, dict)]
    return []


def normalize_positions(value: Any) -> list[dict[str, Any]]:
    positions = []
    for row in normalize_collection(value):
        symbol = clean_text(row.get("symbol") or row.get("pair") or row.get("moeda"))
        side = clean_text(row.get("side") or row.get("direction"))
        quantity = numeric(row.get("quantity", row.get("amount", row.get("qty", 0))))
        if symbol:
            positions.append(
                {
                    **row,
                    "symbol": symbol.upper().replace("/", "").replace(":USDT", ""),
                    "side": {"buy": "long", "sell": "short"}.get(side.lower(), side.lower()),
                    "quantity": quantity,
                    "state_hash": clean_text(row.get("state_hash")),
                }
            )
    return positions


def count_duplicate_client_order_ids(rows: list[dict[str, Any]]) -> int:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        client_order_id = clean_text(row.get("client_order_id"))
        if not client_order_id:
            continue
        if client_order_id in seen:
            duplicates.add(client_order_id)
        seen.add(client_order_id)
    return len(duplicates)


def count_negative_reservations(rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        reserved = numeric(row.get("reserved_capital", row.get("remaining_reserved_capital", row.get("notional", 0))))
        remaining = numeric(row.get("remaining_reserved_capital", reserved))
        if reserved < 0 or remaining < 0:
            count += 1
    return count


def count_dispatch_unknown(order_intents: list[dict[str, Any]], simulated_orders: list[dict[str, Any]]) -> int:
    client_ids: set[str] = set()
    for row in order_intents + simulated_orders:
        status = clean_text(row.get("status")).upper()
        if status == "DISPATCH_UNKNOWN" or bool(row.get("dispatch_unknown")):
            client_ids.add(clean_text(row.get("client_order_id")) or clean_text(row.get("order_intent_id")))
    return len(client_ids)


def count_partial_fill_inconsistencies(
    reservations: list[dict[str, Any]],
    simulated_orders: list[dict[str, Any]],
) -> int:
    count = 0
    by_client_order_id = {
        clean_text(row.get("client_order_id")): row
        for row in simulated_orders
        if clean_text(row.get("client_order_id"))
    }
    for reservation in reservations:
        status = clean_text(reservation.get("status")).upper()
        order_id = clean_text(reservation.get("client_order_id"))
        if status != "PARTIAL_FILLED":
            continue
        reserved = numeric(reservation.get("reserved_capital", reservation.get("notional", 0)))
        filled = numeric(reservation.get("filled_notional", 0))
        remaining = numeric(reservation.get("remaining_reserved_capital", 0))
        if filled < 0 or remaining < 0 or not nearly_equal(filled + remaining, reserved):
            count += 1
            continue
        simulated = by_client_order_id.get(order_id)
        if not simulated or clean_text(simulated.get("status")).upper() != "PARTIAL_FILLED":
            count += 1
    return count


def count_state_divergences(
    local_positions: list[dict[str, Any]],
    snapshot_positions: list[dict[str, Any]] | None,
    findings: list[str],
) -> int:
    if snapshot_positions is None:
        return 0
    divergences = 0
    local = {position_key(row): row for row in local_positions}
    external = {position_key(row): row for row in snapshot_positions}
    for key, local_row in local.items():
        if key not in external:
            findings.append(f"local_position_missing_in_snapshot:{key}")
            divergences += 1
            continue
        external_row = external[key]
        if not nearly_equal(numeric(local_row.get("quantity")), numeric(external_row.get("quantity"))):
            findings.append(f"position_quantity_divergence:{key}")
            divergences += 1
        if clean_text(local_row.get("side")).lower() != clean_text(external_row.get("side")).lower():
            findings.append(f"position_side_divergence:{key}")
            divergences += 1
        local_hash = clean_text(local_row.get("state_hash"))
        external_hash = clean_text(external_row.get("state_hash"))
        if local_hash and external_hash and local_hash != external_hash:
            findings.append(f"state_hash_divergence:{key}")
            divergences += 1
    for key in external:
        if key not in local:
            findings.append(f"snapshot_position_missing_locally:{key}")
            divergences += 1
    return divergences


def expired_lock_findings(rows: list[dict[str, Any]], label: str) -> list[str]:
    now = datetime.now(timezone.utc)
    findings = []
    for row in rows:
        if clean_text(row.get("status")).upper() != "ACTIVE":
            continue
        expires = parse_utc(row.get("expires_at_utc"))
        if expires is not None and expires < now:
            findings.append(f"expired_{label}:{row.get('lock_id')}")
    return findings


def recommended_mode(report: dict[str, Any]) -> str:
    if not report["blocking_findings"] and not report["warnings"]:
        return "NORMAL"
    if report["state_divergence_count"] > 0 or report["dispatch_unknown_count"] > 0:
        return "RECONCILING"
    if report["negative_reserved_capital_count"] > 0 or unsafe_safety_flags(report):
        return "PANIC"
    return "PROTECTION"


def write_report(report: dict[str, Any], report_path: str | Path | None) -> None:
    if report_path is None:
        return
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


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


def unsafe_safety_flags(payload: dict[str, Any]) -> list[str]:
    unsafe = []
    if payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    for flag in SAFE_FALSE_FLAGS:
        if payload.get(flag):
            unsafe.append(flag)
    return unsafe


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def position_key(row: dict[str, Any]) -> str:
    return f"{clean_text(row.get('symbol')).upper()}:{clean_text(row.get('side')).lower()}"


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
