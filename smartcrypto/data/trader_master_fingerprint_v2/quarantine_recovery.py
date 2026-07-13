"""Fail-closed in-memory recovery for authoritative paper-trade forensics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any


RECOVERED = "recovered_authoritatively"
RECOVERY_SOURCE = "authoritative_orders_average_filled_v1"
RECOVERY_TRADE_IDS = frozenset({221, 234})
RECOVERY_ORDER_IDS = frozenset(f"freqtrade-paper-{trade_id}" for trade_id in RECOVERY_TRADE_IDS)
RECOVERY_METADATA_KEY = "_authoritative_forensic_recovery"
REQUIRED_EVIDENCE_TABLES = frozenset({"trades", "orders"})
REQUIRED_SOURCE_COLUMNS = frozenset({"orders.average", "orders.filled"})


class RecoveryValidationError(ValueError):
    """Raised when a forensic report violates the recovery contract."""


@dataclass(frozen=True, slots=True)
class AuthoritativeRecovery:
    """Immutable, fully validated recovery evidence for one paper trade."""

    trade_id: int
    order_id: str
    recovered_close_rate: Decimal
    verified_open_rate: Decimal
    filled_quantity: Decimal
    recovered_residual: Decimal
    formula_version: str
    evidence_tables: tuple[str, ...]
    evidence_row_ids: tuple[tuple[str, tuple[int, ...]], ...]
    source_columns: tuple[str, ...]
    epsilon: Decimal
    recovery_source: str = RECOVERY_SOURCE

    def provenance(self, *, original_close_rate: object) -> dict[str, Any]:
        """Return JSON-safe provenance carried only by an in-memory copy."""
        return {
            "trade_id": self.trade_id,
            "order_id": self.order_id,
            "original_close_rate": original_close_rate,
            "recovered_close_rate": format(self.recovered_close_rate, "f"),
            "verified_open_rate": format(self.verified_open_rate, "f"),
            "filled_quantity": format(self.filled_quantity, "f"),
            "recovered_residual": format(self.recovered_residual, "f"),
            "formula_version": self.formula_version,
            "evidence_tables": list(self.evidence_tables),
            "evidence_row_ids": {
                table: list(row_ids) for table, row_ids in self.evidence_row_ids
            },
            "source_columns": list(self.source_columns),
            "recovery_source": self.recovery_source,
            "close_rate_requested_used": False,
        }


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    """Immutable result of validating every fixed forensic candidate."""

    recoveries: Mapping[int, AuthoritativeRecovery]
    rejected_reasons: Mapping[int, tuple[str, ...]]
    candidate_trade_ids: tuple[int, ...]


def build_authoritative_recovery_map(
    forensic_report: Mapping[str, Any],
    *,
    epsilon: Decimal,
) -> Mapping[int, AuthoritativeRecovery]:
    """Build an immutable map containing only candidates that pass every gate."""
    return assess_authoritative_recovery_map(forensic_report, epsilon=epsilon).recoveries


def assess_authoritative_recovery_map(
    forensic_report: Mapping[str, Any],
    *,
    epsilon: Decimal,
) -> RecoveryAssessment:
    """Revalidate forensic evidence without trusting its decision text alone."""
    if epsilon <= 0 or not epsilon.is_finite():
        raise RecoveryValidationError("epsilon_invalid")
    if forensic_report.get("status") != "ok":
        raise RecoveryValidationError("forensic_report_not_ok")
    if forensic_report.get("recovery_applied") is not False:
        raise RecoveryValidationError("forensic_report_already_applied")
    results = forensic_report.get("trade_results")
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        raise RecoveryValidationError("forensic_trade_results_invalid")

    by_id: dict[int, Mapping[str, Any]] = {}
    candidate_ids: set[int] = set()
    for raw in results:
        if not isinstance(raw, Mapping):
            raise RecoveryValidationError("forensic_trade_result_invalid")
        trade_id = _int_or_none(raw.get("trade_id"))
        if trade_id is None or trade_id in by_id:
            raise RecoveryValidationError("forensic_trade_id_invalid_or_duplicate")
        by_id[trade_id] = raw
        if raw.get("recovery_decision") == RECOVERED:
            if trade_id not in RECOVERY_TRADE_IDS:
                raise RecoveryValidationError(f"recovery_trade_id_not_allowed:{trade_id}")
            candidate_ids.add(trade_id)

    recoveries: dict[int, AuthoritativeRecovery] = {}
    rejected: dict[int, tuple[str, ...]] = {}
    for trade_id in sorted(RECOVERY_TRADE_IDS):
        row = by_id.get(trade_id)
        if row is None:
            rejected[trade_id] = ("forensic_trade_result_missing",)
            continue
        if row.get("recovery_decision") != RECOVERED:
            rejected[trade_id] = ("recovery_decision_not_authoritative",)
            continue
        errors, recovery = _validate_candidate(row, epsilon=epsilon)
        if errors or recovery is None:
            rejected[trade_id] = tuple(sorted(set(errors or ["recovery_candidate_invalid"])))
            continue
        recoveries[trade_id] = recovery

    return RecoveryAssessment(
        recoveries=MappingProxyType(recoveries),
        rejected_reasons=MappingProxyType(rejected),
        candidate_trade_ids=tuple(sorted(candidate_ids)),
    )


def apply_authoritative_recoveries(
    sqlite_records: Sequence[Mapping[str, Any]],
    recovery_map: Mapping[int, AuthoritativeRecovery],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply close-rate overrides to copies while rechecking source-row identity."""
    unexpected = sorted(set(recovery_map) - RECOVERY_TRADE_IDS)
    if unexpected:
        raise RecoveryValidationError(f"recovery_map_contains_unapproved_ids:{unexpected}")

    output: list[dict[str, Any]] = []
    applied: list[int] = []
    rejected: dict[int, list[str]] = {}
    seen: set[int] = set()
    for source_row in sqlite_records:
        copied = dict(source_row)
        trade_id = _int_or_none(source_row.get("id"))
        recovery = recovery_map.get(trade_id) if trade_id is not None else None
        if recovery is not None:
            seen.add(recovery.trade_id)
            errors = _source_row_errors(source_row, recovery)
            if errors:
                rejected[recovery.trade_id] = errors
            else:
                original_close_rate = source_row.get("close_rate")
                copied["close_rate"] = format(recovery.recovered_close_rate, "f")
                copied[RECOVERY_METADATA_KEY] = recovery.provenance(
                    original_close_rate=original_close_rate
                )
                applied.append(recovery.trade_id)
        output.append(copied)

    for missing_id in sorted(set(recovery_map) - seen):
        rejected[missing_id] = ["sqlite_trade_row_missing"]
    return output, {
        "applied_trade_ids": sorted(applied),
        "applied_order_ids": [f"freqtrade-paper-{trade_id}" for trade_id in sorted(applied)],
        "applied_count": len(applied),
        "rejected_trade_ids": sorted(rejected),
        "rejected_order_ids": [
            f"freqtrade-paper-{trade_id}" for trade_id in sorted(rejected)
        ],
        "rejected_reasons": {
            str(trade_id): sorted(set(reasons)) for trade_id, reasons in sorted(rejected.items())
        },
        "source_records_mutated": False,
        "close_rate_only_override": True,
    }


