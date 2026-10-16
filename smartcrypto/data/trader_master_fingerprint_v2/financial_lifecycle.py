"""Read-only financial admission of closed Paper trades, independently of forensics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from typing import TypedDict

from .fingerprint_spec import (
    FingerprintValidationError,
    datetime_from_value,
    decimal_from_value,
)
from .source_profile import FreqtradePaperSourceProfile


Record = Mapping[str, object]
TERMINAL_STATES = frozenset({"closed", "filled", "canceled", "cancelled", "expired", "rejected"})
OPEN_STATES = frozenset({"open", "new", "partially_filled", "pending"})
ZERO = Decimal(0)


class TradeResult(TypedDict):
    trade_id: int | None
    status: str
    reasons: list[str]
    entry_quantity: str
    exit_quantity: str
    weighted_entry_price: str | None
    weighted_exit_price: str | None
    executed_order_row_ids: list[int]
    financial_metrics: dict[str, str | None]
    residual: str | None
    funding_order_crosscheck: str
    fees_source: str
    root_cause: str


class LifecycleReport(TypedDict):
    status: str
    reason: str
    structural_errors: list[str]
    trade_results: list[TradeResult]
    accepted_count: int
    quarantined_trade_ids: list[int | None]
    epsilon: str
    historical_classification_changed: bool
    root_cause: str
    source_records_mutated: bool
    write_performed: bool


@dataclass(frozen=True)
class Fill:
    row_id: int
    entry: bool
    quantity: Decimal
    price: Decimal
    timestamp: datetime


def reconcile_financial_lifecycle(
    trades: Sequence[Record],
    orders: Sequence[Record],
    *,
    profile: FreqtradePaperSourceProfile,
) -> LifecycleReport:
    """Never recover prices or mutate evidence; missing proof denies admission."""
    epsilon = decimal_from_value(profile.financial_contract.epsilon_abs_fonte)
    if epsilon <= 0:
        raise ValueError("financial_epsilon_invalid")
    trade_ids = [_identifier(row.get("id")) for row in trades]
    trade_id_set = set(trade_ids)
    order_ids = [_identifier(row.get("id")) for row in orders]
    exchange_ids = [(_text(row.get("ft_pair")), _text(row.get("order_id"))) for row in orders]
    errors: list[str] = []
    if not trades:
        errors.append("closed_trade_population_empty")
    if None in trade_ids or len(set(trade_ids)) != len(trade_ids):
        errors.append("trade_identity_invalid_or_duplicate")
    if None in order_ids or len(set(order_ids)) != len(order_ids):
        errors.append("order_identity_invalid_or_duplicate")
    # Exchange order IDs are scoped to a pair; local SQLite row IDs are global.
    if any(not _text(pair) or not _text(key) for pair, key in exchange_ids):
        errors.append("exchange_order_identity_missing")
    elif len(set(exchange_ids)) != len(exchange_ids):
        errors.append("exchange_order_identity_duplicate")
    grouped: dict[int, list[Record]] = defaultdict(list)
    for row in orders:
        parent = _identifier(row.get("ft_trade_id"))
        if parent is None or parent not in trade_id_set:
            errors.append("order_parent_identity_unresolved")
        else:
            grouped[parent].append(row)
    results: list[TradeResult] = []
    with localcontext() as context:
        context.prec = 50
        for trade, trade_id in sorted(zip(trades, trade_ids, strict=True), key=lambda x: x[1] or 0):
            results.append(_reconcile_trade(
                trade, grouped.get(trade_id or 0, []), profile, epsilon, errors,
            ))
    rejected = [item["trade_id"] for item in results if item["status"] != "accepted"]
    return {
        "status": "blocked" if errors or rejected else "ok",
        "reason": "financial_lifecycle_quarantined" if errors or rejected else "financial_lifecycle_reconciled",
        "structural_errors": sorted(set(errors)),
        "trade_results": results,
        "accepted_count": len(results) - len(rejected),
        "quarantined_trade_ids": rejected,
        "epsilon": str(epsilon),
        "historical_classification_changed": False,
        "root_cause": "ROOT_CAUSE_UNPROVEN",
        "source_records_mutated": False,
        "write_performed": False,
    }


def _reconcile_trade(
    trade: Record,
    orders: Sequence[Record],
    profile: FreqtradePaperSourceProfile,
    epsilon: Decimal,
    structural_errors: Sequence[str],
) -> TradeResult:
    errors = list(structural_errors)
    trade_id = _identifier(trade.get("id"))
    short = _boolean(trade.get("is_short"))
    if short is None:
        errors.append("trade_side_invalid")
    if _boolean(trade.get("is_open")) is not False:
        errors.append("trade_not_proven_closed")
    if trade.get("exchange") != profile.venue or not _text(trade.get("pair")):
        errors.append("trade_venue_or_pair_invalid")
    opened = _timestamp(trade.get("open_date"), "open_date", errors)
    closed = _timestamp(trade.get("close_date"), "close_date", errors)
    if opened is not None and closed is not None and closed < opened:
        errors.append("trade_close_before_open")
    fills: list[Fill] = []
    for order in orders:
        fill = _execution(order, trade, bool(short), opened, closed, errors, epsilon)
        if fill is not None:
            fills.append(fill)
    entries = [fill for fill in fills if fill.entry]
    exits = [fill for fill in fills if not fill.entry]
    entry_quantity = sum((fill.quantity for fill in entries), ZERO)
    exit_quantity = sum((fill.quantity for fill in exits), ZERO)
    if entry_quantity <= 0:
        errors.append("executed_entry_missing")
    if exit_quantity <= 0:
        errors.append("executed_exit_missing")
    if abs(entry_quantity - exit_quantity) > epsilon:
        errors.append("executed_quantity_imbalance")
    # At an equal timestamp, never invent an entry-before-exit ordering.
    by_time: dict[datetime, list[Fill]] = defaultdict(list)
    for fill in fills:
        by_time[fill.timestamp].append(fill)
    inventory = ZERO
    for timestamp in sorted(by_time):
        group = by_time[timestamp]
        leaving = sum((fill.quantity for fill in group if not fill.entry), ZERO)
        if leaving > inventory + epsilon:
            errors.append("exit_before_proven_entry_inventory")
        inventory += sum((fill.quantity for fill in group if fill.entry), ZERO) - leaving

    amount = _number(trade, "amount", errors, positive=True)
    if amount is not None and abs(entry_quantity - amount) > epsilon:
        errors.append("executed_quantity_trade_amount_mismatch")
    entry_value = sum((fill.quantity * fill.price for fill in entries), ZERO)
    exit_value = sum((fill.quantity * fill.price for fill in exits), ZERO)
    entry_average = entry_value / entry_quantity if entry_quantity > 0 else None
    exit_average = exit_value / exit_quantity if exit_quantity > 0 else None
    for field, average in (("open_rate", entry_average), ("close_rate", exit_average)):
        reported = _number(trade, field, errors, positive=True)
        if reported is not None and average is not None and abs(reported - average) > epsilon:
            errors.append(f"{field}_executed_average_mismatch")
    costs, financial_errors = _accounting(trade, profile, entry_value, exit_value, bool(short))
    errors.extend(financial_errors)
    net = costs.get("reconstructed_net_pnl")
    reported_net = _number(trade, "close_profit_abs", errors)
    realized = _number(trade, "realized_profit", errors)
    residual = abs(net - reported_net) if net is not None and reported_net is not None else None
    if residual is not None and residual > epsilon:
        errors.append("executed_financial_residual_above_epsilon")
    if realized is not None and reported_net is not None and abs(realized - reported_net) > epsilon:
        errors.append("realized_close_profit_divergence")
    filled_order_rows = [row for row in orders if _decimal(row.get("filled")) not in (None, ZERO)]
    funding_complete = bool(filled_order_rows) and all(
        row.get("funding_fee") is not None for row in filled_order_rows
    )
    funding = [
        _number(row, "funding_fee", errors)
        for row in filled_order_rows if row.get("funding_fee") is not None
    ]
    if funding_complete:
        reported_funding = _decimal(trade.get("funding_fees"))
        if all(value is not None for value in funding) and reported_funding is not None:
            total = sum((value for value in funding if value is not None), ZERO)
            if abs(total - reported_funding) > epsilon:
                errors.append("order_trade_funding_divergence")
    return {
        "trade_id": trade_id,
        "status": "quarantined" if errors else "accepted",
        "reasons": sorted(set(errors)),
        "entry_quantity": str(entry_quantity),
        "exit_quantity": str(exit_quantity),
        "weighted_entry_price": _render(entry_average),
        "weighted_exit_price": _render(exit_average),
        "executed_order_row_ids": sorted(fill.row_id for fill in fills),
        "financial_metrics": {key: _render(value) for key, value in costs.items()},
        "residual": _render(residual),
        "funding_order_crosscheck": (
            "available" if funding_complete
            else "unavailable_authoritative_trade_column_only"
        ),
        "fees_source": "authoritative_trade_cost_columns_not_independent_exchange_fills",
        "root_cause": "ROOT_CAUSE_UNPROVEN",
    }


def _execution(
    order: Record,
    trade: Record,
    short: bool,
    opened: datetime | None,
    closed: datetime | None,
    errors: list[str],
    epsilon: Decimal,
) -> Fill | None:
    if order.get("ft_pair") != trade.get("pair"):
        errors.append("order_trade_pair_mismatch")
    if order.get("symbol") is not None and order["symbol"] != trade.get("pair"):
        errors.append("order_symbol_pair_contradiction")
    side = _text(order.get("side"))
    ft_side = _text(order.get("ft_order_side"))
    entry_side, exit_side = ("sell", "buy") if short else ("buy", "sell")
    if side not in {entry_side, exit_side} or ft_side not in {side, "stoploss"}:
        errors.append("order_side_invalid_or_contradictory")
    if ft_side == "stoploss" and side != exit_side:
        errors.append("stoploss_side_invalid")
    status = _text(order.get("status"))
    is_open = _boolean(order.get("ft_is_open"))
    if status not in TERMINAL_STATES | OPEN_STATES or is_open is None:
        errors.append("order_state_invalid")
    if is_open is not False or status in OPEN_STATES:
        errors.append("closed_trade_has_pending_order")
    filled = _number(order, "filled", errors, nonnegative=True)
    amount = _number(order, "amount", errors, nonnegative=True)
    remaining = _number(order, "remaining", errors, nonnegative=True)
    if filled is not None and amount is not None and remaining is not None:
        if filled > amount + epsilon or abs(amount - filled - remaining) > epsilon:
            errors.append("order_amount_filled_remaining_inconsistent")
    if filled is None or filled == 0:
        return None
    base_fee = _decimal(order.get("ft_fee_base"))
    if order.get("ft_fee_base") is not None and base_fee != ZERO:
        errors.append("base_fee_inventory_semantics_unproven")
    price = _number(order, "average", errors, positive=True)
    submitted = _timestamp(order.get("order_date"), "order_date", errors)
    executed = _timestamp(order.get("order_filled_date"), "order_filled_date", errors)
    if executed is not None:
        if submitted is not None and executed < submitted:
            errors.append("fill_before_order_submission")
        if opened is not None and executed < opened:
            errors.append("fill_before_trade_open")
        if closed is not None and executed > closed:
            errors.append("fill_after_trade_close")
        if order.get("order_update_date") is not None:
            updated = _timestamp(order["order_update_date"], "order_update_date", errors)
            if updated is not None and updated < executed:
                errors.append("fill_after_order_update")
    if price is None or executed is None or _identifier(order.get("id")) is None:
        return None
    return Fill(int(str(order["id"])), side == entry_side, filled, price, executed)


def _accounting(
    trade: Record,
    profile: FreqtradePaperSourceProfile,
    entry_value: Decimal,
    exit_value: Decimal,
    short: bool,
) -> tuple[dict[str, Decimal], list[str]]:
    errors: list[str] = []
    values = {
        field: _number(trade, field, errors, positive=field in {"contract_size", "leverage"},
                       nonnegative=field in {"fee_open_cost", "fee_close_cost"})
        for field in ("contract_size", "leverage", "fee_open_cost", "fee_close_cost", "funding_fees")
    }
    for field in ("fee_open_currency", "fee_close_currency"):
        if trade.get(field) != profile.settlement_currency:
            errors.append(f"{field}_invalid")
    if errors:
        return {}, errors
    numbers = {key: value for key, value in values.items() if value is not None}
    gross = (entry_value - exit_value if short else exit_value - entry_value) * numbers["contract_size"]
    fee_open = numbers["fee_open_cost"] * numbers["leverage"]
    fee_close = numbers["fee_close_cost"]
    funding_cost = -numbers["funding_fees"]
    return {
        "gross_pnl": gross,
        "effective_open_fee": fee_open,
        "effective_close_fee": fee_close,
        "normalized_funding_cost": funding_cost,
        "reconstructed_net_pnl": gross - fee_open - fee_close - funding_cost,
    }, errors


def _identifier(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    if not (text.isascii() and text.isdigit() and len(text) <= 19):
        return None
    number = int(text)
    return number if 0 < number <= 9223372036854775807 else None


def _boolean(value: object) -> bool | None:
    if type(value) in (bool, int) and value in (0, 1):
        return bool(value)
    return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _decimal(value: object) -> Decimal | None:
    try:
        return decimal_from_value(value)
    except (FingerprintValidationError, TypeError):
        return None


def _number(
    row: Record, field: str, errors: list[str], *, positive: bool = False, nonnegative: bool = False,
) -> Decimal | None:
    value = _decimal(row.get(field))
    if value is None or (positive and value <= 0) or (nonnegative and value < 0):
        errors.append(f"{field}_missing_or_invalid")
        return None
    return value


def _timestamp(value: object, field: str, errors: list[str]) -> datetime | None:
    try:
        return datetime_from_value(value)
    except (FingerprintValidationError, TypeError, ValueError, OverflowError):
        errors.append(f"{field}_missing_or_invalid")
        return None


def _render(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None
