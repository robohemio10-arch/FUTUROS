"""Deterministic event-driven futures execution engine for research only."""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping, Sequence

from smartcrypto.data.canonical_data_foundation_v2.contracts import stable_hash

from .contracts import (
    ContractViolation,
    EventType,
    Fill,
    InputAuthority,
    LiquidityRole,
    MarginMode,
    MarketEvent,
    OrderIntent,
    OrderState,
    OrderType,
    PostOnlyPolicy,
    QueueModel,
    SAFETY_FLAGS,
    Side,
    TimeInForce,
    decimal_text,
    decimal_value,
    normalize_side,
    parse_utc,
)
from .costs import (
    CostModel,
    ExecutionCostAttribution,
    ZERO,
    attribute_execution_cost,
    reconcile_costs,
)
from .events import EventStreamValidation, validate_event_stream
from .latency import LatencyProfile
from .margin import (
    LiquidationResult,
    MarginAccount,
    MarginEngine,
    Position,
    position_to_dict,
)
from .order_book import BookWalkResult, OrderBook, RawBookFill
from .portfolio import aggregate_exposure


@dataclass(frozen=True)
class ExecutionEngineConfig:
    seed: int = 42
    queue_model: QueueModel = QueueModel.PESSIMISTIC
    post_only_policy: PostOnlyPolicy = PostOnlyPolicy.REJECT
    stale_book_after_ms: int = 5_000
    order_timeout_ms: int = 30_000
    latency: LatencyProfile = LatencyProfile()
    contract_size: Decimal | None = Decimal("1")
    leverage: Decimal | None = Decimal("5")
    margin_mode: MarginMode | None = MarginMode.ISOLATED
    isolated_margin_buffer: Decimal = Decimal("1.1")
    source: str = "simulation_only"

    def __post_init__(self) -> None:
        if self.stale_book_after_ms < 0 or self.order_timeout_ms < 0:
            raise ContractViolation("negative_engine_timeout")
        if self.contract_size is not None and self.contract_size <= 0:
            raise ContractViolation("contract_size_must_be_positive")
        if self.leverage is not None and self.leverage <= 0:
            raise ContractViolation("leverage_must_be_positive")
        if self.isolated_margin_buffer < Decimal("1"):
            raise ContractViolation("isolated_margin_buffer_below_one")

    @property
    def config_hash(self) -> str:
        return stable_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "queue_model": self.queue_model.value,
            "post_only_policy": self.post_only_policy.value,
            "stale_book_after_ms": self.stale_book_after_ms,
            "order_timeout_ms": self.order_timeout_ms,
            "latency": self.latency.to_dict(),
            "latency_profile_hash": self.latency.profile_hash,
            "contract_size": decimal_text(self.contract_size),
            "leverage": decimal_text(self.leverage),
            "margin_mode": (
                self.margin_mode.value if self.margin_mode is not None else None
            ),
            "isolated_margin_buffer": decimal_text(self.isolated_margin_buffer),
            "source": self.source,
        }


@dataclass
class SimulatedOrder:
    intent: OrderIntent
    state: OrderState = OrderState.CREATED
    filled_quantity: Decimal = ZERO
    remaining_quantity: Decimal = ZERO
    fills: list[Fill] = field(default_factory=list)
    lifecycle_event_ids: list[str] = field(default_factory=list)
    rejection_reason: str | None = None
    timeout_requires_reconciliation: bool = False
    child_order_id: str | None = None

    def __post_init__(self) -> None:
        self.remaining_quantity = self.intent.quantity

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.intent.deterministic_order_id,
            "parent_order_id": self.intent.parent_order_id,
            "namespace": self.intent.namespace,
            "symbol": self.intent.symbol,
            "side": self.intent.side.order_side.value,
            "order_type": self.intent.order_type.value,
            "time_in_force": self.intent.time_in_force.value,
            "requested_quantity": decimal_text(self.intent.quantity),
            "filled_quantity": decimal_text(self.filled_quantity),
            "remaining_quantity": decimal_text(self.remaining_quantity),
            "state": self.state.value,
            "rejection_reason": self.rejection_reason,
            "timeout_requires_reconciliation": self.timeout_requires_reconciliation,
            "child_order_id": self.child_order_id,
            "fill_ids": [fill.fill_id for fill in self.fills],
            "lifecycle_event_ids": list(self.lifecycle_event_ids),
        }


@dataclass(frozen=True)
class EngineResult:
    status: str
    reason: str
    authoritative_result: bool
    fixture_only: bool
    input_authority: Mapping[str, Any]
    event_stream: EventStreamValidation | None
    lifecycle_events: tuple[MarketEvent, ...]
    orders: tuple[SimulatedOrder, ...]
    fills: tuple[Fill, ...]
    cost_attributions: tuple[ExecutionCostAttribution, ...]
    cost_summary: Mapping[str, Any]
    margin_updates: tuple[Mapping[str, Any], ...]
    liquidations: tuple[LiquidationResult, ...]
    positions: tuple[Position, ...]
    portfolio_exposure: Mapping[str, Any]
    metrics: Mapping[str, Any]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    deterministic_result_hash: str

    def to_dict(self, *, include_records: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": "futures_execution_engine_result_v2",
            "status": self.status,
            "reason": self.reason,
            "authoritative_result": self.authoritative_result,
            "fixture_only": self.fixture_only,
            "input_authority": dict(self.input_authority),
            "event_stream": (
                {
                    "ordered_event_count": len(self.event_stream.ordered_events),
                    "replay_event_ids": list(self.event_stream.replay_event_ids),
                    "duplicate_sequence_count": (
                        self.event_stream.duplicate_sequence_count
                    ),
                    "input_out_of_order": self.event_stream.input_out_of_order,
                    "deterministic_hash": self.event_stream.deterministic_hash,
                }
                if self.event_stream is not None
                else None
            ),
            "cost_summary": dict(self.cost_summary),
            "portfolio_exposure": dict(self.portfolio_exposure),
            "metrics": dict(self.metrics),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "deterministic_result_hash": self.deterministic_result_hash,
            "safety_flags": dict(SAFETY_FLAGS),
        }
        if include_records:
            payload.update(
                {
                    "lifecycle_events": [
                        event.to_dict() for event in self.lifecycle_events
                    ],
                    "orders": [order.to_dict() for order in self.orders],
                    "fills": [fill.to_dict() for fill in self.fills],
                    "cost_attributions": [
                        item.to_dict() for item in self.cost_attributions
                    ],
                    "margin_updates": [dict(item) for item in self.margin_updates],
                    "liquidations": [
                        item.to_dict() for item in self.liquidations
                    ],
                    "positions": [
                        position_to_dict(position) for position in self.positions
                    ],
                }
            )
        return payload


