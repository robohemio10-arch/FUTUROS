"""Validated L2 order book with exact depth consumption and no invented liquidity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from .contracts import (
    ContractViolation,
    EventType,
    MarketEvent,
    Side,
    decimal_text,
    decimal_value,
    parse_utc,
)


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    quantity: Decimal
    level: int

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ContractViolation("book_price_must_be_positive")
        if self.quantity <= 0:
            raise ContractViolation("book_quantity_must_be_positive")
        if self.level < 1:
            raise ContractViolation("book_level_must_be_positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": decimal_text(self.price),
            "quantity": decimal_text(self.quantity),
            "level": self.level,
        }


@dataclass(frozen=True)
class RawBookFill:
    price: Decimal
    quantity: Decimal
    level: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": decimal_text(self.price),
            "quantity": decimal_text(self.quantity),
            "level": self.level,
        }


@dataclass(frozen=True)
class BookWalkResult:
    fills: tuple[RawBookFill, ...]
    requested_quantity: Decimal
    filled_quantity: Decimal
    unfilled_quantity: Decimal
    vwap: Decimal | None
    depth_insufficient: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "fills": [item.to_dict() for item in self.fills],
            "requested_quantity": decimal_text(self.requested_quantity),
            "filled_quantity": decimal_text(self.filled_quantity),
            "unfilled_quantity": decimal_text(self.unfilled_quantity),
            "vwap": decimal_text(self.vwap),
            "depth_insufficient": self.depth_insufficient,
        }


class OrderBook:
    """Mutable simulation book reconstructed only from validated public events."""

    def __init__(
        self,
        *,
        symbol: str,
        bids: Sequence[BookLevel],
        asks: Sequence[BookLevel],
        sequence: int,
        event_time_utc: datetime,
        receive_time_utc: datetime,
        source_event_id: str,
    ) -> None:
        self.symbol = symbol
        self.bids = list(bids)
        self.asks = list(asks)
        self.sequence = int(sequence)
        self.event_time_utc = parse_utc(event_time_utc)
        self.receive_time_utc = parse_utc(receive_time_utc)
        self.source_event_id = source_event_id
        self.validate()

    @classmethod
    def from_snapshot_event(cls, event: MarketEvent) -> "OrderBook":
        if event.event_type != EventType.BOOK_SNAPSHOT:
            raise ContractViolation("book_snapshot_event_required")
        payload = event.payload
        bids = _parse_levels(payload.get("bids"), descending=True)
        asks = _parse_levels(payload.get("asks"), descending=False)
        return cls(
            symbol=event.symbol,
            bids=bids,
            asks=asks,
            sequence=event.sequence,
            event_time_utc=event.event_time_utc,
            receive_time_utc=event.receive_time_utc,
            source_event_id=event.event_id,
        )

    def clone(self) -> "OrderBook":
        return OrderBook(
            symbol=self.symbol,
            bids=tuple(self.bids),
            asks=tuple(self.asks),
            sequence=self.sequence,
            event_time_utc=self.event_time_utc,
            receive_time_utc=self.receive_time_utc,
            source_event_id=self.source_event_id,
        )

    def validate(self) -> None:
        if not self.bids or not self.asks:
            raise ContractViolation("empty_order_book")
        _validate_levels(self.bids, descending=True)
        _validate_levels(self.asks, descending=False)
        if self.best_bid >= self.best_ask:
            raise ContractViolation("crossed_or_locked_order_book")
        if self.sequence < 0:
            raise ContractViolation("negative_book_sequence")

    @property
    def best_bid(self) -> Decimal:
        return self.bids[0].price

    @property
    def best_ask(self) -> Decimal:
        return self.asks[0].price

    @property
    def mid_price(self) -> Decimal:
        return (self.best_bid + self.best_ask) / Decimal("2")

    @property
    def spread_absolute(self) -> Decimal:
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> Decimal:
        return self.spread_absolute / self.mid_price * Decimal("10000")

    @property
    def book_imbalance(self) -> Decimal:
        bid_quantity = sum((level.quantity for level in self.bids), Decimal("0"))
        ask_quantity = sum((level.quantity for level in self.asks), Decimal("0"))
        total = bid_quantity + ask_quantity
        if total == 0:
            raise ContractViolation("empty_order_book")
        return (bid_quantity - ask_quantity) / total

    def is_stale(self, interaction_time_utc: datetime, stale_after_ms: int) -> bool:
        if stale_after_ms < 0:
            raise ContractViolation("negative_book_staleness_threshold")
        interaction_time = parse_utc(interaction_time_utc)
        return interaction_time - self.receive_time_utc > timedelta(
            milliseconds=stale_after_ms
        )

    def available_quantity(
        self,
        *,
        side: Side,
        limit_price: Decimal | None = None,
    ) -> Decimal:
        levels = self.asks if side.order_side == Side.BUY else self.bids
        return sum(
            (
                level.quantity
                for level in levels
                if _price_allowed(side.order_side, level.price, limit_price)
            ),
            Decimal("0"),
        )

    def quantity_at_price(self, side: Side, price: Decimal) -> Decimal:
        levels = self.bids if side.order_side == Side.BUY else self.asks
        return sum(
            (level.quantity for level in levels if level.price == price),
            Decimal("0"),
        )

    def walk(
        self,
        *,
        side: Side,
        quantity: Decimal,
        limit_price: Decimal | None = None,
        mutate: bool = True,
    ) -> BookWalkResult:
        requested = decimal_value(quantity, field_name="requested_quantity")
        if requested <= 0:
            raise ContractViolation("requested_quantity_must_be_positive")
        levels = self.asks if side.order_side == Side.BUY else self.bids
        remaining = requested
        raw_fills: list[RawBookFill] = []
        updated: list[BookLevel] = []
        for level in levels:
            if remaining <= 0 or not _price_allowed(
                side.order_side, level.price, limit_price
            ):
                updated.append(level)
                continue
            consumed = min(level.quantity, remaining)
            if consumed > 0:
                raw_fills.append(
                    RawBookFill(
                        price=level.price,
                        quantity=consumed,
                        level=level.level,
                    )
                )
                remaining -= consumed
            residual = level.quantity - consumed
            if residual > 0:
                updated.append(
                    BookLevel(
                        price=level.price,
                        quantity=residual,
                        level=level.level,
                    )
                )
        if mutate:
            if side.order_side == Side.BUY:
                self.asks = _renumber(updated)
            else:
                self.bids = _renumber(updated)
        filled = requested - remaining
        notional = sum(
            (fill.price * fill.quantity for fill in raw_fills), Decimal("0")
        )
        vwap = notional / filled if filled > 0 else None
        return BookWalkResult(
            fills=tuple(raw_fills),
            requested_quantity=requested,
            filled_quantity=filled,
            unfilled_quantity=remaining,
            vwap=vwap,
            depth_insufficient=remaining > 0,
        )

    def apply_delta(self, event: MarketEvent) -> None:
        if event.event_type != EventType.BOOK_DELTA:
            raise ContractViolation("book_delta_event_required")
        if event.symbol != self.symbol:
            raise ContractViolation("book_delta_symbol_mismatch")
        if event.sequence <= self.sequence:
            raise ContractViolation("regressive_book_delta_sequence")
        self.bids = _apply_side_delta(
            self.bids,
            event.payload.get("bids", ()),
            descending=True,
        )
        self.asks = _apply_side_delta(
            self.asks,
            event.payload.get("asks", ()),
            descending=False,
        )
        self.sequence = event.sequence
        self.event_time_utc = event.event_time_utc
        self.receive_time_utc = event.receive_time_utc
        self.source_event_id = event.event_id
        self.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "sequence": self.sequence,
            "event_time_utc": self.event_time_utc.isoformat(),
            "receive_time_utc": self.receive_time_utc.isoformat(),
            "source_event_id": self.source_event_id,
            "bids": [level.to_dict() for level in self.bids],
            "asks": [level.to_dict() for level in self.asks],
            "best_bid": decimal_text(self.best_bid),
            "best_ask": decimal_text(self.best_ask),
            "spread_absolute": decimal_text(self.spread_absolute),
            "spread_bps": decimal_text(self.spread_bps),
            "book_imbalance": decimal_text(self.book_imbalance),
        }


def _parse_levels(value: Any, *, descending: bool) -> list[BookLevel]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        raise ContractViolation("book_levels_must_be_sequence")
    parsed: list[BookLevel] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, Mapping):
            price = decimal_value(item.get("price"), field_name="book_price")
            quantity = decimal_value(
                item.get("quantity"), field_name="book_quantity"
            )
            level = int(item.get("level", index))
        elif isinstance(item, Sequence) and len(item) >= 2:
            price = decimal_value(item[0], field_name="book_price")
            quantity = decimal_value(item[1], field_name="book_quantity")
            level = index
        else:
            raise ContractViolation("invalid_book_level")
        parsed.append(BookLevel(price=price, quantity=quantity, level=level))
    _validate_levels(parsed, descending=descending)
    return parsed


def _validate_levels(levels: Sequence[BookLevel], *, descending: bool) -> None:
    if not levels:
        raise ContractViolation("empty_book_side")
    prices = [level.price for level in levels]
    expected = sorted(prices, reverse=descending)
    if prices != expected or len(set(prices)) != len(prices):
        reason = "bids_not_strictly_descending" if descending else "asks_not_strictly_ascending"
        raise ContractViolation(reason)
    level_numbers = [level.level for level in levels]
    if level_numbers != list(range(1, len(levels) + 1)):
        raise ContractViolation("book_level_sequence_invalid")


def _price_allowed(
    side: Side,
    price: Decimal,
    limit_price: Decimal | None,
) -> bool:
    if limit_price is None:
        return True
    if side.order_side == Side.BUY:
        return price <= limit_price
    return price >= limit_price


def _renumber(levels: Sequence[BookLevel]) -> list[BookLevel]:
    return [
        BookLevel(price=level.price, quantity=level.quantity, level=index)
        for index, level in enumerate(levels, start=1)
    ]


def _apply_side_delta(
    current: Sequence[BookLevel],
    raw_delta: Any,
    *,
    descending: bool,
) -> list[BookLevel]:
    quantities = {level.price: level.quantity for level in current}
    if not isinstance(raw_delta, Iterable) or isinstance(
        raw_delta, (str, bytes, Mapping)
    ):
        raise ContractViolation("book_delta_levels_must_be_sequence")
    for item in raw_delta:
        if isinstance(item, Mapping):
            price = decimal_value(item.get("price"), field_name="delta_price")
            quantity = decimal_value(
                item.get("quantity"), field_name="delta_quantity"
            )
        elif isinstance(item, Sequence) and len(item) >= 2:
            price = decimal_value(item[0], field_name="delta_price")
            quantity = decimal_value(item[1], field_name="delta_quantity")
        else:
            raise ContractViolation("invalid_book_delta_level")
        if quantity < 0:
            raise ContractViolation("negative_book_delta_quantity")
        if quantity == 0:
            quantities.pop(price, None)
        else:
            quantities[price] = quantity
    sorted_prices = sorted(quantities, reverse=descending)
    return [
        BookLevel(price=price, quantity=quantities[price], level=index)
        for index, price in enumerate(sorted_prices, start=1)
    ]
