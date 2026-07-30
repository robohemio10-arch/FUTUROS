"""Typed, immutable contracts for the research-only futures execution engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping

from smartcrypto.data.canonical_data_foundation_v2.contracts import (
    canonical_json,
    json_safe,
    stable_hash,
)

SCHEMA_VERSION = "futures_execution_realism_engine_v2"
SIMULATION_NAMESPACE = "simulation_only"

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "automatic_promotion_allowed": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "writes_active_signals": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
}


class ContractViolation(ValueError):
    """Fail-closed contract violation with a stable machine-readable reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EventType(StrEnum):
    BOOK_SNAPSHOT = "BookSnapshotEvent"
    BOOK_DELTA = "BookDeltaEvent"
    TRADE_PRINT = "TradePrintEvent"
    MARK_PRICE = "MarkPriceEvent"
    FUNDING_RATE = "FundingRateEvent"
    SIGNAL_INTENT = "SignalIntentEvent"
    ORDER_SUBMIT = "OrderSubmitEvent"
    ORDER_ACCEPTED = "OrderAcceptedEvent"
    ORDER_REJECTED = "OrderRejectedEvent"
    PARTIAL_FILL = "PartialFillEvent"
    FILL = "FillEvent"
    CANCEL_REQUEST = "CancelRequestEvent"
    CANCEL_CONFIRMED = "CancelConfirmedEvent"
    REPRICE = "RepriceEvent"
    TIMEOUT = "TimeoutEvent"
    STOP_TRIGGER = "StopTriggerEvent"
    MARGIN_UPDATE = "MarginUpdateEvent"
    LIQUIDATION = "LiquidationEvent"
    POSITION_CLOSE = "PositionCloseEvent"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def order_side(self) -> "Side":
        if self in {Side.BUY, Side.LONG}:
            return Side.BUY
        return Side.SELL

    @property
    def direction(self) -> Decimal:
        return Decimal("1") if self in {Side.BUY, Side.LONG} else Decimal("-1")


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    LIMIT_MAKER = "LIMIT_MAKER"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


class OrderState(StrEnum):
    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class LiquidityRole(StrEnum):
    MAKER = "maker"
    TAKER = "taker"
    UNKNOWN = "unknown"


class QueueModel(StrEnum):
    PESSIMISTIC = "pessimistic_queue"
    PROPORTIONAL = "proportional_queue"
    DETERMINISTIC_FRONT = "deterministic_front"
    DETERMINISTIC_BACK = "deterministic_back"


class LatencyDistribution(StrEnum):
    CONSTANT = "constant"
    EMPIRICAL = "deterministic_empirical_fixture"
    LOGNORMAL = "seeded_lognormal"
    GAMMA = "seeded_gamma"


class SlippageModel(StrEnum):
    OBSERVED_BOOK_WALK = "observed_book_walk"
    FIXED_BPS = "fixed_bps"
    SQUARE_ROOT_IMPACT = "square_root_impact"
    CONSERVATIVE_HYBRID = "conservative_hybrid"


class MarginMode(StrEnum):
    ISOLATED = "ISOLATED"
    CROSS = "CROSS"


class PostOnlyPolicy(StrEnum):
    REJECT = "reject"
    REPRICE = "reprice"


@dataclass(frozen=True)
class InputAuthority:
    """B02 authority boundary for one execution input."""

    dataset_class: str
    lineage_status: str
    candle_status: str
    fixture_only: bool = False
    legacy_research_non_authoritative: bool = False
    source_hash: str | None = None

    @property
    def authoritative(self) -> bool:
        return (
            not self.fixture_only
            and not self.legacy_research_non_authoritative
            and self.lineage_status == "VERIFIED"
            and self.candle_status in {"VERIFIED", "RECOVERED_VERIFIED"}
        )

    @property
    def quarantined(self) -> bool:
        return (
            self.lineage_status == "PERMANENT_QUARANTINE"
            or self.candle_status == "PERMANENT_QUARANTINE"
        )

    def validate(self) -> None:
        if self.source_hash is not None and not _is_sha256(self.source_hash):
            raise ContractViolation("invalid_input_source_hash")
        if self.quarantined and not self.legacy_research_non_authoritative:
            raise ContractViolation("input_not_authoritative")
        if not self.authoritative and not (
            self.fixture_only or self.legacy_research_non_authoritative
        ):
            raise ContractViolation("input_authority_unresolved")

    def to_dict(self) -> dict[str, Any]:
        return {
            **json_safe(asdict(self)),
            "authoritative": self.authoritative,
            "quarantined": self.quarantined,
        }