def _validate_candidate(
    row: Mapping[str, Any],
    *,
    epsilon: Decimal,
) -> tuple[list[str], AuthoritativeRecovery | None]:
    errors: list[str] = []
    trade_id = _int_or_none(row.get("trade_id"))
    if trade_id not in RECOVERY_TRADE_IDS:
        errors.append("trade_id_not_allowed")
    expected_order_id = f"freqtrade-paper-{trade_id}" if trade_id is not None else None
    if row.get("order_id") != expected_order_id or row.get("order_id") not in RECOVERY_ORDER_IDS:
        errors.append("order_id_not_allowed")
    if row.get("recovery_decision") != RECOVERED:
        errors.append("recovery_decision_not_authoritative")
    if row.get("close_rate_requested_used") is not False:
        errors.append("close_rate_requested_used")
    if row.get("recovery_applied") is not False:
        errors.append("forensic_row_already_applied")
    if row.get("remaining_blockers") not in ([], ()):
        errors.append("remaining_blockers_present")

    recovered_close = _positive_decimal(row.get("weighted_exit_price"), "weighted_exit_price", errors)
    weighted_entry = _positive_decimal(row.get("weighted_entry_price"), "weighted_entry_price", errors)
    verified_open = _positive_decimal(row.get("verified_open_rate"), "verified_open_rate", errors)
    entry_quantity = _positive_decimal(
        row.get("filled_entry_quantity"), "filled_entry_quantity", errors
    )
    exit_quantity = _positive_decimal(
        row.get("filled_exit_quantity"), "filled_exit_quantity", errors
    )
    amount_inventory = row.get("amount_inventory")
    trade_amount = _positive_decimal(
        amount_inventory.get("amount") if isinstance(amount_inventory, Mapping) else None,
        "trade_amount",
        errors,
    )
    residual = _non_negative_decimal(row.get("recovered_residual"), "recovered_residual", errors)

    if entry_quantity is not None and exit_quantity is not None:
        if abs(entry_quantity - exit_quantity) > epsilon:
            errors.append("filled_entry_exit_quantity_mismatch")
    if entry_quantity is not None and trade_amount is not None:
        if abs(entry_quantity - trade_amount) > epsilon:
            errors.append("filled_quantity_trade_amount_mismatch")
    if weighted_entry is not None and verified_open is not None:
        if abs(weighted_entry - verified_open) > epsilon:
            errors.append("weighted_entry_open_rate_mismatch")
    if residual is not None and residual > epsilon:
        errors.append("recovered_residual_above_epsilon")

    reported_profit = row.get("reported_profit")
    realized = _decimal_or_none(
        reported_profit.get("realized_profit") if isinstance(reported_profit, Mapping) else None
    )
    close_profit = _decimal_or_none(
        reported_profit.get("close_profit_abs") if isinstance(reported_profit, Mapping) else None
    )
    if (
        not isinstance(reported_profit, Mapping)
        or reported_profit.get("values_match") is not True
        or realized is None
        or close_profit is None
        or abs(realized - close_profit) > epsilon
    ):
        errors.append("realized_profit_close_profit_abs_mismatch")

    evidence_tables = _string_tuple(row.get("evidence_table"))
    if not REQUIRED_EVIDENCE_TABLES <= set(evidence_tables):
        errors.append("required_evidence_tables_missing")
    source_columns = _string_tuple(row.get("source_columns"))
    if not REQUIRED_SOURCE_COLUMNS <= set(source_columns):
        errors.append("required_source_columns_missing")
    evidence_row_ids = _evidence_row_ids(row.get("evidence_row_ids"), errors)
    formula_version = str(row.get("formula_version") or "").strip()
    if not formula_version:
        errors.append("formula_version_missing")
    if row.get("weighted_average_fill_validated") is not True:
        errors.append("weighted_average_fill_not_validated")

    if (
        errors
        or trade_id is None
        or recovered_close is None
        or weighted_entry is None
        or verified_open is None
        or entry_quantity is None
        or exit_quantity is None
        or trade_amount is None
        or residual is None
    ):
        return sorted(set(errors)), None
    return [], AuthoritativeRecovery(
        trade_id=trade_id,
        order_id=str(row["order_id"]),
        recovered_close_rate=recovered_close,
        verified_open_rate=verified_open,
        filled_quantity=entry_quantity,
        recovered_residual=residual,
        formula_version=formula_version,
        evidence_tables=tuple(sorted(evidence_tables)),
        evidence_row_ids=evidence_row_ids,
        source_columns=tuple(source_columns),
        epsilon=epsilon,
    )