class _BookTimeline:
    def __init__(self, symbol: str, events: Sequence[MarketEvent]) -> None:
        self.symbol = symbol
        self.events = tuple(
            sorted(
                (
                    event
                    for event in events
                    if event.symbol == symbol
                    and event.event_type
                    in {EventType.BOOK_SNAPSHOT, EventType.BOOK_DELTA}
                ),
                key=lambda event: (
                    event.receive_time_utc,
                    event.event_time_utc,
                    event.sequence,
                    event.event_id,
                ),
            )
        )
        self.index = 0
        self.book: OrderBook | None = None

    def advance(self, time_utc: datetime) -> OrderBook:
        target = parse_utc(time_utc)
        while self.index < len(self.events):
            event = self.events[self.index]
            if event.receive_time_utc > target or event.event_time_utc > target:
                break
            if event.event_type == EventType.BOOK_SNAPSHOT:
                self.book = OrderBook.from_snapshot_event(event)
            elif self.book is None:
                raise ContractViolation("book_delta_before_snapshot")
            else:
                self.book.apply_delta(event)
            self.index += 1
        if self.book is None:
            raise ContractViolation("book_unavailable_at_order_arrival")
        return self.book


class EventDrivenExecutionEngine:
    """Pure simulation engine; it cannot access exchange, runtime, or order APIs."""

    def __init__(
        self,
        *,
        config: ExecutionEngineConfig,
        cost_model: CostModel,
        margin_engine: MarginEngine,
    ) -> None:
        self.config = config
        self.cost_model = cost_model
        self.margin_engine = margin_engine
        self._rng = random.Random(config.seed)
        self._internal_sequence = 10_000_000
        self._sample_index = 0

    def run(
        self,
        *,
        events: Sequence[MarketEvent],
        intents: Sequence[OrderIntent],
        input_authority: InputAuthority,
        initial_account: MarginAccount,
        correlations: Mapping[tuple[str, str], Decimal] | None = None,
    ) -> EngineResult:
        try:
            input_authority.validate()
        except ContractViolation as exc:
            return self._blocked_result(
                reason=exc.reason,
                input_authority=input_authority,
            )
        try:
            stream = validate_event_stream(events)
        except ContractViolation as exc:
            return self._blocked_result(
                reason=exc.reason,
                input_authority=input_authority,
            )
        if not intents:
            return self._blocked_result(
                reason="order_intents_required",
                input_authority=input_authority,
                event_stream=stream,
            )

        timelines = {
            symbol: _BookTimeline(symbol, stream.ordered_events)
            for symbol in sorted({intent.symbol for intent in intents})
        }
        trade_prints = tuple(
            event
            for event in stream.ordered_events
            if event.event_type == EventType.TRADE_PRINT
        )
        funding_events = tuple(
            event
            for event in stream.ordered_events
            if event.event_type == EventType.FUNDING_RATE
        )
        mark_events = tuple(
            event
            for event in stream.ordered_events
            if event.event_type == EventType.MARK_PRICE
        )

        account = initial_account
        orders: list[SimulatedOrder] = []
        lifecycle: list[MarketEvent] = []
        fills: list[Fill] = []
        attributions: list[ExecutionCostAttribution] = []
        margin_updates: list[Mapping[str, Any]] = []
        liquidations: list[LiquidationResult] = []
        latest_marks: dict[str, Decimal] = {}
        funding_cost_total = ZERO
        liquidation_penalty_total = ZERO
        applied_funding_ids: set[str] = set()
        applied_mark_ids: set[str] = set()
        blockers: list[str] = []
        warnings: list[str] = []

        for intent in sorted(
            intents,
            key=lambda item: (
                item.submit_time_utc,
                item.deterministic_order_id,
            ),
        ):
            funding_cost_total, account = self._apply_funding_until(
                time_utc=intent.submit_time_utc,
                events=funding_events,
                applied_ids=applied_funding_ids,
                account=account,
                latest_marks=latest_marks,
                current_total=funding_cost_total,
                lifecycle=lifecycle,
            )
            account, penalty = self._apply_marks_until(
                time_utc=intent.submit_time_utc,
                events=mark_events,
                applied_ids=applied_mark_ids,
                account=account,
                latest_marks=latest_marks,
                lifecycle=lifecycle,
                margin_updates=margin_updates,
                liquidations=liquidations,
            )
            liquidation_penalty_total += penalty
            try:
                parent, new_events, new_attributions, account = self._execute_one(
                    intent=intent,
                    timeline=timelines[intent.symbol],
                    trade_prints=trade_prints,
                    mark_events=mark_events,
                    account=account,
                )
            except ContractViolation as exc:
                parent = SimulatedOrder(intent=intent)
                parent.state = OrderState.REJECTED
                parent.rejection_reason = exc.reason
                new_events = [
                    self._lifecycle_event(
                        event_type=EventType.ORDER_REJECTED,
                        symbol=intent.symbol,
                        time_utc=intent.submit_time_utc,
                        payload={
                            "order_id": intent.deterministic_order_id,
                            "reason": exc.reason,
                        },
                    )
                ]
                new_attributions = []
            orders.append(parent)
            lifecycle.extend(new_events)
            fills.extend(parent.fills)
            attributions.extend(new_attributions)

            if (
                parent.remaining_quantity > 0
                and intent.reprice_after_ms is not None
                and intent.reprice_price is not None
                and parent.state
                in {
                    OrderState.CANCELLED,
                    OrderState.EXPIRED,
                    OrderState.PARTIALLY_FILLED,
                }
            ):
                reprice_time = intent.submit_time_utc + timedelta(
                    milliseconds=intent.reprice_after_ms
                )
                reprice_delay = self._latency_ms("reprice")
                child_submit = reprice_time + _milliseconds(reprice_delay)
                child_id = f"sim_order_{stable_hash({'parent': intent.deterministic_order_id, 'price': decimal_text(intent.reprice_price), 'time': child_submit.isoformat()})[:32]}"
                child_intent = replace(
                    intent,
                    quantity=parent.remaining_quantity,
                    submit_time_utc=child_submit,
                    limit_price=intent.reprice_price,
                    reprice_after_ms=None,
                    reprice_price=None,
                    cancel_after_ms=None,
                    order_id=child_id,
                    parent_order_id=intent.deterministic_order_id,
                    client_intent_id=f"{intent.client_intent_id}:reprice",
                )
                parent.child_order_id = child_id
                lifecycle.append(
                    self._lifecycle_event(
                        event_type=EventType.REPRICE,
                        symbol=intent.symbol,
                        time_utc=child_submit,
                        payload={
                            "order_id": intent.deterministic_order_id,
                            "child_order_id": child_id,
                            "new_limit_price": decimal_text(intent.reprice_price),
                        },
                    )
                )
                try:
                    child, child_events, child_attributions, account = (
                        self._execute_one(
                            intent=child_intent,
                            timeline=timelines[intent.symbol],
                            trade_prints=trade_prints,
                            mark_events=mark_events,
                            account=account,
                        )
                    )
                except ContractViolation as exc:
                    child = SimulatedOrder(intent=child_intent)
                    child.state = OrderState.REJECTED
                    child.rejection_reason = exc.reason
                    child_events = [
                        self._lifecycle_event(
                            event_type=EventType.ORDER_REJECTED,
                            symbol=intent.symbol,
                            time_utc=child_submit,
                            payload={
                                "order_id": child_id,
                                "reason": exc.reason,
                            },
                        )
                    ]
                    child_attributions = []
                orders.append(child)
                lifecycle.extend(child_events)
                fills.extend(child.fills)
                attributions.extend(child_attributions)

        final_time = max(
            (
                event.receive_time_utc
                for event in stream.ordered_events
            ),
            default=max(intent.submit_time_utc for intent in intents),
        )
        funding_cost_total, account = self._apply_funding_until(
            time_utc=final_time,
            events=funding_events,
            applied_ids=applied_funding_ids,
            account=account,
            latest_marks=latest_marks,
            current_total=funding_cost_total,
            lifecycle=lifecycle,
        )
        account, penalty = self._apply_marks_until(
            time_utc=final_time,
            events=mark_events,
            applied_ids=applied_mark_ids,
            account=account,
            latest_marks=latest_marks,
            lifecycle=lifecycle,
            margin_updates=margin_updates,
            liquidations=liquidations,
        )
        liquidation_penalty_total += penalty

        observed_spread = sum(
            (item.observed_spread_cost for item in attributions), ZERO
        )
        observed_walk = sum(
            (item.observed_book_walk_cost for item in attributions), ZERO
        )
        modeled_slippage = sum(
            (item.modeled_slippage_cost + item.uncertainty_cost for item in attributions),
            ZERO,
        )
        modeled_impact = sum(
            (item.modeled_market_impact_cost for item in attributions), ZERO
        )
        actual_price_pnl = account.realized_pnl
        frictionless_price_pnl = actual_price_pnl + observed_spread + observed_walk
        cost_summary = reconcile_costs(
            realized_price_pnl=frictionless_price_pnl,
            fills=fills,
            funding_fees=funding_cost_total,
            spread_cost=observed_spread,
            slippage_cost=modeled_slippage,
            market_impact_cost=observed_walk + modeled_impact,
            liquidation_penalty=liquidation_penalty_total,
            retry_reprice_costs=sum(
                (
                    self.cost_model.reprice_cost
                    for order in orders
                    if order.intent.parent_order_id is not None
                ),
                ZERO,
            ),
        )
        if cost_summary.reconciliation_residual != 0:
            blockers.append("cost_reconciliation_residual_nonzero")

        if account.positions:
            for position in account.positions:
                if position.symbol not in latest_marks:
                    timeline = timelines.get(position.symbol)
                    fallback_book = timeline.book if timeline is not None else None
                    if (
                        fallback_book is None
                        or not fallback_book.bids
                        or not fallback_book.asks
                    ):
                        warnings.append(f"mark_price_missing:{position.symbol}")
                    else:
                        latest_marks[position.symbol] = fallback_book.mid_price
        try:
            portfolio = aggregate_exposure(
                positions=account.positions,
                mark_prices=latest_marks,
                correlations=correlations,
            )
        except ContractViolation as exc:
            portfolio = {
                "status": "blocked",
                "reason": exc.reason,
                "risk_manager_updated": False,
                "operational_limits_published": False,
            }
            warnings.append(f"portfolio_exposure:{exc.reason}")

        metrics = _build_metrics(
            orders=orders,
            fills=fills,
            attributions=attributions,
            margin_updates=margin_updates,
            liquidations=liquidations,
            cost_summary=cost_summary.to_dict(),
            contract_size=self._contract_size(),
        )
        if any(
            fill.quantity <= 0 or fill.remaining_quantity < 0 for fill in fills
        ):
            blockers.append("invalid_fill_quantity")
        if any(
            order.filled_quantity > order.intent.quantity for order in orders
        ):
            blockers.append("order_overfill_detected")
        if input_authority.fixture_only:
            warnings.append("fixture_only_non_authoritative")
        if input_authority.legacy_research_non_authoritative:
            warnings.append("legacy_research_non_authoritative")
        if any(not item.authoritative for item in attributions):
            warnings.append("modeled_execution_cost_assumptions_present")

        status = (
            "blocked"
            if blockers
            else (
                "warning"
                if input_authority.quarantined
                or input_authority.legacy_research_non_authoritative
                else "ok"
            )
        )
        reason = (
            "execution_engine_blocked"
            if blockers
            else (
                "input_not_authoritative"
                if status == "warning"
                else "execution_engine_completed_research_only"
            )
        )
        deterministic_payload = {
            "status": status,
            "reason": reason,
            "authoritative_result": input_authority.authoritative
            and all(item.authoritative for item in attributions),
            "event_stream_hash": stream.deterministic_hash,
            "orders": [order.to_dict() for order in orders],
            "fills": [fill.to_dict() for fill in fills],
            "cost_summary": cost_summary.to_dict(),
            "margin_updates": list(margin_updates),
            "liquidations": [item.to_dict() for item in liquidations],
            "positions": [position_to_dict(item) for item in account.positions],
            "metrics": metrics,
            "config_hash": self.config.config_hash,
            "cost_model_hash": self.cost_model.cost_model_hash,
            "blockers": sorted(set(blockers)),
            "warnings": sorted(set(warnings)),
        }
        return EngineResult(
            status=status,
            reason=reason,
            authoritative_result=bool(
                deterministic_payload["authoritative_result"]
            ),
            fixture_only=input_authority.fixture_only,
            input_authority=input_authority.to_dict(),
            event_stream=stream,
            lifecycle_events=tuple(
                sorted(
                    lifecycle,
                    key=lambda event: (
                        event.event_time_utc,
                        event.sequence,
                        event.receive_time_utc,
                        event.event_id,
                    ),
                )
            ),
            orders=tuple(orders),
            fills=tuple(fills),
            cost_attributions=tuple(attributions),
            cost_summary=cost_summary.to_dict(),
            margin_updates=tuple(margin_updates),
            liquidations=tuple(liquidations),
            positions=account.positions,
            portfolio_exposure=portfolio,
            metrics=metrics,
            blockers=tuple(sorted(set(blockers))),
            warnings=tuple(sorted(set(warnings))),
            deterministic_result_hash=stable_hash(deterministic_payload),
        )

    def _execute_one(
        self,
        *,
        intent: OrderIntent,
        timeline: _BookTimeline,
        trade_prints: Sequence[MarketEvent],
        mark_events: Sequence[MarketEvent],
        account: MarginAccount,
    ) -> tuple[
        SimulatedOrder,
        list[MarketEvent],
        list[ExecutionCostAttribution],
        MarginAccount,
    ]:
        order = SimulatedOrder(intent=intent)
        lifecycle: list[MarketEvent] = []
        attributions: list[ExecutionCostAttribution] = []
        order_id = intent.deterministic_order_id
        signal_delay = self._latency_ms("signal_to_submit")
        exchange_delay = self._latency_ms("client_to_exchange")
        jitter = self._latency_ms("jitter")
        market_data_delay = self._latency_ms("market_data")
        submit_time = intent.submit_time_utc + _milliseconds(signal_delay)
        arrival_time = submit_time + _milliseconds(exchange_delay + jitter)
        lifecycle.append(
            self._lifecycle_event(
                event_type=EventType.SIGNAL_INTENT,
                symbol=intent.symbol,
                time_utc=intent.submit_time_utc,
                payload={"order_id": order_id, "intent": intent.to_dict()},
            )
        )
        lifecycle.append(
            self._lifecycle_event(
                event_type=EventType.ORDER_SUBMIT,
                symbol=intent.symbol,
                time_utc=submit_time,
                payload={"order_id": order_id},
            )
        )
        if intent.simulate_api_timeout:
            order.state = OrderState.UNKNOWN
            order.timeout_requires_reconciliation = True
            timeout_event = self._lifecycle_event(
                event_type=EventType.TIMEOUT,
                symbol=intent.symbol,
                time_utc=arrival_time,
                payload={
                    "order_id": order_id,
                    "state": OrderState.UNKNOWN.value,
                    "requires_reconciliation": True,
                },
            )
            lifecycle.append(timeout_event)
            order.lifecycle_event_ids = [item.event_id for item in lifecycle]
            return order, lifecycle, attributions, account

        book = timeline.advance(arrival_time - _milliseconds(market_data_delay))
        if book.is_stale(arrival_time, self.config.stale_book_after_ms):
            raise ContractViolation("stale_order_book")
        ack_time = arrival_time + _milliseconds(self._latency_ms("exchange_ack"))
        order.state = OrderState.ACCEPTED
        lifecycle.append(
            self._lifecycle_event(
                event_type=EventType.ORDER_ACCEPTED,
                symbol=intent.symbol,
                time_utc=ack_time,
                payload={"order_id": order_id},
            )
        )

        effective_intent = intent
        if intent.order_type in {OrderType.STOP_MARKET, OrderType.STOP_LIMIT}:
            trigger_result = self._find_stop_trigger(
                intent,
                mark_events,
                ack_time,
                market_data_delay,
            )
            if trigger_result is None:
                order.state = OrderState.EXPIRED
                lifecycle.append(
                    self._lifecycle_event(
                        event_type=EventType.TIMEOUT,
                        symbol=intent.symbol,
                        time_utc=ack_time
                        + timedelta(milliseconds=self.config.order_timeout_ms),
                        payload={"order_id": order_id, "reason": "stop_not_triggered"},
                    )
                )
                order.lifecycle_event_ids = [item.event_id for item in lifecycle]
                return order, lifecycle, attributions, account
            trigger, trigger_available_at = trigger_result
            lifecycle.append(
                self._lifecycle_event(
                    event_type=EventType.STOP_TRIGGER,
                    symbol=intent.symbol,
                    time_utc=trigger_available_at,
                    payload={
                        "order_id": order_id,
                        "stop_price": decimal_text(intent.stop_price),
                        "mark_price_authority": True,
                    },
                )
            )
            ack_time = trigger_available_at
            book = timeline.advance(
                ack_time - _milliseconds(market_data_delay)
            )
            effective_intent = replace(
                intent,
                order_type=(
                    OrderType.MARKET
                    if intent.order_type == OrderType.STOP_MARKET
                    else OrderType.LIMIT
                ),
            )

        marketable = _is_marketable(effective_intent, book)
        if (
            effective_intent.order_type == OrderType.LIMIT_MAKER
            and marketable
        ):
            if self.config.post_only_policy == PostOnlyPolicy.REJECT:
                order.state = OrderState.REJECTED
                order.rejection_reason = "post_only_would_cross"
                lifecycle.append(
                    self._lifecycle_event(
                        event_type=EventType.ORDER_REJECTED,
                        symbol=intent.symbol,
                        time_utc=ack_time,
                        payload={
                            "order_id": order_id,
                            "reason": order.rejection_reason,
                        },
                    )
                )
                order.lifecycle_event_ids = [item.event_id for item in lifecycle]
                return order, lifecycle, attributions, account
            safe_price = (
                book.best_bid
                if intent.side.order_side == Side.BUY
                else book.best_ask
            )
            effective_intent = replace(
                effective_intent,
                order_type=OrderType.LIMIT,
                limit_price=safe_price,
            )
            marketable = False
            lifecycle.append(
                self._lifecycle_event(
                    event_type=EventType.REPRICE,
                    symbol=intent.symbol,
                    time_utc=ack_time,
                    payload={
                        "order_id": order_id,
                        "reason": "post_only_repriced",
                        "new_limit_price": decimal_text(safe_price),
                    },
                )
            )

        if effective_intent.order_type == OrderType.MARKET or marketable:
            limit_price = (
                effective_intent.limit_price
                if effective_intent.order_type != OrderType.MARKET
                else None
            )
            if effective_intent.time_in_force == TimeInForce.FOK:
                available = book.available_quantity(
                    side=effective_intent.side,
                    limit_price=limit_price,
                )
                if available < effective_intent.quantity:
                    order.state = OrderState.REJECTED
                    order.rejection_reason = "fok_depth_insufficient"
                    lifecycle.append(
                        self._lifecycle_event(
                            event_type=EventType.ORDER_REJECTED,
                            symbol=intent.symbol,
                            time_utc=ack_time,
                            payload={
                                "order_id": order_id,
                                "reason": order.rejection_reason,
                            },
                        )
                    )
                    order.lifecycle_event_ids = [item.event_id for item in lifecycle]
                    return order, lifecycle, attributions, account
            preview = book.walk(
                side=effective_intent.side,
                quantity=effective_intent.quantity,
                limit_price=limit_price,
                mutate=False,
            )
            self._validate_margin_capacity(
                intent=effective_intent,
                preview=preview,
                account=account,
            )
            benchmark_mid_price = book.mid_price
            benchmark_best_quote = (
                book.best_ask
                if effective_intent.side.order_side == Side.BUY
                else book.best_bid
            )
            walk = book.walk(
                side=effective_intent.side,
                quantity=effective_intent.quantity,
                limit_price=limit_price,
                mutate=True,
            )
            raw_fills = walk.fills
            liquidity_role = LiquidityRole.TAKER
            account = self._materialize_raw_fills(
                order=order,
                raw_fills=raw_fills,
                time_utc=ack_time,
                source_event_id=book.source_event_id,
                liquidity_role=liquidity_role,
                benchmark_mid_price=benchmark_mid_price,
                benchmark_best_quote=benchmark_best_quote,
                lifecycle=lifecycle,
                attributions=attributions,
                account=account,
            )
            if walk.unfilled_quantity > 0:
                if effective_intent.time_in_force == TimeInForce.FOK:
                    raise ContractViolation("fok_partial_fill_forbidden")
                order.state = (
                    OrderState.CANCELLED
                    if effective_intent.time_in_force == TimeInForce.IOC
                    else OrderState.PARTIALLY_FILLED
                )
                lifecycle.append(
                    self._lifecycle_event(
                        event_type=EventType.CANCEL_CONFIRMED,
                        symbol=intent.symbol,
                        time_utc=ack_time,
                        payload={
                            "order_id": order_id,
                            "remaining_quantity": decimal_text(
                                order.remaining_quantity
                            ),
                            "reason": "ioc_residual_cancelled"
                            if effective_intent.time_in_force
                            == TimeInForce.IOC
                            else "depth_insufficient",
                        },
                    )
                )
            else:
                order.state = OrderState.FILLED
        else:
            if effective_intent.time_in_force in {
                TimeInForce.IOC,
                TimeInForce.FOK,
            }:
                order.state = (
                    OrderState.CANCELLED
                    if effective_intent.time_in_force == TimeInForce.IOC
                    else OrderState.REJECTED
                )
                order.rejection_reason = (
                    None
                    if effective_intent.time_in_force == TimeInForce.IOC
                    else "fok_non_marketable"
                )
                lifecycle.append(
                    self._lifecycle_event(
                        event_type=(
                            EventType.CANCEL_CONFIRMED
                            if order.state == OrderState.CANCELLED
                            else EventType.ORDER_REJECTED
                        ),
                        symbol=intent.symbol,
                        time_utc=ack_time,
                        payload={
                            "order_id": order_id,
                            "reason": order.rejection_reason
                            or "ioc_non_marketable",
                        },
                    )
                )
            else:
                account = self._fill_resting_limit(
                    order=order,
                    intent=effective_intent,
                    book=book,
                    trade_prints=trade_prints,
                    accepted_time=ack_time,
                    lifecycle=lifecycle,
                    attributions=attributions,
                    account=account,
                )

        order.lifecycle_event_ids = [event.event_id for event in lifecycle]
        return order, lifecycle, attributions, account

    def _fill_resting_limit(
        self,
        *,
        order: SimulatedOrder,
        intent: OrderIntent,
        book: OrderBook,
        trade_prints: Sequence[MarketEvent],
        accepted_time: datetime,
        lifecycle: list[MarketEvent],
        attributions: list[ExecutionCostAttribution],
        account: MarginAccount,
    ) -> MarginAccount:
        if intent.limit_price is None:
            raise ContractViolation("limit_price_required")
        queue_ahead = self._queue_ahead(
            book.quantity_at_price(intent.side, intent.limit_price)
        )
        timeout_time = accepted_time + timedelta(
            milliseconds=self.config.order_timeout_ms
        )
        cancel_request_time: datetime | None = None
        cancel_confirm_time: datetime | None = None
        if intent.cancel_after_ms is not None:
            cancel_request_time = intent.submit_time_utc + timedelta(
                milliseconds=intent.cancel_after_ms
            )
            cancel_confirm_time = cancel_request_time + _milliseconds(
                self._latency_ms("cancel")
            )
            lifecycle.append(
                self._lifecycle_event(
                    event_type=EventType.CANCEL_REQUEST,
                    symbol=intent.symbol,
                    time_utc=cancel_request_time,
                    payload={"order_id": intent.deterministic_order_id},
                )
            )
        reprice_time = (
            intent.submit_time_utc
            + timedelta(milliseconds=intent.reprice_after_ms)
            if intent.reprice_after_ms is not None
            else None
        )
        horizon = min(
            [
                candidate
                for candidate in (timeout_time, cancel_confirm_time, reprice_time)
                if candidate is not None
            ]
        )
        for trade in sorted(
            trade_prints,
            key=lambda item: (
                item.receive_time_utc,
                item.event_time_utc,
                item.sequence,
                item.event_id,
            ),
        ):
            if trade.symbol != intent.symbol:
                continue
            if trade.receive_time_utc < accepted_time:
                continue
            if trade.receive_time_utc > horizon:
                break
            price = decimal_value(
                trade.payload.get("price"), field_name="trade_print_price"
            )
            quantity = decimal_value(
                trade.payload.get("quantity"), field_name="trade_print_quantity"
            )
            aggressor = normalize_side(
                str(trade.payload.get("aggressor_side", ""))
            )
            if not _trade_print_matches_resting_order(
                order_side=intent.side,
                aggressor_side=aggressor,
                trade_price=price,
                limit_price=intent.limit_price,
            ):
                continue
            if queue_ahead > 0:
                consumed_ahead = min(queue_ahead, quantity)
                queue_ahead -= consumed_ahead
                quantity -= consumed_ahead
            if quantity <= 0:
                continue
            fill_quantity = min(quantity, order.remaining_quantity)
            if fill_quantity <= 0:
                break
            raw_fill = RawBookFill(
                price=price,
                quantity=fill_quantity,
                level=1,
            )
            account = self._materialize_raw_fills(
                order=order,
                raw_fills=(raw_fill,),
                time_utc=trade.receive_time_utc,
                source_event_id=trade.event_id,
                liquidity_role=LiquidityRole.MAKER,
                benchmark_mid_price=book.mid_price,
                benchmark_best_quote=(
                    book.best_bid
                    if intent.side.order_side == Side.BUY
                    else book.best_ask
                ),
                lifecycle=lifecycle,
                attributions=attributions,
                account=account,
            )
            if order.remaining_quantity <= 0:
                order.state = OrderState.FILLED
                return account
        if cancel_confirm_time is not None and cancel_confirm_time <= horizon:
            order.state = OrderState.CANCELLED
            reason = "cancel_confirmed_after_latency"
            event_time = cancel_confirm_time
        elif reprice_time is not None and reprice_time <= horizon:
            order.state = (
                OrderState.PARTIALLY_FILLED
                if order.filled_quantity > 0
                else OrderState.CANCELLED
            )
            reason = "reprice_remainder"
            event_time = reprice_time
        else:
            order.state = (
                OrderState.PARTIALLY_FILLED
                if order.filled_quantity > 0
                else OrderState.EXPIRED
            )
            reason = "order_timeout"
            event_time = timeout_time
        lifecycle.append(
            self._lifecycle_event(
                event_type=(
                    EventType.CANCEL_CONFIRMED
                    if order.state == OrderState.CANCELLED
                    else EventType.TIMEOUT
                ),
                symbol=intent.symbol,
                time_utc=event_time,
                payload={
                    "order_id": intent.deterministic_order_id,
                    "remaining_quantity": decimal_text(order.remaining_quantity),
                    "reason": reason,
                },
            )
        )
        return account

    def _materialize_raw_fills(
        self,
        *,
        order: SimulatedOrder,
        raw_fills: Sequence[RawBookFill],
        time_utc: datetime,
        source_event_id: str,
        liquidity_role: LiquidityRole,
        benchmark_mid_price: Decimal,
        benchmark_best_quote: Decimal,
        lifecycle: list[MarketEvent],
        attributions: list[ExecutionCostAttribution],
        account: MarginAccount,
    ) -> MarginAccount:
        for raw in raw_fills:
            if raw.quantity > order.remaining_quantity:
                raise ContractViolation("fill_exceeds_order_remaining_quantity")
            fee = self.cost_model.fee_for_fill(
                quantity=raw.quantity,
                price=raw.price,
                contract_size=self._contract_size(),
                liquidity_role=liquidity_role,
            )
            remaining = order.remaining_quantity - raw.quantity
            fill_payload = {
                "order_id": order.intent.deterministic_order_id,
                "source_event_id": source_event_id,
                "quantity": decimal_text(raw.quantity),
                "price": decimal_text(raw.price),
                "fill_index": len(order.fills),
                "timestamp_utc": parse_utc(time_utc).isoformat(),
            }
            fill_id = f"sim_fill_{stable_hash(fill_payload)[:32]}"
            fill = Fill(
                fill_id=fill_id,
                order_id=order.intent.deterministic_order_id,
                symbol=order.intent.symbol,
                side=order.intent.side,
                quantity=raw.quantity,
                price=raw.price,
                liquidity_role=liquidity_role,
                timestamp_utc=parse_utc(time_utc),
                fee=fee,
                remaining_quantity=remaining,
                source_event_id=source_event_id,
            )
            attribution = attribute_execution_cost(
                side=order.intent.side,
                quantity=raw.quantity,
                contract_size=self._contract_size(),
                mid_price=benchmark_mid_price,
                best_quote=benchmark_best_quote,
                fill_price=raw.price,
                cost_model=self.cost_model,
            )
            order.fills.append(fill)
            order.filled_quantity += raw.quantity
            order.remaining_quantity = remaining
            order.state = (
                OrderState.FILLED
                if remaining == 0
                else OrderState.PARTIALLY_FILLED
            )
            lifecycle.append(
                self._lifecycle_event(
                    event_type=(
                        EventType.FILL
                        if remaining == 0
                        else EventType.PARTIAL_FILL
                    ),
                    symbol=order.intent.symbol,
                    time_utc=time_utc,
                    payload=fill.to_dict(),
                )
            )
            attributions.append(attribution)
            existing_position = next(
                (
                    position
                    for position in account.positions
                    if position.symbol == fill.symbol
                    and position.side.order_side != fill.side.order_side
                ),
                None,
            )
            closed_quantity = (
                min(existing_position.quantity, fill.quantity)
                if existing_position is not None
                else ZERO
            )
            account = self._apply_fill_to_account(
                account=account,
                intent=order.intent,
                fill=fill,
            )
            if closed_quantity > 0:
                lifecycle.append(
                    self._lifecycle_event(
                        event_type=EventType.POSITION_CLOSE,
                        symbol=order.intent.symbol,
                        time_utc=time_utc,
                        payload={
                            "order_id": order.intent.deterministic_order_id,
                            "fill_id": fill.fill_id,
                            "closed_quantity": decimal_text(closed_quantity),
                            "fully_closed": not any(
                                position.symbol == fill.symbol
                                for position in account.positions
                            ),
                        },
                    )
                )
        return account

    def _apply_fill_to_account(
        self,
        *,
        account: MarginAccount,
        intent: OrderIntent,
        fill: Fill,
    ) -> MarginAccount:
        positions = list(account.positions)
        existing_index = next(
            (
                index
                for index, position in enumerate(positions)
                if position.symbol == fill.symbol
            ),
            None,
        )
        if existing_index is None:
            if intent.reduce_only:
                raise ContractViolation("reduce_only_without_position")
            position = self._new_position(intent=intent, fill=fill)
            required = self._margin_allocation(position, fill.price)
            if account.available_balance < required:
                raise ContractViolation("insufficient_margin")
            positions.append(position)
            return replace(
                account,
                available_balance=account.available_balance - required,
                positions=tuple(positions),
            )

        position = positions[existing_index]
        same_side = position.side.order_side == fill.side.order_side
        if same_side:
            if intent.reduce_only:
                raise ContractViolation("reduce_only_increases_position")
            total_quantity = position.quantity + fill.quantity
            weighted_entry = (
                position.entry_price * position.quantity
                + fill.price * fill.quantity
            ) / total_quantity
            added_position = self._new_position(intent=intent, fill=fill)
            additional_margin = self._margin_allocation(
                added_position,
                fill.price,
            )
            if account.available_balance < additional_margin:
                raise ContractViolation("insufficient_margin")
            updated_isolated_margin = position.isolated_margin
            if position.margin_mode == MarginMode.ISOLATED:
                updated_isolated_margin = (
                    position.isolated_margin or ZERO
                ) + additional_margin
            positions[existing_index] = replace(
                position,
                quantity=total_quantity,
                entry_price=weighted_entry,
                isolated_margin=updated_isolated_margin,
            )
            return replace(
                account,
                available_balance=account.available_balance - additional_margin,
                positions=tuple(positions),
            )

        closing_quantity = min(position.quantity, fill.quantity)
        realized = (
            (fill.price - position.entry_price)
            * closing_quantity
            * (position.contract_size or ZERO)
            * position.side.direction
        )
        residual_position_quantity = position.quantity - closing_quantity
        excess_fill = fill.quantity - closing_quantity
        released_margin = self._released_margin(position, closing_quantity)
        if residual_position_quantity > 0:
            residual_isolated = position.isolated_margin
            if position.margin_mode == MarginMode.ISOLATED:
                residual_isolated = max(
                    ZERO,
                    (position.isolated_margin or ZERO) - released_margin,
                )
            positions[existing_index] = replace(
                position,
                quantity=residual_position_quantity,
                realized_pnl=position.realized_pnl + realized,
                isolated_margin=residual_isolated,
            )
        else:
            positions.pop(existing_index)
        excess_margin = ZERO
        if excess_fill > 0:
            if intent.reduce_only:
                raise ContractViolation("reduce_only_overfill_position")
            excess_fill_record = replace(fill, quantity=excess_fill)
            excess_position = self._new_position(
                intent=intent,
                fill=excess_fill_record,
            )
            excess_margin = self._margin_allocation(
                excess_position,
                fill.price,
            )
            positions.append(excess_position)
        available_after_close = (
            account.available_balance
            + released_margin
            + realized
            - excess_margin
        )
        if available_after_close < 0:
            raise ContractViolation("negative_available_balance_after_fill")
        return replace(
            account,
            realized_pnl=account.realized_pnl + realized,
            available_balance=available_after_close,
            positions=tuple(positions),
        )

    def _new_position(self, *, intent: OrderIntent, fill: Fill) -> Position:
        contract_size = self.config.contract_size
        leverage = self.config.leverage
        margin_mode = self.config.margin_mode
        if contract_size is None:
            raise ContractViolation("contract_size_missing")
        if leverage is None:
            raise ContractViolation("leverage_missing")
        if margin_mode is None:
            raise ContractViolation("margin_mode_missing")
        initial = fill.quantity * contract_size * fill.price / leverage
        isolated = (
            initial * self.config.isolated_margin_buffer
            if margin_mode == MarginMode.ISOLATED
            else None
        )
        return Position(
            position_id=f"sim_position_{stable_hash({'order_id': fill.order_id, 'fill_id': fill.fill_id})[:24]}",
            symbol=fill.symbol,
            side=fill.side,
            quantity=fill.quantity,
            entry_price=fill.price,
            contract_size=contract_size,
            leverage=leverage,
            margin_mode=margin_mode,
            isolated_margin=isolated,
            funding_accrued=ZERO,
        )

    def _margin_allocation(
        self,
        position: Position,
        mark_price: Decimal,
    ) -> Decimal:
        initial = self.margin_engine.initial_margin(position, mark_price)
        if position.margin_mode == MarginMode.ISOLATED:
            if position.isolated_margin is None:
                raise ContractViolation("isolated_margin_missing")
            return position.isolated_margin
        return initial

    def _released_margin(
        self,
        position: Position,
        closing_quantity: Decimal,
    ) -> Decimal:
        if closing_quantity <= 0 or closing_quantity > position.quantity:
            raise ContractViolation("invalid_margin_release_quantity")
        if position.margin_mode == MarginMode.ISOLATED:
            if position.isolated_margin is None:
                raise ContractViolation("isolated_margin_missing")
            return (
                position.isolated_margin
                * closing_quantity
                / position.quantity
            )
        return (
            position.entry_price
            * closing_quantity
            * (position.contract_size or ZERO)
            / (position.leverage or Decimal("1"))
        )

    def _validate_margin_capacity(
        self,
        *,
        intent: OrderIntent,
        preview: BookWalkResult,
        account: MarginAccount,
    ) -> None:
        if preview.filled_quantity <= 0 or preview.vwap is None:
            return
        if intent.reduce_only:
            if not any(
                position.symbol == intent.symbol
                and position.side.order_side != intent.side.order_side
                for position in account.positions
            ):
                raise ContractViolation("reduce_only_without_opposite_position")
            return
        temporary_fill = Fill(
            fill_id="sim_fill_margin_preview",
            order_id=intent.deterministic_order_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=preview.filled_quantity,
            price=preview.vwap,
            liquidity_role=LiquidityRole.TAKER,
            timestamp_utc=intent.submit_time_utc,
            fee=ZERO,
            remaining_quantity=intent.quantity - preview.filled_quantity,
            source_event_id="margin_preview",
        )
        position = self._new_position(intent=intent, fill=temporary_fill)
        required = self._margin_allocation(position, preview.vwap)
        if required > account.available_balance:
            raise ContractViolation("insufficient_margin")

    def _apply_funding_until(
        self,
        *,
        time_utc: datetime,
        events: Sequence[MarketEvent],
        applied_ids: set[str],
        account: MarginAccount,
        latest_marks: dict[str, Decimal],
        current_total: Decimal,
        lifecycle: list[MarketEvent],
    ) -> tuple[Decimal, MarginAccount]:
        positions = list(account.positions)
        total = current_total
        for event in events:
            if event.event_id in applied_ids or event.receive_time_utc > time_utc:
                continue
            rate_raw = event.payload.get("funding_rate")
            if rate_raw is None:
                raise ContractViolation("funding_rate_missing")
            rate = decimal_value(rate_raw, field_name="funding_rate")
            mark_raw = event.payload.get("mark_price")
            if mark_raw is not None:
                latest_marks[event.symbol] = decimal_value(
                    mark_raw, field_name="funding_mark_price"
                )
            if event.symbol not in latest_marks:
                raise ContractViolation("funding_mark_price_missing")
            for index, position in enumerate(positions):
                if position.symbol != event.symbol:
                    continue
                cost = self.cost_model.funding_cost(
                    side=position.side,
                    quantity=position.quantity,
                    contract_size=position.contract_size or ZERO,
                    mark_price=latest_marks[event.symbol],
                    funding_rate=rate,
                )
                total += cost
                positions[index] = replace(
                    position,
                    funding_accrued=(position.funding_accrued or ZERO) + cost,
                )
            applied_ids.add(event.event_id)
            lifecycle.append(event)
        return total, replace(account, positions=tuple(positions))

    def _apply_marks_until(
        self,
        *,
        time_utc: datetime,
        events: Sequence[MarketEvent],
        applied_ids: set[str],
        account: MarginAccount,
        latest_marks: dict[str, Decimal],
        lifecycle: list[MarketEvent],
        margin_updates: list[Mapping[str, Any]],
        liquidations: list[LiquidationResult],
    ) -> tuple[MarginAccount, Decimal]:
        penalty_total = ZERO
        current = account
        for event in events:
            if event.event_id in applied_ids or event.receive_time_utc > time_utc:
                continue
            mark = decimal_value(
                event.payload.get("mark_price"), field_name="mark_price"
            )
            if mark <= 0:
                raise ContractViolation("mark_price_must_be_positive")
            latest_marks[event.symbol] = mark
            applied_ids.add(event.event_id)
            lifecycle.append(event)
            matching = tuple(
                position
                for position in current.positions
                if position.symbol == event.symbol
            )
            for position in matching:
                metrics = self.margin_engine.evaluate(
                    account=replace(current, positions=(position,)),
                    mark_prices={event.symbol: mark},
                    mode=position.margin_mode or MarginMode.ISOLATED,
                )
                margin_payload = {
                    "event_id": event.event_id,
                    "symbol": event.symbol,
                    "mark_price": decimal_text(mark),
                    **metrics.to_dict(),
                }
                margin_updates.append(margin_payload)
                lifecycle.append(
                    self._lifecycle_event(
                        event_type=EventType.MARGIN_UPDATE,
                        symbol=event.symbol,
                        time_utc=event.receive_time_utc,
                        payload=margin_payload,
                    )
                )
                if metrics.liquidated:
                    result = self.margin_engine.liquidate(
                        position=position,
                        account=current,
                        mark_price=mark,
                    )
                    liquidations.append(result)
                    penalty_total += result.penalty
                    current = self._apply_liquidation(
                        account=current,
                        position=position,
                        result=result,
                        mark_price=mark,
                    )
                    lifecycle.append(
                        self._lifecycle_event(
                            event_type=EventType.LIQUIDATION,
                            symbol=event.symbol,
                            time_utc=event.receive_time_utc,
                            payload=result.to_dict(),
                        )
                    )
        return current, penalty_total

    def _apply_liquidation(
        self,
        *,
        account: MarginAccount,
        position: Position,
        result: LiquidationResult,
        mark_price: Decimal,
    ) -> MarginAccount:
        closed_quantity = result.liquidation_quantity
        realized = (
            (mark_price - position.entry_price)
            * closed_quantity
            * (position.contract_size or ZERO)
            * position.side.direction
        )
        positions = [
            item for item in account.positions if item.position_id != position.position_id
        ]
        if result.residual_position is not None:
            positions.append(result.residual_position)
        return replace(
            account,
            realized_pnl=account.realized_pnl + realized,
            wallet_balance=max(
                ZERO,
                account.wallet_balance + realized - result.penalty,
            ),
            available_balance=max(
                ZERO,
                account.available_balance + realized - result.penalty,
            ),
            positions=tuple(positions),
        )

    def _find_stop_trigger(
        self,
        intent: OrderIntent,
        mark_events: Sequence[MarketEvent],
        accepted_time: datetime,
        market_data_delay_ms: Decimal,
    ) -> tuple[MarketEvent, datetime] | None:
        if intent.stop_price is None:
            raise ContractViolation("stop_price_required")
        symbol_mark_events = [
            event
            for event in mark_events
            if event.symbol == intent.symbol
            and event.event_type == EventType.MARK_PRICE
        ]
        if not symbol_mark_events:
            raise ContractViolation("mark_price_authority_required_for_stop")
        for event in sorted(
            symbol_mark_events,
            key=lambda item: (
                item.receive_time_utc,
                item.event_time_utc,
                item.sequence,
                item.event_id,
            ),
        ):
            available_at = event.receive_time_utc + _milliseconds(
                market_data_delay_ms
            )
            if available_at < accepted_time:
                continue
            mark = decimal_value(
                event.payload.get("mark_price"), field_name="mark_price"
            )
            if (
                intent.side.order_side == Side.BUY
                and mark >= intent.stop_price
            ) or (
                intent.side.order_side == Side.SELL
                and mark <= intent.stop_price
            ):
                return event, available_at
        return None

    def _queue_ahead(self, level_quantity: Decimal) -> Decimal:
        if self.config.queue_model == QueueModel.DETERMINISTIC_FRONT:
            return ZERO
        if self.config.queue_model == QueueModel.PROPORTIONAL:
            return level_quantity / Decimal("2")
        if self.config.queue_model in {
            QueueModel.PESSIMISTIC,
            QueueModel.DETERMINISTIC_BACK,
        }:
            return level_quantity
        raise ContractViolation("unsupported_queue_model")

    def _latency_ms(self, name: str) -> Decimal:
        value = self.config.latency.sample(
            name,
            rng=self._rng,
            sample_index=self._sample_index,
        )
        self._sample_index += 1
        return value

    def _contract_size(self) -> Decimal:
        if self.config.contract_size is None:
            raise ContractViolation("contract_size_missing")
        return self.config.contract_size

    def _lifecycle_event(
        self,
        *,
        event_type: EventType,
        symbol: str,
        time_utc: datetime,
        payload: Mapping[str, Any],
    ) -> MarketEvent:
        self._internal_sequence += 1
        return MarketEvent.create(
            event_type=event_type,
            symbol=symbol,
            event_time_utc=parse_utc(time_utc),
            receive_time_utc=parse_utc(time_utc),
            sequence=self._internal_sequence,
            source=self.config.source,
            source_hash=self.config.config_hash,
            payload=payload,
        )

    def _blocked_result(
        self,
        *,
        reason: str,
        input_authority: InputAuthority,
        event_stream: EventStreamValidation | None = None,
    ) -> EngineResult:
        payload = {
            "status": "blocked",
            "reason": reason,
            "input_authority": input_authority.to_dict(),
            "config_hash": self.config.config_hash,
            "cost_model_hash": self.cost_model.cost_model_hash,
        }
        return EngineResult(
            status="blocked",
            reason=reason,
            authoritative_result=False,
            fixture_only=input_authority.fixture_only,
            input_authority=input_authority.to_dict(),
            event_stream=event_stream,
            lifecycle_events=(),
            orders=(),
            fills=(),
            cost_attributions=(),
            cost_summary={},
            margin_updates=(),
            liquidations=(),
            positions=(),
            portfolio_exposure={},
            metrics={},
            blockers=(reason,),
            warnings=(),
            deterministic_result_hash=stable_hash(payload),
        )


