"""Typed contracts for W8 Execution Intelligence research.

The package is an offline execution simulator and intrabar exit laboratory.  It
has no authority over Freqtrade, RiskManager, active signals, private exchange
APIs, or models.  Future market path rows are permitted only as *simulation
outcomes*; every simulated action is evaluated against data that was available
at that action timestamp, preventing look-ahead inside the replay.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

REQUEST_SCHEMA_VERSION: Literal["execution_intelligence_request_v1"] = (
    "execution_intelligence_request_v1"
)
SNAPSHOT_SCHEMA_VERSION: Literal["execution_intelligence_snapshot_v1"] = (
    "execution_intelligence_snapshot_v1"
)

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=180,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,179}$",
    ),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ExecutionPolicyName(str, Enum):
    SINGLE_LIMIT = "single_limit"
    MAKER_REPRICE = "maker_reprice"
    TWAP = "twap"
    AGGRESSIVE_LIMIT = "aggressive_limit"


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class LiquidityRole(str, Enum):
    MAKER = "maker"
    TAKER = "taker"


class ExitPolicyName(str, Enum):
    BREAKEVEN = "breakeven"
    TRAILING = "trailing"
    TIME_STOP = "time_stop"


class ExitStatus(str, Enum):
    EXITED = "EXITED"
    OPEN_AT_PATH_END = "OPEN_AT_PATH_END"
    BLOCKED = "BLOCKED"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class CandleGranularity(str, Enum):
    SECOND_15 = "15s"
    MINUTE_1 = "1m"


class FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        allow_inf_nan=False,
    )


def require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    if offset.total_seconds() != 0:
        raise ValueError("timestamp_must_use_utc_offset_zero")
    return value.astimezone(timezone.utc)


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"unsupported_json_type:{type(value).__name__}")


class SafetyContract(FrozenContract):
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False
    network_required: Literal[False] = False
    network_calls_executed: Literal[False] = False
    live: Literal[False] = False
    canary: Literal[False] = False


class MarketSlice(FrozenContract):
    slice_id: Identifier
    source_id: Identifier
    symbol: Identifier
    event_time_utc: datetime
    available_at_utc: datetime
    best_bid: float = Field(gt=0)
    best_ask: float = Field(gt=0)
    bid_quantity: float = Field(ge=0)
    ask_quantity: float = Field(ge=0)
    last_price: float = Field(gt=0)
    traded_volume: float = Field(ge=0)
    volatility_bps: float = Field(default=0.0, ge=0, le=10_000)
    source_hash: Sha256Hex

    @field_validator("event_time_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_market(self) -> "MarketSlice":
        if self.event_time_utc > self.available_at_utc:
            raise ValueError("market_event_after_available")
        if self.best_bid >= self.best_ask:
            raise ValueError("crossed_or_locked_top_of_book")
        return self

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread_bps(self) -> float:
        return (self.best_ask - self.best_bid) / self.mid_price * 10_000.0


class ExecutionCostModel(FrozenContract):
    maker_fee_bps: float = Field(default=1.5, ge=0, le=500)
    taker_fee_bps: float = Field(default=4.0, ge=0, le=500)
    base_slippage_bps: float = Field(default=0.5, ge=0, le=500)
    impact_coefficient_bps: float = Field(default=8.0, ge=0, le=5_000)
    latency_penalty_bps_per_second: float = Field(default=0.10, ge=0, le=500)
    exit_cost_bps: float = Field(default=4.0, ge=0, le=500)


class ExecutionScenario(FrozenContract):
    scenario_id: Identifier
    strategy_id: Identifier
    symbol: Identifier
    side: Side
    quantity: float = Field(gt=0, le=1_000_000_000)
    policy: ExecutionPolicyName
    decision_time_utc: datetime
    market_path: tuple[MarketSlice, ...] = Field(min_length=1, max_length=20_000)
    submit_latency_ms: int = Field(default=50, ge=0, le=120_000)
    timeout_seconds: float = Field(default=30.0, gt=0, le=3_600)
    simulate_api_timeout: bool = False
    limit_offset_bps: float = Field(default=0.0, ge=-500, le=500)
    aggressive_limit_bps: float = Field(default=20.0, gt=0, le=2_000)
    participation_cap: float = Field(default=0.25, gt=0, le=1)
    reprice_interval_seconds: float = Field(default=2.0, gt=0, le=600)
    max_reprices: int = Field(default=3, ge=0, le=100)
    twap_slices: int = Field(default=4, ge=1, le=100)
    twap_interval_seconds: float = Field(default=5.0, gt=0, le=3_600)
    cost_model: ExecutionCostModel = ExecutionCostModel()

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_decision_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_path(self) -> "ExecutionScenario":
        prior: tuple[datetime, str] | None = None
        ids: set[str] = set()
        for item in self.market_path:
            if item.symbol != self.symbol:
                raise ValueError("market_path_symbol_mismatch")
            if item.slice_id in ids:
                raise ValueError("duplicate_market_slice_id")
            ids.add(item.slice_id)
            key = (item.available_at_utc, item.slice_id)
            if prior is not None and key < prior:
                raise ValueError("market_path_not_available_time_ordered")
            prior = key
        if self.policy == ExecutionPolicyName.MAKER_REPRICE and self.max_reprices < 1:
            raise ValueError("maker_reprice_requires_positive_max_reprices")
        if self.policy == ExecutionPolicyName.TWAP and self.twap_slices < 2:
            raise ValueError("twap_requires_multiple_slices")
        return self


class FillRecord(FrozenContract):
    fill_id: Identifier
    source_slice_id: Identifier
    fill_time_utc: datetime
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    liquidity_role: LiquidityRole
    fee_bps: float = Field(ge=0)
    impact_bps: float = Field(ge=0)
    child_index: int = Field(ge=0)

    @field_validator("fill_time_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class ExecutionEvaluation(FrozenContract):
    scenario_id: Identifier
    strategy_id: Identifier
    policy: ExecutionPolicyName
    status: ExecutionStatus
    reason: str = Field(min_length=1, max_length=240)
    requested_quantity: float = Field(gt=0)
    filled_quantity: float = Field(ge=0)
    fill_rate: float = Field(ge=0, le=1)
    fill_probability: float = Field(ge=0, le=1)
    probability_model: Literal["deterministic_liquidity_proxy_v1"] = (
        "deterministic_liquidity_proxy_v1"
    )
    partial_fill: bool
    timed_out: bool
    arrival_mid_price: float | None = Field(default=None, gt=0)
    average_fill_price: float | None = Field(default=None, gt=0)
    spread_bps: float | None = Field(default=None, ge=0)
    slippage_bps: float | None = Field(default=None, ge=0)
    market_impact_bps: float | None = Field(default=None, ge=0)
    fee_bps: float | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0)
    latency_cost_bps: float = Field(ge=0)
    total_execution_cost_bps: float | None = Field(default=None, ge=0)
    reprice_count: int = Field(ge=0)
    child_order_count: int = Field(ge=0)
    used_market_slice_ids: tuple[Identifier, ...]
    latest_used_available_at_utc: datetime | None = None
    lookahead_detected: Literal[False] = False
    fills: tuple[FillRecord, ...]

    @field_validator("latest_used_available_at_utc")
    @classmethod
    def _validate_optional_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)


class IntrabarBar(FrozenContract):
    bar_id: Identifier
    source_id: Identifier
    symbol: Identifier
    granularity: CandleGranularity
    start_time_utc: datetime
    end_time_utc: datetime
    available_at_utc: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(default=0.0, ge=0)
    source_hash: Sha256Hex

    @field_validator("start_time_utc", "end_time_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_bar(self) -> "IntrabarBar":
        if self.start_time_utc >= self.end_time_utc:
            raise ValueError("invalid_bar_time_range")
        if self.end_time_utc > self.available_at_utc:
            raise ValueError("bar_available_before_close")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("invalid_ohlc_envelope")
        if self.low > self.high:
            raise ValueError("bar_low_above_high")
        return self


class IntrabarExitScenario(FrozenContract):
    scenario_id: Identifier
    strategy_id: Identifier
    symbol: Identifier
    side: PositionSide
    entry_time_utc: datetime
    entry_price: float = Field(gt=0)
    notional_usdt: float = Field(gt=0)
    bars: tuple[IntrabarBar, ...] = Field(min_length=1, max_length=100_000)
    breakeven_trigger_bps: float = Field(default=20.0, gt=0, le=10_000)
    breakeven_lock_bps: float = Field(default=0.0, ge=0, le=5_000)
    trailing_activation_bps: float = Field(default=30.0, gt=0, le=10_000)
    trailing_distance_bps: float = Field(default=20.0, gt=0, le=10_000)
    time_stop_seconds: int = Field(default=3_600, gt=0, le=30 * 24 * 3_600)
    exit_cost_bps: float = Field(default=4.0, ge=0, le=500)

    @field_validator("entry_time_utc")
    @classmethod
    def _validate_entry_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_bars(self) -> "IntrabarExitScenario":
        ids: set[str] = set()
        granularity: CandleGranularity | None = None
        prior_end: datetime | None = None
        for bar in self.bars:
            if bar.symbol != self.symbol:
                raise ValueError("intrabar_symbol_mismatch")
            if bar.bar_id in ids:
                raise ValueError("duplicate_intrabar_bar_id")
            ids.add(bar.bar_id)
            if granularity is None:
                granularity = bar.granularity
            elif bar.granularity != granularity:
                raise ValueError("mixed_intrabar_granularity")
            if prior_end is not None and bar.start_time_utc < prior_end:
                raise ValueError("overlapping_intrabar_bars")
            prior_end = bar.end_time_utc
        return self


class ExitPolicyEvaluation(FrozenContract):
    scenario_id: Identifier
    strategy_id: Identifier
    policy: ExitPolicyName
    status: ExitStatus
    reason: str = Field(min_length=1, max_length=240)
    path_granularity: CandleGranularity
    intrabar_ordering: Literal["conservative_adverse_first"] = "conservative_adverse_first"
    exit_time_utc: datetime | None = None
    exit_price: float | None = Field(default=None, gt=0)
    gross_return_bps: float | None = None
    exit_cost_bps: float = Field(ge=0)
    net_return_bps: float | None = None
    hold_seconds: float = Field(ge=0)
    capital_hours_consumed: float = Field(ge=0)
    mfe_bps: float = Field(ge=0)
    mae_bps: float = Field(ge=0)
    activation_time_utc: datetime | None = None
    trigger_price: float | None = Field(default=None, gt=0)
    used_bar_ids: tuple[Identifier, ...]
    lookahead_detected: Literal[False] = False

    @field_validator("exit_time_utc", "activation_time_utc")
    @classmethod
    def _validate_optional_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_utc(value)


class ExecutionIntelligenceRequest(FrozenContract):
    schema_version: Literal["execution_intelligence_request_v1"] = REQUEST_SCHEMA_VERSION
    request_id: Identifier
    decision_time_utc: datetime
    execution_scenarios: tuple[ExecutionScenario, ...] = ()
    intrabar_exit_scenarios: tuple[IntrabarExitScenario, ...] = ()

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_decision_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_nonempty(self) -> "ExecutionIntelligenceRequest":
        if not self.execution_scenarios and not self.intrabar_exit_scenarios:
            raise ValueError("execution_intelligence_request_empty")
        scenario_ids = [item.scenario_id for item in self.execution_scenarios]
        scenario_ids.extend(item.scenario_id for item in self.intrabar_exit_scenarios)
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("duplicate_execution_intelligence_scenario_id")
        for scenario in self.execution_scenarios:
            if scenario.decision_time_utc != self.decision_time_utc:
                raise ValueError("execution_scenario_decision_time_mismatch")
        return self


class ExecutionIntelligenceSnapshot(FrozenContract):
    schema_version: Literal["execution_intelligence_snapshot_v1"] = SNAPSHOT_SCHEMA_VERSION
    snapshot_id: Identifier
    request_id: Identifier
    decision_time_utc: datetime
    created_at_utc: datetime
    status: ExecutionStatus
    reason: str = Field(min_length=1, max_length=240)
    execution_evaluations: tuple[ExecutionEvaluation, ...]
    intrabar_exit_evaluations: tuple[ExitPolicyEvaluation, ...]
    evaluated_count: int = Field(ge=0)
    partial_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    edge_proven: Literal[False] = False
    execution_policy_paper_authorized: Literal[False] = False
    safety: SafetyContract = SafetyContract()

    @field_validator("decision_time_utc", "created_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)
