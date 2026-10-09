"""Minimal observed L1 contracts, without fabricated last-trade or fill fields."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, TypeAlias

from pydantic import Field, TypeAdapter, field_validator, model_validator

from smartcrypto.research.execution_intelligence.contracts import (
    FrozenContract,
    Identifier,
    Sha256Hex,
    canonical_sha256,
    require_utc,
)

SOURCE_URL: Literal["https://fapi.binance.com/fapi/v1/ticker/bookTicker"] = (
    "https://fapi.binance.com/fapi/v1/ticker/bookTicker"
)
SOURCE_ID: Literal["binance-usdm-public-rest-bookticker-v1"] = (
    "binance-usdm-public-rest-bookticker-v1"
)
Symbol: TypeAlias = Literal["BTCUSDT", "ETHUSDT"]


class CollectorConfig(FrozenContract):
    symbols: tuple[Symbol, ...] = ("BTCUSDT", "ETHUSDT")
    poll_seconds: float = Field(default=2, ge=1, le=60)
    ledger_poll_seconds: float = Field(default=0.1, ge=0.01, le=5)
    request_timeout_seconds: float = Field(default=3, ge=0.1, le=10)
    backoff_max_seconds: float = Field(default=60, ge=5, le=3600)
    max_quote_age_seconds: float = Field(default=5, gt=0, le=60)
    gap_seconds: float = Field(default=5, gt=0, le=120)
    queue_capacity: int = Field(default=256, ge=1, le=10000)
    history_per_symbol: int = Field(default=256, ge=1, le=10000)
    max_decisions: int = Field(default=100000, ge=1, le=1000000)
    shutdown_seconds: float = Field(default=15, ge=1, le=30)
    segment_records: int = Field(default=32, ge=1, le=256)
    max_archive_bytes: int = Field(default=512 * 1024 * 1024, ge=1024, le=2 * 1024**3)

    @model_validator(mode="after")
    def valid_symbols(self) -> CollectorConfig:
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("nonempty_unique_symbols_required")
        return self


class RawBookTicker(FrozenContract):
    symbol: Symbol
    bidPrice: Decimal = Field(gt=0)
    askPrice: Decimal = Field(gt=0)
    bidQty: Decimal = Field(ge=0)
    askQty: Decimal = Field(ge=0)
    time: int | None = Field(default=None, gt=0, strict=True)


class L1Quote(FrozenContract):
    schema_version: Literal["execution_decision_l1_quote_v1"] = "execution_decision_l1_quote_v1"
    quote_id: Sha256Hex
    source_id: Literal["binance-usdm-public-rest-bookticker-v1"] = SOURCE_ID
    source_url: Literal["https://fapi.binance.com/fapi/v1/ticker/bookTicker"] = SOURCE_URL
    symbol: Symbol
    event_time_utc: datetime | None
    receive_time_utc: datetime
    available_at_utc: datetime
    available_monotonic_ns: int = Field(ge=0)
    best_bid: Decimal = Field(gt=0)
    best_ask: Decimal = Field(gt=0)
    spread_bps: Decimal = Field(gt=0)
    bid_quantity: Decimal = Field(ge=0)
    ask_quantity: Decimal = Field(ge=0)
    raw_body: str = Field(max_length=16384)
    raw_sha256: Sha256Hex
    availability_basis: Literal["OBSERVED_LOCAL_RECEIVE_AND_VALIDATION"] = (
        "OBSERVED_LOCAL_RECEIVE_AND_VALIDATION"
    )
    clock_synchronization: Literal["UNPROVEN"] = "UNPROVEN"

    @field_validator("event_time_utc", "receive_time_utc", "available_at_utc")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return require_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_observation(self) -> L1Quote:
        raw = RawBookTicker.model_validate_json(self.raw_body)
        if hashlib.sha256(self.raw_body.encode()).hexdigest() != self.raw_sha256:
            raise ValueError("raw_hash_mismatch")
        event_time = datetime.fromtimestamp(raw.time / 1000, timezone.utc) if raw.time else None
        if (
            self.symbol != raw.symbol
            or self.event_time_utc != event_time
            or self.best_bid != raw.bidPrice
            or self.best_ask != raw.askPrice
            or self.bid_quantity != raw.bidQty
            or self.ask_quantity != raw.askQty
        ):
            raise ValueError("quote_raw_projection_mismatch")
        if self.best_bid >= self.best_ask:
            raise ValueError("crossed_or_locked_quote")
        spread = (self.best_ask - self.best_bid) / ((self.best_bid + self.best_ask) / 2) * 10000
        if self.spread_bps != spread:
            raise ValueError("spread_projection_mismatch")
        if self.receive_time_utc > self.available_at_utc:
            raise ValueError("available_before_receive")
        if self.event_time_utc and self.event_time_utc > self.receive_time_utc:
            raise ValueError("exchange_clock_ahead_of_receive")
        if self.quote_id != canonical_sha256(self.model_dump(mode="json", exclude={"quote_id"})):
            raise ValueError("quote_id_hash_mismatch")
        return self


def build_quote(raw_body: str, receive: datetime, available: datetime, mono_ns: int) -> L1Quote:
    raw = RawBookTicker.model_validate_json(raw_body)
    payload = {
        "schema_version": "execution_decision_l1_quote_v1",
        "source_id": SOURCE_ID,
        "source_url": SOURCE_URL,
        "symbol": raw.symbol,
        "event_time_utc": datetime.fromtimestamp(raw.time / 1000, timezone.utc)
        if raw.time
        else None,
        "receive_time_utc": receive,
        "available_at_utc": available,
        "available_monotonic_ns": mono_ns,
        "best_bid": raw.bidPrice,
        "best_ask": raw.askPrice,
        "spread_bps": (raw.askPrice - raw.bidPrice) / ((raw.bidPrice + raw.askPrice) / 2) * 10000,
        "bid_quantity": raw.bidQty,
        "ask_quantity": raw.askQty,
        "raw_body": raw_body,
        "raw_sha256": hashlib.sha256(raw_body.encode()).hexdigest(),
        "availability_basis": "OBSERVED_LOCAL_RECEIVE_AND_VALIDATION",
        "clock_synchronization": "UNPROVEN",
    }
    # Pydantic JSON formatting (not Decimal -> float) is the canonical quote seal.
    provisional = L1Quote.model_construct(quote_id="0" * 64, **payload)
    return L1Quote.model_validate(
        {
            **payload,
            "quote_id": canonical_sha256(provisional.model_dump(mode="json", exclude={"quote_id"})),
        }
    )


class DecisionL1Association(FrozenContract):
    schema_version: Literal["execution_decision_l1_association_v1"] = (
        "execution_decision_l1_association_v1"
    )
    decision_id: Identifier
    candidate_id: Identifier
    signal_id: Identifier
    decision_payload_sha256: Sha256Hex
    symbol: Identifier
    decision_timestamp_utc: datetime
    observed_at_utc: datetime
    quote: L1Quote | None
    status: Literal["MATCHED_PIT", "UNAVAILABLE"]
    reason: Identifier
    feature_age_seconds: float | None = Field(default=None, ge=0)

    @field_validator("decision_timestamp_utc", "observed_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def strict_pit(self) -> DecisionL1Association:
        if self.observed_at_utc < self.decision_timestamp_utc:
            raise ValueError("decision_after_observation")
        if self.status == "MATCHED_PIT":
            if self.quote is None or self.quote.event_time_utc is None:
                raise ValueError("matched_quote_with_event_time_required")
            if (
                self.quote.symbol != self.symbol
                or self.quote.available_at_utc > self.decision_timestamp_utc
            ):
                raise ValueError("quote_not_available_at_decision")
            age = (self.decision_timestamp_utc - self.quote.event_time_utc).total_seconds()
            if self.feature_age_seconds != age or age < 0:
                raise ValueError("feature_age_mismatch")
        elif self.quote is not None or self.feature_age_seconds is not None:
            raise ValueError("unavailable_association_cannot_contain_quote")
        return self


class GapEvidence(FrozenContract):
    schema_version: Literal["execution_decision_l1_gap_v1"] = "execution_decision_l1_gap_v1"
    observed_at_utc: datetime
    symbol: Identifier | None = None
    reason: Identifier
    missing_market_message_count: None = None

    @field_validator("observed_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value)


EventRecord: TypeAlias = L1Quote | DecisionL1Association | GapEvidence
EVENT_ADAPTER: TypeAdapter[EventRecord] = TypeAdapter(EventRecord)


def schema_sha256() -> str:
    return canonical_sha256(EVENT_ADAPTER.json_schema())