def _is_marketable(intent: OrderIntent, book: OrderBook) -> bool:
    if intent.order_type == OrderType.MARKET:
        return True
    if intent.limit_price is None:
        return False
    if intent.side.order_side == Side.BUY:
        return intent.limit_price >= book.best_ask
    return intent.limit_price <= book.best_bid


def _trade_print_matches_resting_order(
    *,
    order_side: Side,
    aggressor_side: Side,
    trade_price: Decimal,
    limit_price: Decimal,
) -> bool:
    if order_side.order_side == Side.BUY:
        return aggressor_side.order_side == Side.SELL and trade_price <= limit_price
    return aggressor_side.order_side == Side.BUY and trade_price >= limit_price


def _milliseconds(value: Decimal) -> timedelta:
    return timedelta(microseconds=int(value * Decimal("1000")))


def _build_metrics(
    *,
    orders: Sequence[SimulatedOrder],
    fills: Sequence[Fill],
    attributions: Sequence[ExecutionCostAttribution],
    margin_updates: Sequence[Mapping[str, Any]],
    liquidations: Sequence[LiquidationResult],
    cost_summary: Mapping[str, Any],
    contract_size: Decimal,
) -> dict[str, Any]:
    requested = sum((order.intent.quantity for order in orders), ZERO)
    filled = sum((fill.quantity for fill in fills), ZERO)
    price_quantity = sum((fill.quantity * fill.price for fill in fills), ZERO)
    notional = price_quantity * contract_size
    vwap = price_quantity / filled if filled > 0 else None
    maker_quantity = sum(
        (
            fill.quantity
            for fill in fills
            if fill.liquidity_role == LiquidityRole.MAKER
        ),
        ZERO,
    )
    taker_quantity = sum(
        (
            fill.quantity
            for fill in fills
            if fill.liquidity_role == LiquidityRole.TAKER
        ),
        ZERO,
    )
    margin_ratios = [
        decimal_value(item["margin_ratio"], field_name="margin_ratio")
        for item in margin_updates
        if item.get("margin_ratio") is not None
    ]
    buffers = [
        decimal_value(item["liquidation_buffer"], field_name="liquidation_buffer")
        for item in margin_updates
        if item.get("liquidation_buffer") is not None
    ]
    implementation_shortfall = sum(
        (item.explicit_price_cost for item in attributions), ZERO
    )
    observed_price_cost = sum(
        (
            item.observed_spread_cost + item.observed_book_walk_cost
            for item in attributions
        ),
        ZERO,
    )
    modeled_slippage_cost = sum(
        (
            item.modeled_slippage_cost + item.uncertainty_cost
            for item in attributions
        ),
        ZERO,
    )
    impact_cost = sum(
        (
            item.observed_book_walk_cost + item.modeled_market_impact_cost
            for item in attributions
        ),
        ZERO,
    )
    order_sides = {fill.side.order_side for fill in fills}
    arrival_price: Decimal | None = None
    if filled > 0 and len(order_sides) == 1 and vwap is not None:
        side_sign = (
            Decimal("1")
            if next(iter(order_sides)) == Side.BUY
            else Decimal("-1")
        )
        arrival_price = vwap - (
            side_sign
            * observed_price_cost
            / (filled * contract_size)
        )
    return {
        "requested_quantity": decimal_text(requested),
        "filled_quantity": decimal_text(filled),
        "unfilled_quantity": decimal_text(max(ZERO, requested - filled)),
        "fill_ratio": decimal_text(filled / requested if requested > 0 else ZERO),
        "vwap": decimal_text(vwap),
        "arrival_price": decimal_text(arrival_price),
        "implementation_shortfall": decimal_text(implementation_shortfall),
        "realized_spread": decimal_text(
            -sum((item.observed_spread_cost for item in attributions), ZERO)
        ),
        "slippage_bps": decimal_text(
            modeled_slippage_cost / notional * Decimal("10000")
            if notional > 0
            else None
        ),
        "market_impact_bps": decimal_text(
            impact_cost / notional * Decimal("10000")
            if notional > 0
            else None
        ),
        "maker_fill_ratio": decimal_text(
            maker_quantity / filled if filled > 0 else ZERO
        ),
        "taker_fill_ratio": decimal_text(
            taker_quantity / filled if filled > 0 else ZERO
        ),
        "total_fees": cost_summary.get("trading_fees"),
        "total_funding": cost_summary.get("funding_fees"),
        "total_cost": cost_summary.get("total_cost"),
        "gross_pnl": cost_summary.get("realized_price_pnl"),
        "net_pnl": cost_summary.get("net_pnl"),
        "max_margin_ratio": decimal_text(max(margin_ratios))
        if margin_ratios
        else None,
        "minimum_liquidation_buffer": decimal_text(min(buffers))
        if buffers
        else None,
        "partial_fill_count": sum(
            1 for order in orders if order.filled_quantity > 0 and order.remaining_quantity > 0
        ),
        "timeout_count": sum(
            1
            for order in orders
            if order.state in {OrderState.EXPIRED, OrderState.UNKNOWN}
        ),
        "reject_count": sum(
            1 for order in orders if order.state == OrderState.REJECTED
        ),
        "cancel_count": sum(
            1 for order in orders if order.state == OrderState.CANCELLED
        ),
        "liquidation_count": len(liquidations),
        "order_count": len(orders),
        "fill_count": len(fills),
        "no_overfill": all(
            order.filled_quantity <= order.intent.quantity for order in orders
        ),
    }
