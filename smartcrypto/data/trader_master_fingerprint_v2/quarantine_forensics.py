"""Targeted, read-only accounting forensics for five quarantined paper trades."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from .authoritative_sqlite import read_authoritative_trade_evidence
from .fingerprint_spec import FingerprintValidationError, decimal_from_value
from .source_profile import SourceProfileError, load_source_profile
from .staging_runner import resolve_path
from .staging_validator import SAFETY_FLAGS


FORENSICS_SCHEMA_VERSION = "freqtrade_paper_quarantine_forensics_v2"
TARGET_TRADE_IDS = frozenset({141, 221, 234, 258, 561})
DEFAULT_SOURCE_PROFILE = Path("config/freqtrade_paper_closed_trades_source_profile_v2.json")

RECOVERED = "recovered_authoritatively"
MISSING_EXIT = "remains_quarantined_missing_authoritative_exit"
ACCOUNTING_UNEXPLAINED = "remains_quarantined_accounting_unexplained"

FORENSIC_SAFETY_FLAGS: dict[str, bool] = {
    **SAFETY_FLAGS,
    "writes_csv": False,
    "writes_parquet": False,
    "writes_sqlite": False,
    "writes_runtime": False,
    "compares_trader_master": False,
    "runs_import": False,
    "runs_backfill": False,
    "updates_freqtrade_runtime": False,
}


def build_targeted_quarantine_forensics_report(
    *,
    project_root: str | Path,
    source_profile_path: str | Path = DEFAULT_SOURCE_PROFILE,
    authoritative_sqlite_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    base = _base_report(source_profile_path)
    try:
        profile = load_source_profile(resolve_path(root, source_profile_path))
    except SourceProfileError as exc:
        return _blocked(base, "source_profile_invalid", str(exc).split(";"))
    snapshot = resolve_path(
        root,
        authoritative_sqlite_path or profile.authoritative_sqlite.snapshot_path,
    )
    evidence = read_authoritative_trade_evidence(
        project_root=root,
        snapshot_path=snapshot,
        profile=profile,
        trade_ids=TARGET_TRADE_IDS,
    )
    report = {
        **base,
        "source_profile_id": profile.profile_id,
        "source_profile_sha256": profile.profile_sha256,
        "authoritative_sqlite_path": evidence.get("snapshot_path"),
        **{key: value for key, value in evidence.items() if key not in {"trades", "orders", "trade_custom_data"}},
    }
    if evidence["status"] != "ok":
        return _blocked(
            report,
            str(evidence["reason"]),
            evidence.get("validation_errors", []),
        )

    trades = {int(row["id"]): row for row in evidence["trades"]}
    missing_trade_ids = sorted(TARGET_TRADE_IDS - set(trades))
    unexpected_trade_ids = sorted(set(trades) - TARGET_TRADE_IDS)
    if missing_trade_ids or unexpected_trade_ids:
        return _blocked(
            report,
            "target_trade_scope_mismatch",
            [
                *(f"target_trade_missing:{trade_id}" for trade_id in missing_trade_ids),
                *(f"unexpected_trade_loaded:{trade_id}" for trade_id in unexpected_trade_ids),
            ],
        )

    orders_by_trade: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence["orders"]:
        orders_by_trade[int(row["ft_trade_id"])].append(row)
    custom_by_trade: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence["trade_custom_data"]:
        custom_by_trade[int(row["ft_trade_id"])].append(row)

    trade_results = [
        analyze_quarantined_trade(
            trades[trade_id],
            orders_by_trade[trade_id],
            custom_by_trade[trade_id],
            epsilon=decimal_from_value(profile.financial_contract.epsilon_abs_fonte),
        )
        for trade_id in sorted(TARGET_TRADE_IDS)
    ]
    decision_counts = Counter(item["recovery_decision"] for item in trade_results)
    report.update(
        status="ok",
        reason="targeted_quarantine_forensics_completed",
        target_trade_ids=sorted(TARGET_TRADE_IDS),
        target_trade_count=len(TARGET_TRADE_IDS),
        target_scope_exact=True,
        trade_results=trade_results,
        recovery_decision_counts=dict(sorted(decision_counts.items())),
        recovered_trade_ids=[
            item["order_id"] for item in trade_results if item["recovery_decision"] == RECOVERED
        ],
        remaining_quarantined_trade_ids=[
            item["order_id"] for item in trade_results if item["recovery_decision"] != RECOVERED
        ],
        recovered_count=decision_counts[RECOVERED],
        remains_quarantined_count=len(trade_results) - decision_counts[RECOVERED],
        recovery_applied=False,
        write_performed=False,
        **FORENSIC_SAFETY_FLAGS,
        safety_flags=dict(FORENSIC_SAFETY_FLAGS),
    )
    return report


def analyze_quarantined_trade(
    trade: Mapping[str, Any],
    orders: Sequence[Mapping[str, Any]],
    custom_data: Sequence[Mapping[str, Any]],
    *,
    epsilon: Decimal,
) -> dict[str, Any]:
    trade_id = int(trade["id"])
    order_id = f"freqtrade-paper-{trade_id}"
    side = "short" if _sqlite_bool(trade.get("is_short")) else "long"
    entry_side, exit_side = ("sell", "buy") if side == "short" else ("buy", "sell")
    filled_orders, ignored_orders, execution_blockers = _classify_orders(orders)
    entry_orders = [row for row in filled_orders if _side(row) == entry_side]
    exit_orders = [row for row in filled_orders if _side(row) == exit_side]
    unexpected_orders = [
        row for row in filled_orders if _side(row) not in {entry_side, exit_side}
    ]
    if unexpected_orders:
        execution_blockers.append("filled_order_side_unclassified")

    entry_quantity = _sum_decimal(entry_orders, "filled")
    exit_quantity = _sum_decimal(exit_orders, "filled")
    entry_average = _weighted_average(entry_orders)
    exit_average = _weighted_average(exit_orders)
    trade_amount = _decimal_or_none(trade.get("amount"))
    quantity_compatible = (
        trade_amount is not None
        and abs(entry_quantity - exit_quantity) <= epsilon
        and abs(entry_quantity - trade_amount) <= epsilon
    )
    if entry_quantity <= 0:
        execution_blockers.append("filled_entry_missing")
    if exit_quantity <= 0:
        execution_blockers.append("filled_exit_missing")
    if entry_quantity > 0 and exit_quantity > 0 and not quantity_compatible:
        execution_blockers.append("filled_order_quantity_mismatch")

    original_candidate = _trade_summary_candidate(trade, side=side, epsilon=epsilon)
    weighted_candidate = _weighted_fill_candidate(
        trade,
        side=side,
        entry_orders=entry_orders,
        exit_orders=exit_orders,
        entry_average=entry_average,
        exit_average=exit_average,
        entry_quantity=entry_quantity,
        exit_quantity=exit_quantity,
        quantity_compatible=quantity_compatible,
        execution_blockers=execution_blockers,
        epsilon=epsilon,
    )
    realized = _decimal_or_none(trade.get("realized_profit"))
    close_profit_abs = _decimal_or_none(trade.get("close_profit_abs"))
    realized_matches = (
        realized is not None
        and close_profit_abs is not None
        and abs(realized - close_profit_abs) <= epsilon
    )
    if not realized_matches:
        execution_blockers.append("realized_profit_close_profit_abs_divergence")

    if weighted_candidate["valid_for_recovery"] and realized_matches:
        decision = RECOVERED
        remaining_blockers: list[str] = []
        recovery_reason = "filled_orders_balance_and_accounting_identity_reconciled"
        formula_version = weighted_candidate["formula_version"]
        source_columns = weighted_candidate["source_columns"]
        recovered_residual = weighted_candidate["residual"]
    elif exit_quantity <= 0 or exit_average is None:
        decision = MISSING_EXIT
        remaining_blockers = sorted(set(execution_blockers + weighted_candidate["blockers"]))
        recovery_reason = "authoritative_filled_exit_not_available"
        formula_version = None
        source_columns = []
        recovered_residual = None
    else:
        decision = ACCOUNTING_UNEXPLAINED
        remaining_blockers = sorted(set(execution_blockers + weighted_candidate["blockers"]))
        recovery_reason = "authoritative_orders_do_not_prove_balanced_reconciled_position"
        formula_version = None
        source_columns = []
        recovered_residual = None

    order_row_ids = sorted(int(row["id"]) for row in filled_orders)
    custom_row_ids = sorted(int(row["id"]) for row in custom_data)
    return {
        "trade_id": trade_id,
        "order_id": order_id,
        "recovery_decision": decision,
        "recovery_reason": recovery_reason,
        "original_residual": original_candidate["residual"],
        "recovered_residual": recovered_residual,
        "orders_found": len(orders),
        "filled_orders_found": len(filled_orders),
        "filled_entry_order_count": len(entry_orders),
        "filled_exit_order_count": len(exit_orders),
        "filled_entry_quantity": _decimal_text(entry_quantity),
        "filled_exit_quantity": _decimal_text(exit_quantity),
        "weighted_entry_price": _decimal_text(entry_average),
        "weighted_exit_price": _decimal_text(exit_average),
        "weighted_average_fill_validated": weighted_candidate["valid_for_recovery"],
        "multiple_entry_fills": len(entry_orders) > 1,
        "multiple_exit_fills": len(exit_orders) > 1,
        "partial_exit_detected": len(exit_orders) > 1
        and any((_decimal_or_none(row.get("filled")) or Decimal(0)) < entry_quantity for row in exit_orders),
        "position_adjustment_detected": len(entry_orders) > 1
        or any("adjust" in str(row.get("cd_key", "")).casefold() for row in custom_data),
        "quantity_compatible": quantity_compatible,
        "ignored_orders": ignored_orders,
        "remaining_blockers": remaining_blockers,
        "amount_inventory": {
            "amount": _decimal_text(_decimal_or_none(trade.get("amount"))),
            "amount_requested": _decimal_text(_decimal_or_none(trade.get("amount_requested"))),
            "stake_amount": _decimal_text(_decimal_or_none(trade.get("stake_amount"))),
            "max_stake_amount": _decimal_text(_decimal_or_none(trade.get("max_stake_amount"))),
            "open_trade_value": _decimal_text(_decimal_or_none(trade.get("open_trade_value"))),
        },
        "reported_profit": {
            "realized_profit": _decimal_text(realized),
            "close_profit_abs": _decimal_text(close_profit_abs),
            "values_match": realized_matches,
        },
        "formula_candidates": [original_candidate, weighted_candidate],
        "evidence_table": ["trades", "orders", *( ["trade_custom_data"] if custom_data else [])],
        "evidence_row_ids": {
            "trades": [trade_id],
            "orders": order_row_ids,
            "trade_custom_data": custom_row_ids,
        },
        "formula_version": formula_version,
        "source_columns": source_columns,
        "residual": recovered_residual,
        "close_rate_requested_used": False,
        "recovery_applied": False,
    }


def _classify_orders(
    orders: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]], list[str]]:
    filled: list[Mapping[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    blockers: list[str] = []
    for row in orders:
        status = (_text(row.get("status")) or "").casefold()
        quantity = _decimal_or_none(row.get("filled"))
        average = _decimal_or_none(row.get("average"))
        filled_at = _text(row.get("order_filled_date"))
        if status in {"canceled", "cancelled", "rejected", "expired"}:
            ignored.append({"order_row_id": int(row["id"]), "reason": "cancelled_or_rejected_status"})
            continue
        if quantity is None or quantity <= 0:
            ignored.append({"order_row_id": int(row["id"]), "reason": "not_filled"})
            continue
        if status != "closed":
            ignored.append({"order_row_id": int(row["id"]), "reason": "filled_but_not_closed"})
            blockers.append("filled_order_not_closed")
            continue
        if average is None or average <= 0 or filled_at is None:
            ignored.append(
                {"order_row_id": int(row["id"]), "reason": "missing_execution_price_or_timestamp"}
            )
            blockers.append("filled_order_missing_execution_evidence")
            continue
        filled.append(row)
    return filled, ignored, blockers


def _trade_summary_candidate(
    trade: Mapping[str, Any],
    *,
    side: str,
    epsilon: Decimal,
) -> dict[str, Any]:
    required = {
        key: _decimal_or_none(trade.get(key))
        for key in (
            "open_rate",
            "close_rate",
            "amount",
            "contract_size",
            "leverage",
            "fee_open_cost",
            "fee_close_cost",
            "funding_fees",
            "close_profit_abs",
        )
    }
    missing = sorted(key for key, value in required.items() if value is None)
    if missing:
        return _candidate_report(
            formula_version="trade_summary_single_amount_v1",
            source_columns=[f"trades.{key}" for key in required],
            blockers=[f"missing_source_column:{key}" for key in missing],
        )
    values = {key: value for key, value in required.items() if value is not None}
    gross = _signed_price_pnl(
        side,
        values["open_rate"],
        values["close_rate"],
        values["amount"],
        values["contract_size"],
    )
    return _financial_candidate(
        formula_version="trade_summary_single_amount_v1",
        source_columns=[f"trades.{key}" for key in required],
        gross=gross,
        trade=values,
        epsilon=epsilon,
        extra_blockers=[],
    )


def _weighted_fill_candidate(
    trade: Mapping[str, Any],
    *,
    side: str,
    entry_orders: Sequence[Mapping[str, Any]],
    exit_orders: Sequence[Mapping[str, Any]],
    entry_average: Decimal | None,
    exit_average: Decimal | None,
    entry_quantity: Decimal,
    exit_quantity: Decimal,
    quantity_compatible: bool,
    execution_blockers: Sequence[str],
    epsilon: Decimal,
) -> dict[str, Any]:
    required_trade_values = {
        key: _decimal_or_none(trade.get(key))
        for key in (
            "contract_size",
            "leverage",
            "fee_open_cost",
            "fee_close_cost",
            "funding_fees",
            "close_profit_abs",
        )
    }
    blockers = list(execution_blockers)
    blockers.extend(
        f"missing_source_column:{key}"
        for key, value in required_trade_values.items()
        if value is None
    )
    if entry_average is None:
        blockers.append("weighted_entry_price_unavailable")
    if exit_average is None:
        blockers.append("weighted_exit_price_unavailable")
    if not quantity_compatible:
        blockers.append("filled_order_quantity_mismatch")
    source_columns = [
        "orders.id",
        "orders.ft_trade_id",
        "orders.status",
        "orders.side",
        "orders.average",
        "orders.filled",
        "orders.remaining",
        "orders.order_filled_date",
        "trades.amount",
        "trades.contract_size",
        "trades.leverage",
        "trades.fee_open_cost",
        "trades.fee_close_cost",
        "trades.funding_fees",
        "trades.close_profit_abs",
    ]
    if blockers or entry_average is None or exit_average is None:
        gross = None
        if entry_average is not None and exit_average is not None and exit_quantity > 0:
            contract_size = required_trade_values.get("contract_size")
            if contract_size is not None:
                gross = _signed_price_pnl(
                    side,
                    entry_average,
                    exit_average,
                    exit_quantity,
                    contract_size,
                )
        candidate = _candidate_report(
            formula_version="filled_orders_weighted_average_v1",
            source_columns=source_columns,
            blockers=sorted(set(blockers)),
            gross_pnl=_decimal_text(gross),
            entry_order_row_ids=sorted(int(row["id"]) for row in entry_orders),
            exit_order_row_ids=sorted(int(row["id"]) for row in exit_orders),
        )
        if gross is not None and all(value is not None for value in required_trade_values.values()):
            financial = _financial_candidate(
                formula_version="filled_orders_weighted_average_v1",
                source_columns=source_columns,
                gross=gross,
                trade={
                    key: value
                    for key, value in required_trade_values.items()
                    if value is not None
                },
                epsilon=epsilon,
                extra_blockers=sorted(set(blockers)),
            )
            candidate.update(financial)
        return candidate
    gross = _signed_price_pnl(
        side,
        entry_average,
        exit_average,
        exit_quantity,
        required_trade_values["contract_size"],  # type: ignore[arg-type]
    )
    candidate = _financial_candidate(
        formula_version="filled_orders_weighted_average_v1",
        source_columns=source_columns,
        gross=gross,
        trade={key: value for key, value in required_trade_values.items() if value is not None},
        epsilon=epsilon,
        extra_blockers=[],
    )
    candidate.update(
        entry_order_row_ids=sorted(int(row["id"]) for row in entry_orders),
        exit_order_row_ids=sorted(int(row["id"]) for row in exit_orders),
    )
    return candidate


def _financial_candidate(
    *,
    formula_version: str,
    source_columns: list[str],
    gross: Decimal,
    trade: Mapping[str, Decimal],
    epsilon: Decimal,
    extra_blockers: Sequence[str],
) -> dict[str, Any]:
    open_fee = trade["fee_open_cost"] * trade["leverage"]
    close_fee = trade["fee_close_cost"]
    trading_fee = open_fee + close_fee
    funding_fee = -trade["funding_fees"]
    reconstructed_net = gross - trading_fee - funding_fee
    residual = abs(reconstructed_net - trade["close_profit_abs"])
    blockers = list(extra_blockers)
    if residual > epsilon:
        blockers.append("financial_accounting_identity_violation")
    return _candidate_report(
        formula_version=formula_version,
        source_columns=source_columns,
        blockers=sorted(set(blockers)),
        gross_pnl=_decimal_text(gross),
        effective_open_fee=_decimal_text(open_fee),
        effective_close_fee=_decimal_text(close_fee),
        trading_fee=_decimal_text(trading_fee),
        normalized_funding_fee=_decimal_text(funding_fee),
        reconstructed_net_pnl=_decimal_text(reconstructed_net),
        reported_net_pnl=_decimal_text(trade["close_profit_abs"]),
        residual=_decimal_text(residual),
        valid_for_recovery=not blockers,
    )


def _candidate_report(
    *,
    formula_version: str,
    source_columns: list[str],
    blockers: Sequence[str],
    **values: Any,
) -> dict[str, Any]:
    return {
        "formula_version": formula_version,
        "source_columns": source_columns,
        "blockers": sorted(set(blockers)),
        "valid_for_recovery": False,
        "gross_pnl": None,
        "effective_open_fee": None,
        "effective_close_fee": None,
        "trading_fee": None,
        "normalized_funding_fee": None,
        "reconstructed_net_pnl": None,
        "reported_net_pnl": None,
        "residual": None,
        **values,
    }


def _weighted_average(orders: Sequence[Mapping[str, Any]]) -> Decimal | None:
    quantity = _sum_decimal(orders, "filled")
    if quantity <= 0:
        return None
    total = Decimal(0)
    for row in orders:
        average = _decimal_or_none(row.get("average"))
        filled = _decimal_or_none(row.get("filled"))
        if average is None or filled is None:
            return None
        total += average * filled
    return total / quantity


def _sum_decimal(rows: Sequence[Mapping[str, Any]], field: str) -> Decimal:
    return sum((_decimal_or_none(row.get(field)) or Decimal(0) for row in rows), Decimal(0))


def _signed_price_pnl(
    side: str,
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
    contract_size: Decimal,
) -> Decimal:
    delta = entry_price - exit_price if side == "short" else exit_price - entry_price
    return delta * quantity * contract_size


def _side(row: Mapping[str, Any]) -> str:
    return (_text(row.get("side")) or "").casefold()


def _decimal_or_none(value: object) -> Decimal | None:
    try:
        return decimal_from_value(value)
    except (FingerprintValidationError, TypeError):
        return None


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _sqlite_bool(value: object) -> bool:
    return value is True or value == 1 or str(value).strip().casefold() in {"true", "1"}


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.casefold() in {"", "nan", "none", "null", "<na>"} else text


def _base_report(source_profile_path: str | Path) -> dict[str, Any]:
    return {
        "schema_version": FORENSICS_SCHEMA_VERSION,
        "status": "blocked",
        "reason": "not_evaluated",
        "source_profile_path": str(source_profile_path),
        "target_trade_ids": sorted(TARGET_TRADE_IDS),
        "target_trade_count": len(TARGET_TRADE_IDS),
        "write_performed": False,
        **FORENSIC_SAFETY_FLAGS,
        "safety_flags": dict(FORENSIC_SAFETY_FLAGS),
    }


def _blocked(
    report: dict[str, Any], reason: str, errors: Sequence[str] | None = None
) -> dict[str, Any]:
    result = dict(report)
    result.update(
        status="blocked",
        reason=reason,
        validation_errors=sorted(set(errors or [reason])),
        write_performed=False,
    )
    return result