@dataclass(frozen=True)
class MarketEvent:
    """Canonical market or simulated lifecycle event."""

    event_id: str
    event_type: EventType
    symbol: str
    event_time_utc: datetime
    receive_time_utc: datetime
    sequence: int
    source: str
    source_hash: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_simulation_or_source_event_id(self.event_id)
        _validate_symbol(self.symbol)
        _require_utc("event_time_utc", self.event_time_utc)
        _require_utc("receive_time_utc", self.receive_time_utc)
        if self.receive_time_utc < self.event_time_utc:
            raise ContractViolation("receive_time_precedes_event_time")
        if self.sequence < 0:
            raise ContractViolation("negative_event_sequence")
        if not self.source.strip():
            raise ContractViolation("event_source_required")
        if not _is_sha256(self.source_hash):
            raise ContractViolation("invalid_event_source_hash")
        canonical_json(self.payload)

    @classmethod
    def create(
        cls,
        *,
        event_type: EventType,
        symbol: str,
        event_time_utc: datetime,
        receive_time_utc: datetime,
        sequence: int,
        source: str,
        source_hash: str,
        payload: Mapping[str, Any] | None = None,
        event_id: str | None = None,
    ) -> "MarketEvent":
        resolved_payload = dict(payload or {})
        identity_payload = {
            "event_type": event_type.value,
            "symbol": normalize_symbol(symbol),
            "event_time_utc": to_utc(event_time_utc).isoformat(),
            "receive_time_utc": to_utc(receive_time_utc).isoformat(),
            "sequence": int(sequence),
            "source": source,
            "source_hash": source_hash,
            "payload": resolved_payload,
            "schema_version": SCHEMA_VERSION,
        }
        resolved_id = event_id or f"sim_evt_{stable_hash(identity_payload)[:32]}"
        return cls(
            event_id=resolved_id,
            event_type=event_type,
            symbol=normalize_symbol(symbol),
            event_time_utc=to_utc(event_time_utc),
            receive_time_utc=to_utc(receive_time_utc),
            sequence=int(sequence),
            source=source,
            source_hash=source_hash.lower(),
            payload=resolved_payload,
        )

    @property
    def content_hash(self) -> str:
        return stable_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "symbol": self.symbol,
            "event_time_utc": self.event_time_utc.isoformat(),
            "receive_time_utc": self.receive_time_utc.isoformat(),
            "sequence": self.sequence,
            "source": self.source,
            "source_hash": self.source_hash,
            "schema_version": self.schema_version,
            "payload": json_safe(self.payload),
        }