def _source_row_errors(
    row: Mapping[str, Any], recovery: AuthoritativeRecovery
) -> list[str]:
    errors: list[str] = []
    open_rate = _decimal_or_none(row.get("open_rate"))
    amount = _decimal_or_none(row.get("amount"))
    if open_rate is None or abs(open_rate - recovery.verified_open_rate) > recovery.epsilon:
        errors.append("sqlite_open_rate_changed_since_forensics")
    if amount is None or abs(amount - recovery.filled_quantity) > recovery.epsilon:
        errors.append("sqlite_amount_changed_since_forensics")
    return errors


def _evidence_row_ids(
    value: object, errors: list[str]
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    if not isinstance(value, Mapping):
        errors.append("evidence_row_ids_invalid")
        return ()
    normalized: list[tuple[str, tuple[int, ...]]] = []
    for table, raw_ids in sorted(value.items(), key=lambda item: str(item[0])):
        if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes)):
            errors.append("evidence_row_ids_invalid")
            continue
        ids = tuple(row_id for raw in raw_ids if (row_id := _int_or_none(raw)) is not None)
        if len(ids) != len(raw_ids):
            errors.append("evidence_row_ids_invalid")
        normalized.append((str(table), ids))
    lookup = {table: ids for table, ids in normalized}
    if not lookup.get("trades") or not lookup.get("orders"):
        errors.append("required_evidence_row_ids_missing")
    return tuple(normalized)


def _positive_decimal(value: object, field: str, errors: list[str]) -> Decimal | None:
    result = _decimal_or_none(value)
    if result is None or result <= 0:
        errors.append(f"{field}_invalid")
        return None
    return result


def _non_negative_decimal(value: object, field: str, errors: list[str]) -> Decimal | None:
    result = _decimal_or_none(value)
    if result is None or result < 0:
        errors.append(f"{field}_invalid")
        return None
    return result


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value)