@dataclass(frozen=True)
class OrderIntent:
    """An order intent that can only exist in the simulation namespace."""

    symbol: str
    side: Side
    quantity: Decimal
    order_type: OrderType
    time_in_force: TimeInForce
    submit_time_utc: datetime
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    cancel_after_ms: int | None = None
    reprice_after_ms: int | None = None
    reprice_price: Decimal | None = None
    reduce_only: bool = False
    simulate_api_timeout: bool = False
    client_intent_id: str = "intent"
    order_id: str | None = None
    parent_order_id: str | None = None
    namespace: str = SIMULATION_NAMESPACE

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        if self.namespace != SIMULATION_NAMESPACE:
            raise ContractViolation("non_simulation_order_namespace_forbidden")
        _require_utc("submit_time_utc", self.submit_time_utc)
        _require_positive("quantity", self.quantity)
        if self.limit_price is not None:
            _require_positive("limit_price", self.limit_price)
        if self.stop_price is not None:
            _require_positive("stop_price", self.stop_price)
        if self.reprice_price is not None:
            _require_positive("reprice_price", self.reprice_price)
        if self.order_type in {
            OrderType.LIMIT,
            OrderType.LIMIT_MAKER,
            OrderType.STOP_LIMIT,
        } and self.limit_price is None:
            raise ContractViolation("limit_price_required")
        if self.order_type in {OrderType.STOP_MARKET, OrderType.STOP_LIMIT}:
            if self.stop_price is None:
                raise ContractViolation("stop_price_required")
        if self.cancel_after_ms is not None and self.cancel_after_ms < 0:
            raise ContractViolation("negative_cancel_after_ms")
        if self.reprice_after_ms is not None and self.reprice_after_ms < 0:
            raise ContractViolation("negative_reprice_after_ms")
        if self.reprice_after_ms is not None and self.reprice_price is None:
            raise ContractViolation("reprice_price_required")
        if self.order_id is not None and not self.order_id.startswith("sim_order_"):
            raise ContractViolation("invalid_simulation_order_id")
        if self.parent_order_id is not None and not self.parent_order_id.startswith(
            "sim_order_"
        ):
            raise ContractViolation("invalid_parent_simulation_order_id")

    @property
    def deterministic_order_id(self) -> str:
        if self.order_id is not None:
            return self.order_id
        payload = self.to_dict(include_order_id=False)
        return f"sim_order_{stable_hash(payload)[:32]}"

    def to_dict(self, *, include_order_id: bool = True) -> dict[str, Any]:
        payload = {
            "namespace": self.namespace,
            "client_intent_id": self.client_intent_id,
            "symbol": normalize_symbol(self.symbol),
            "side": self.side.order_side.value,
            "quantity": decimal_text(self.quantity),
            "order_type": self.order_type.value,
            "time_in_force": self.time_in_force.value,
            "submit_time_utc": to_utc(self.submit_time_utc).isoformat(),
            "limit_price": decimal_text(self.limit_price),
            "stop_price": decimal_text(self.stop_price),
            "cancel_after_ms": self.cancel_after_ms,
            "reprice_after_ms": self.reprice_after_ms,
            "reprice_price": decimal_text(self.reprice_price),
            "reduce_only": self.reduce_only,
            "simulate_api_timeout": self.simulate_api_timeout,
            "parent_order_id": self.parent_order_id,
        }
        if include_order_id:
            payload["order_id"] = self.deterministic_order_id
        return payload


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    liquidity_role: LiquidityRole
    timestamp_utc: datetime
    fee: Decimal | None
    remaining_quantity: Decimal
    source_event_id: str

    def __post_init__(self) -> None:
        if not self.fill_id.startswith("sim_fill_"):
            raise ContractViolation("invalid_simulation_fill_id")
        if not self.order_id.startswith("sim_order_"):
            raise ContractViolation("invalid_fill_order_id")
        _validate_symbol(self.symbol)
        _require_positive("fill_quantity", self.quantity)
        _require_positive("fill_price", self.price)
        _require_non_negative("remaining_quantity", self.remaining_quantity)
        if self.fee is not None:
            _require_non_negative("fill_fee", self.fee)
        _require_utc("fill_timestamp_utc", self.timestamp_utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.order_side.value,
            "quantity": decimal_text(self.quantity),
            "price": decimal_text(self.price),
            "liquidity_role": self.liquidity_role.value,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "fee": decimal_text(self.fee),
            "remaining_quantity": decimal_text(self.remaining_quantity),
            "source_event_id": self.source_event_id,
        }


def normalize_symbol(value: str) -> str:
    normalized = str(value).strip().upper().replace("/", "").replace("_", "")
    _validate_symbol(normalized)
    return normalized


def normalize_side(value: str | Side) -> Side:
    if isinstance(value, Side):
        return value.order_side
    normalized = str(value).strip().upper()
    aliases = {
        "BUY": Side.BUY,
        "LONG": Side.BUY,
        "SELL": Side.SELL,
        "SHORT": Side.SELL,
    }
    if normalized not in aliases:
        raise ContractViolation("unsupported_side")
    return aliases[normalized]


def decimal_value(value: Any, *, field_name: str = "decimal") -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ContractViolation(f"invalid_decimal:{field_name}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ContractViolation(f"invalid_decimal:{field_name}") from exc
    if not parsed.is_finite():
        raise ContractViolation(f"non_finite_decimal:{field_name}")
    return parsed


def decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if not value.is_finite():
        raise ContractViolation("non_finite_decimal")
    return format(value, "f")


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractViolation("timezone_aware_datetime_required")
    return value.astimezone(UTC)


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return to_utc(value)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ContractViolation("invalid_utc_datetime") from exc
    return to_utc(parsed)


def _validate_symbol(value: str) -> None:
    normalized = str(value).strip()
    if not normalized or not normalized.isalnum():
        raise ContractViolation("invalid_symbol")


def _validate_simulation_or_source_event_id(value: str) -> None:
    normalized = str(value).strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ContractViolation("invalid_event_id")


def _require_utc(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractViolation(f"{name}_timezone_required")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ContractViolation(f"{name}_must_be_utc")


def _require_positive(name: str, value: Decimal) -> None:
    parsed = decimal_value(value, field_name=name)
    if parsed <= 0:
        raise ContractViolation(f"{name}_must_be_positive")


def _require_non_negative(name: str, value: Decimal) -> None:
    parsed = decimal_value(value, field_name=name)
    if parsed < 0:
        raise ContractViolation(f"{name}_must_be_non_negative")


def _is_sha256(value: str) -> bool:
    normalized = str(value).lower()
    return len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )
