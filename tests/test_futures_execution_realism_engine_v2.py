from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.build_futures_execution_realism_engine_v2 import main
from smartcrypto.data.canonical_data_foundation_v2.manifest import (
    ManifestValidationError,
)
from smartcrypto.research.futures_execution_realism_v2.contracts import (
    ContractViolation,
    EventType,
    Fill,
    InputAuthority,
    LatencyDistribution,
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
    SlippageModel,
    TimeInForce,
)
from smartcrypto.research.futures_execution_realism_v2.costs import (
    CostModel,
    attribute_execution_cost,
    reconcile_costs,
)
from smartcrypto.research.futures_execution_realism_v2.engine import (
    EventDrivenExecutionEngine,
)
from smartcrypto.research.futures_execution_realism_v2.events import (
    SimulationClock,
    events_available_at,
    order_events,
    validate_event_stream,
)
from smartcrypto.research.futures_execution_realism_v2.latency import (
    LatencyProfile,
    LatencySpec,
)
from smartcrypto.research.futures_execution_realism_v2.margin import (
    MaintenanceTier,
    MarginAccount,
    MarginEngine,
    Position,
    resolve_stop_vs_liquidation,
)
from smartcrypto.research.futures_execution_realism_v2.order_book import OrderBook
from smartcrypto.research.futures_execution_realism_v2.pipeline import (
    build_futures_execution_realism_report,
    build_synthetic_fixture,
    default_cost_model,
    default_engine_config,
    default_margin_engine,
)
from smartcrypto.research.futures_execution_realism_v2.portfolio import (
    aggregate_exposure,
)
from smartcrypto.research.futures_execution_realism_v2.reporting import (
    render_execution_markdown,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
SOURCE_HASH = "a" * 64


def event(
    event_type: EventType,
    *,
    seconds: float,
    sequence: int,
    payload: dict[str, object],
    event_id: str | None = None,
    symbol: str = "BTCUSDT",
    receive_delay_ms: int = 0,
) -> MarketEvent:
    event_time = BASE_TIME + timedelta(seconds=seconds)
    return MarketEvent.create(
        event_type=event_type,
        symbol=symbol,
        event_time_utc=event_time,
        receive_time_utc=event_time + timedelta(milliseconds=receive_delay_ms),
        sequence=sequence,
        source="fixture",
        source_hash=SOURCE_HASH,
        payload=payload,
        event_id=event_id,
    )


def snapshot(
    *,
    seconds: float = 0,
    sequence: int = 1,
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
) -> MarketEvent:
    return event(
        EventType.BOOK_SNAPSHOT,
        seconds=seconds,
        sequence=sequence,
        payload={
            "bids": bids or [["99", "2"], ["98", "3"]],
            "asks": asks or [["101", "1"], ["102", "4"]],
        },
    )


def intent(
    *,
    side: Side = Side.BUY,
    quantity: str = "1",
    order_type: OrderType = OrderType.MARKET,
    tif: TimeInForce = TimeInForce.GTC,
    seconds: float = 0.1,
    limit_price: str | None = None,
    stop_price: str | None = None,
    reduce_only: bool = False,
    cancel_after_ms: int | None = None,
    reprice_after_ms: int | None = None,
    reprice_price: str | None = None,
    simulate_api_timeout: bool = False,
) -> OrderIntent:
    return OrderIntent(
        symbol="BTCUSDT",
        side=side,
        quantity=Decimal(quantity),
        order_type=order_type,
        time_in_force=tif,
        submit_time_utc=BASE_TIME + timedelta(seconds=seconds),
        limit_price=Decimal(limit_price) if limit_price else None,
        stop_price=Decimal(stop_price) if stop_price else None,
        reduce_only=reduce_only,
        cancel_after_ms=cancel_after_ms,
        reprice_after_ms=reprice_after_ms,
        reprice_price=Decimal(reprice_price) if reprice_price else None,
        client_intent_id=f"test_{side.value}_{seconds}_{order_type.value}",
        simulate_api_timeout=simulate_api_timeout,
    )


def authority(*, quarantined: bool = False) -> InputAuthority:
    return InputAuthority(
        dataset_class="fixture",
        lineage_status="PERMANENT_QUARANTINE" if quarantined else "VERIFIED",
        candle_status="PERMANENT_QUARANTINE" if quarantined else "VERIFIED",
        fixture_only=not quarantined,
        legacy_research_non_authoritative=quarantined,
        source_hash=SOURCE_HASH,
    )


def engine(
    *,
    queue_model: QueueModel = QueueModel.PESSIMISTIC,
    post_only_policy: PostOnlyPolicy = PostOnlyPolicy.REJECT,
    latency: LatencyProfile | None = None,
    stale_after_ms: int = 5_000,
    cost_model: CostModel | None = None,
    margin_mode: MarginMode = MarginMode.ISOLATED,
) -> EventDrivenExecutionEngine:
    costs = cost_model or default_cost_model()
    config = replace(
        default_engine_config(seed=17),
        queue_model=queue_model,
        post_only_policy=post_only_policy,
        latency=latency or LatencyProfile(),
        stale_book_after_ms=stale_after_ms,
        margin_mode=margin_mode,
    )
    return EventDrivenExecutionEngine(
        config=config,
        cost_model=costs,
        margin_engine=default_margin_engine(costs),
    )


def run(
    events: tuple[MarketEvent, ...],
    intents: tuple[OrderIntent, ...],
    *,
    simulator: EventDrivenExecutionEngine | None = None,
    input_authority: InputAuthority | None = None,
    account: MarginAccount | None = None,
):
    return (simulator or engine()).run(
        events=events,
        intents=intents,
        input_authority=input_authority or authority(),
        initial_account=account
        or MarginAccount(
            wallet_balance=Decimal("10000"),
            available_balance=Decimal("10000"),
        ),
    )


def position(
    *,
    side: Side = Side.LONG,
    symbol: str = "BTCUSDT",
    quantity: str = "1",
    entry: str = "100",
    isolated_margin: str = "20",
    margin_mode: MarginMode = MarginMode.ISOLATED,
    funding: str | None = "0",
    leverage: str | None = "5",
    contract_size: str | None = "1",
) -> Position:
    return Position(
        position_id=f"pos_{symbol}_{side.value}",
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        entry_price=Decimal(entry),
        contract_size=Decimal(contract_size) if contract_size else None,
        leverage=Decimal(leverage) if leverage else None,
        margin_mode=margin_mode,
        isolated_margin=(
            Decimal(isolated_margin)
            if margin_mode == MarginMode.ISOLATED
            else None
        ),
        funding_accrued=Decimal(funding) if funding is not None else None,
    )


def margin_engine() -> MarginEngine:
    return default_margin_engine(default_cost_model())


def test_event_ordering_is_deterministic() -> None:
    first = event(EventType.MARK_PRICE, seconds=1, sequence=2, payload={"mark_price": "1"})
    second = event(EventType.MARK_PRICE, seconds=0, sequence=1, payload={"mark_price": "1"})
    assert order_events((first, second)) == (second, first)


def test_event_stream_blocks_out_of_order_input() -> None:
    later = event(EventType.MARK_PRICE, seconds=1, sequence=2, payload={"mark_price": "1"})
    earlier = event(EventType.MARK_PRICE, seconds=0, sequence=1, payload={"mark_price": "1"})
    with pytest.raises(ContractViolation, match="event_stream_out_of_order"):
        validate_event_stream((later, earlier))


def test_event_stream_replay_is_idempotent() -> None:
    source = event(
        EventType.MARK_PRICE,
        seconds=0,
        sequence=1,
        payload={"mark_price": "1"},
        event_id="sim_evt_replay",
    )
    validated = validate_event_stream((source, source))
    assert len(validated.ordered_events) == 1
    assert validated.replay_event_ids == ("sim_evt_replay",)


def test_event_stream_blocks_duplicate_sequence_conflict() -> None:
    first = event(EventType.MARK_PRICE, seconds=0, sequence=1, payload={"mark_price": "1"})
    second = event(EventType.MARK_PRICE, seconds=0, sequence=1, payload={"mark_price": "2"})
    with pytest.raises(ContractViolation, match="duplicate_sequence_conflict"):
        validate_event_stream((first, second))


def test_event_stream_blocks_regressive_sequence() -> None:
    first = event(EventType.MARK_PRICE, seconds=0, sequence=2, payload={"mark_price": "1"})
    second = event(EventType.MARK_PRICE, seconds=1, sequence=1, payload={"mark_price": "1"})
    with pytest.raises(ContractViolation, match="regressive_event_sequence"):
        validate_event_stream((first, second))


def test_future_event_is_not_available_to_past_decision() -> None:
    past = event(EventType.MARK_PRICE, seconds=0, sequence=1, payload={"mark_price": "1"})
    future = event(EventType.MARK_PRICE, seconds=2, sequence=2, payload={"mark_price": "2"})
    available = events_available_at((past, future), BASE_TIME + timedelta(seconds=1))
    assert available == (past,)


def test_injected_clock_blocks_regression() -> None:
    clock = SimulationClock(BASE_TIME)
    clock.advance_to(BASE_TIME + timedelta(seconds=1))
    with pytest.raises(ContractViolation, match="simulation_clock_regression"):
        clock.advance_to(BASE_TIME)


def test_same_seed_produces_same_lognormal_latency() -> None:
    spec = LatencySpec(
        distribution=LatencyDistribution.LOGNORMAL,
        lognormal_mu=1.0,
        lognormal_sigma=0.2,
    )
    first = spec.sample_ms(rng=random.Random(7), sample_index=0)
    second = spec.sample_ms(rng=random.Random(7), sample_index=0)
    assert first == second


def test_negative_latency_is_blocked() -> None:
    with pytest.raises(ContractViolation, match="negative_latency_forbidden"):
        LatencySpec(constant_ms=Decimal("-1"))


def test_valid_order_book_metrics() -> None:
    book = OrderBook.from_snapshot_event(snapshot())
    assert book.best_bid == Decimal("99")
    assert book.best_ask == Decimal("101")
    assert book.spread_absolute == Decimal("2")
    assert book.spread_bps > 0


@pytest.mark.parametrize(
    ("bids", "asks", "reason"),
    [
        ([["101", "1"]], [["101", "1"]], "crossed_or_locked_order_book"),
        ([["102", "1"]], [["101", "1"]], "crossed_or_locked_order_book"),
        ([], [["101", "1"]], "empty_book_side"),
        ([["99", "1"]], [], "empty_book_side"),
        ([["99", "0"]], [["101", "1"]], "book_quantity_must_be_positive"),
    ],
)
def test_invalid_order_books_are_blocked(
    bids: list[list[str]],
    asks: list[list[str]],
    reason: str,
) -> None:
    payload = {"bids": bids, "asks": asks}
    raw = event(EventType.BOOK_SNAPSHOT, seconds=0, sequence=1, payload=payload)
    with pytest.raises(ContractViolation, match=reason):
        OrderBook.from_snapshot_event(raw)


def test_book_walk_consumes_multiple_levels_and_calculates_vwap() -> None:
    book = OrderBook.from_snapshot_event(snapshot())
    result = book.walk(side=Side.BUY, quantity=Decimal("2"))
    assert [item.quantity for item in result.fills] == [
        Decimal("1"),
        Decimal("1"),
    ]
    assert result.vwap == Decimal("101.5")
    assert result.unfilled_quantity == 0


def test_book_walk_reports_depth_insufficient_without_inventing_liquidity() -> None:
    book = OrderBook.from_snapshot_event(snapshot())
    result = book.walk(side=Side.BUY, quantity=Decimal("10"))
    assert result.filled_quantity == Decimal("5")
    assert result.unfilled_quantity == Decimal("5")
    assert result.depth_insufficient is True


def test_limit_walk_never_executes_worse_than_limit() -> None:
    book = OrderBook.from_snapshot_event(snapshot())
    result = book.walk(
        side=Side.BUY,
        quantity=Decimal("2"),
        limit_price=Decimal("101"),
    )
    assert result.filled_quantity == Decimal("1")
    assert all(fill.price <= Decimal("101") for fill in result.fills)


def test_stale_book_blocks_execution() -> None:
    result = run(
        (snapshot(),),
        (intent(seconds=2),),
        simulator=engine(stale_after_ms=100),
    )
    assert result.orders[0].rejection_reason == "stale_order_book"


def test_market_order_consumes_book_in_price_priority() -> None:
    result = run((snapshot(),), (intent(quantity="2"),))
    assert [fill.price for fill in result.fills] == [
        Decimal("101"),
        Decimal("102"),
    ]
    assert result.orders[0].state == OrderState.FILLED
    assert result.metrics["arrival_price"] is not None
    assert result.metrics["slippage_bps"] is not None
    assert result.metrics["market_impact_bps"] is not None


def test_ioc_partial_fill_cancels_residual() -> None:
    result = run(
        (snapshot(asks=[["101", "1"]]),),
        (intent(quantity="2", tif=TimeInForce.IOC),),
    )
    order = result.orders[0]
    assert order.filled_quantity == Decimal("1")
    assert order.remaining_quantity == Decimal("1")
    assert order.state == OrderState.CANCELLED


def test_fok_blocks_when_full_depth_is_unavailable() -> None:
    result = run(
        (snapshot(asks=[["101", "1"]]),),
        (intent(quantity="2", tif=TimeInForce.FOK),),
    )
    assert result.orders[0].state == OrderState.REJECTED
    assert result.orders[0].rejection_reason == "fok_depth_insufficient"
    assert not result.fills


def test_fok_executes_only_when_full_depth_is_available() -> None:
    result = run(
        (snapshot(),),
        (intent(quantity="2", tif=TimeInForce.FOK),),
    )
    assert result.orders[0].state == OrderState.FILLED
    assert result.orders[0].remaining_quantity == 0


def test_post_only_cross_is_rejected_by_default() -> None:
    result = run(
        (snapshot(),),
        (
            intent(
                order_type=OrderType.LIMIT_MAKER,
                limit_price="101",
            ),
        ),
    )
    assert result.orders[0].rejection_reason == "post_only_would_cross"


def test_post_only_cross_can_be_explicitly_repriced() -> None:
    result = run(
        (snapshot(),),
        (
            intent(
                order_type=OrderType.LIMIT_MAKER,
                limit_price="101",
            ),
        ),
        simulator=engine(post_only_policy=PostOnlyPolicy.REPRICE),
    )
    assert result.orders[0].state in {
        OrderState.EXPIRED,
        OrderState.PARTIALLY_FILLED,
    }
    assert any(item.event_type == EventType.REPRICE for item in result.lifecycle_events)


def test_api_timeout_results_in_unknown_and_requires_reconciliation() -> None:
    result = run(
        (snapshot(),),
        (intent(simulate_api_timeout=True),),
    )
    order = result.orders[0]
    assert order.state == OrderState.UNKNOWN
    assert order.timeout_requires_reconciliation is True


def test_stop_market_uses_mark_price_authority() -> None:
    events = (
        snapshot(),
        event(EventType.MARK_PRICE, seconds=1, sequence=2, payload={"mark_price": "105"}),
    )
    result = run(
        events,
        (
            intent(
                order_type=OrderType.STOP_MARKET,
                stop_price="104",
                seconds=0.1,
            ),
        ),
    )
    assert any(
        item.event_type == EventType.STOP_TRIGGER
        for item in result.lifecycle_events
    )
    assert result.orders[0].filled_quantity > 0


def test_stop_order_without_mark_price_is_rejected() -> None:
    result = run(
        (snapshot(),),
        (
            intent(
                order_type=OrderType.STOP_MARKET,
                stop_price="104",
            ),
        ),
    )
    assert result.orders[0].rejection_reason == "mark_price_authority_required_for_stop"


def test_market_data_latency_delays_book_availability() -> None:
    latency = LatencyProfile(
        market_data=LatencySpec(constant_ms=Decimal("200"))
    )
    result = run(
        (snapshot(),),
        (intent(seconds=0.1),),
        simulator=engine(latency=latency),
    )
    assert result.orders[0].rejection_reason == "book_unavailable_at_order_arrival"


def test_pessimistic_queue_consumes_volume_ahead_before_fill() -> None:
    events = (
        snapshot(),
        event(
            EventType.TRADE_PRINT,
            seconds=1,
            sequence=2,
            payload={"price": "99", "quantity": "2", "aggressor_side": "SELL"},
        ),
    )
    result = run(
        events,
        (
            intent(
                order_type=OrderType.LIMIT,
                limit_price="99",
                seconds=0.1,
            ),
        ),
    )
    assert result.orders[0].filled_quantity == 0


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (QueueModel.DETERMINISTIC_FRONT, Decimal("1")),
        (QueueModel.PROPORTIONAL, Decimal("0.5")),
        (QueueModel.DETERMINISTIC_BACK, Decimal("0")),
        (QueueModel.PESSIMISTIC, Decimal("0")),
    ],
)
def test_queue_models_are_explicit_and_deterministic(
    model: QueueModel,
    expected: Decimal,
) -> None:
    events = (
        snapshot(),
        event(
            EventType.TRADE_PRINT,
            seconds=1,
            sequence=2,
            payload={"price": "99", "quantity": "1.5", "aggressor_side": "SELL"},
        ),
    )
    result = run(
        events,
        (
            intent(
                order_type=OrderType.LIMIT,
                limit_price="99",
                seconds=0.1,
            ),
        ),
        simulator=engine(queue_model=model),
    )
    assert result.orders[0].filled_quantity == expected


def test_fill_during_cancel_latency_is_preserved() -> None:
    latency = LatencyProfile(cancel=LatencySpec(constant_ms=Decimal("1000")))
    events = (
        snapshot(),
        event(
            EventType.TRADE_PRINT,
            seconds=0.8,
            sequence=2,
            payload={"price": "99", "quantity": "1", "aggressor_side": "SELL"},
        ),
    )
    result = run(
        events,
        (
            intent(
                order_type=OrderType.LIMIT,
                limit_price="99",
                cancel_after_ms=500,
            ),
        ),
        simulator=engine(
            queue_model=QueueModel.DETERMINISTIC_FRONT,
            latency=latency,
        ),
    )
    assert result.orders[0].filled_quantity == Decimal("1")
    assert result.orders[0].state == OrderState.FILLED


def test_reprice_creates_linked_child_order() -> None:
    result = run(
        (snapshot(),),
        (
            intent(
                order_type=OrderType.LIMIT,
                limit_price="98",
                reprice_after_ms=500,
                reprice_price="101",
            ),
        ),
    )
    assert len(result.orders) == 2
    assert result.orders[0].child_order_id == result.orders[1].intent.order_id
    assert result.orders[1].intent.parent_order_id == result.orders[0].intent.deterministic_order_id


def test_fills_never_overfill_order() -> None:
    result = run((snapshot(),), (intent(quantity="3"),))
    order = result.orders[0]
    assert sum(fill.quantity for fill in order.fills) <= order.intent.quantity
    assert result.metrics["no_overfill"] is True


def test_full_reduce_fill_emits_position_close_event() -> None:
    events, intents, source_authority = build_synthetic_fixture()
    result = run(events, intents, input_authority=source_authority)
    close_events = [
        item
        for item in result.lifecycle_events
        if item.event_type == EventType.POSITION_CLOSE
    ]
    assert len(close_events) == 1
    assert close_events[0].payload["fully_closed"] is True


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (LiquidityRole.MAKER, Decimal("0.02")),
        (LiquidityRole.TAKER, Decimal("0.04")),
    ],
)
def test_fee_is_computed_per_fill(role: LiquidityRole, expected: Decimal) -> None:
    fee = default_cost_model().fee_for_fill(
        quantity=Decimal("1"),
        price=Decimal("100"),
        contract_size=Decimal("1"),
        liquidity_role=role,
    )
    assert fee == expected


def test_unknown_liquidity_role_blocks_authoritative_fee() -> None:
    with pytest.raises(ContractViolation, match="unknown_liquidity_role"):
        default_cost_model().fee_for_fill(
            quantity=Decimal("1"),
            price=Decimal("100"),
            contract_size=Decimal("1"),
            liquidity_role=LiquidityRole.UNKNOWN,
        )


@pytest.mark.parametrize(
    ("side", "rate", "expected"),
    [
        (Side.LONG, "0.001", Decimal("0.1")),
        (Side.SHORT, "0.001", Decimal("-0.1")),
        (Side.LONG, "-0.001", Decimal("-0.1")),
        (Side.SHORT, "-0.001", Decimal("0.1")),
    ],
)
def test_funding_sign_is_side_aware(
    side: Side,
    rate: str,
    expected: Decimal,
) -> None:
    value = default_cost_model().funding_cost(
        side=side,
        quantity=Decimal("1"),
        contract_size=Decimal("1"),
        mark_price=Decimal("100"),
        funding_rate=Decimal(rate),
    )
    assert value == expected


def test_missing_funding_rate_blocks() -> None:
    with pytest.raises(ContractViolation, match="funding_rate_missing"):
        default_cost_model().funding_cost(
            side=Side.LONG,
            quantity=Decimal("1"),
            contract_size=Decimal("1"),
            mark_price=Decimal("100"),
            funding_rate=None,
        )


def test_cost_model_hash_is_deterministic() -> None:
    assert default_cost_model().cost_model_hash == default_cost_model().cost_model_hash


def test_observed_book_walk_separates_spread_and_impact() -> None:
    model = replace(
        default_cost_model(),
        slippage_model=SlippageModel.OBSERVED_BOOK_WALK,
    )
    attribution = attribute_execution_cost(
        side=Side.BUY,
        quantity=Decimal("1"),
        contract_size=Decimal("1"),
        mid_price=Decimal("100"),
        best_quote=Decimal("101"),
        fill_price=Decimal("102"),
        cost_model=model,
    )
    assert attribution.observed_spread_cost == Decimal("1.00000000")
    assert attribution.observed_book_walk_cost == Decimal("1.00000000")
    assert attribution.modeled_component == 0
    assert attribution.authoritative is True


def test_square_root_impact_requires_inputs() -> None:
    model = replace(
        default_cost_model(),
        slippage_model=SlippageModel.SQUARE_ROOT_IMPACT,
    )
    with pytest.raises(ContractViolation, match="square_root_impact_inputs_missing"):
        attribute_execution_cost(
            side=Side.BUY,
            quantity=Decimal("1"),
            contract_size=Decimal("1"),
            mid_price=Decimal("100"),
            best_quote=Decimal("101"),
            fill_price=Decimal("102"),
            cost_model=model,
        )


def test_cost_reconciliation_identity_is_exact() -> None:
    fill = Fill(
        fill_id="sim_fill_test",
        order_id="sim_order_test",
        symbol="BTCUSDT",
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        liquidity_role=LiquidityRole.TAKER,
        timestamp_utc=BASE_TIME,
        fee=Decimal("1"),
        remaining_quantity=Decimal("0"),
        source_event_id="sim_evt_test",
    )
    summary = reconcile_costs(
        realized_price_pnl=Decimal("10"),
        fills=(fill,),
        funding_fees=Decimal("1"),
        spread_cost=Decimal("2"),
        slippage_cost=Decimal("1"),
        market_impact_cost=Decimal("1"),
        liquidation_penalty=Decimal("1"),
    )
    assert summary.net_pnl == Decimal("3.00000000")
    assert summary.reconciliation_residual == 0


def test_missing_fill_fee_blocks_reconciliation() -> None:
    fill = Fill(
        fill_id="sim_fill_test",
        order_id="sim_order_test",
        symbol="BTCUSDT",
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        liquidity_role=LiquidityRole.TAKER,
        timestamp_utc=BASE_TIME,
        fee=None,
        remaining_quantity=Decimal("0"),
        source_event_id="sim_evt_test",
    )
    with pytest.raises(ContractViolation, match="fill_fee_missing"):
        reconcile_costs(
            realized_price_pnl=Decimal("1"),
            fills=(fill,),
            funding_fees=Decimal("0"),
            spread_cost=Decimal("0"),
            slippage_cost=Decimal("0"),
            market_impact_cost=Decimal("0"),
        )


@pytest.mark.parametrize(
    ("side", "mark", "expected_sign"),
    [
        (Side.LONG, "110", 1),
        (Side.SHORT, "90", 1),
        (Side.LONG, "90", -1),
        (Side.SHORT, "110", -1),
    ],
)
def test_unrealized_pnl_is_directional(
    side: Side,
    mark: str,
    expected_sign: int,
) -> None:
    value = position(side=side).unrealized_pnl(Decimal(mark))
    assert (value > 0) is (expected_sign > 0)


def test_margin_tier_changes_maintenance_requirement() -> None:
    evaluator = MarginEngine(
        tiers=(
            MaintenanceTier(Decimal("100"), Decimal("0.01")),
            MaintenanceTier(Decimal("1000"), Decimal("0.02")),
        ),
        cost_model=default_cost_model(),
    )
    assert evaluator.maintenance_margin(position(), Decimal("100")) == Decimal("1")
    assert evaluator.maintenance_margin(position(), Decimal("101")) == Decimal("2.02")


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"leverage": None}, "leverage_missing"),
        ({"contract_size": None}, "contract_size_missing"),
        ({"funding": None}, "funding_accrued_missing"),
    ],
)
def test_missing_margin_contract_fields_block(
    kwargs: dict[str, object],
    reason: str,
) -> None:
    value = position(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(ContractViolation, match=reason):
        value.validate()


def test_mark_price_missing_blocks_margin_evaluation() -> None:
    value = position()
    account = MarginAccount(
        wallet_balance=Decimal("100"),
        available_balance=Decimal("80"),
        positions=(value,),
    )
    with pytest.raises(ContractViolation, match="mark_price_missing"):
        margin_engine().evaluate(
            account=account,
            mark_prices={},
            mode=MarginMode.ISOLATED,
        )


def test_funding_reduces_liquidation_buffer() -> None:
    clean = position(funding="0")
    funded = position(funding="5")
    base = MarginAccount(
        wallet_balance=Decimal("100"),
        available_balance=Decimal("80"),
        positions=(clean,),
    )
    charged = replace(base, positions=(funded,))
    evaluator = margin_engine()
    clean_metrics = evaluator.evaluate(
        account=base,
        mark_prices={"BTCUSDT": Decimal("100")},
        mode=MarginMode.ISOLATED,
    )
    funded_metrics = evaluator.evaluate(
        account=charged,
        mark_prices={"BTCUSDT": Decimal("100")},
        mode=MarginMode.ISOLATED,
    )
    assert funded_metrics.liquidation_buffer < clean_metrics.liquidation_buffer


def test_cross_margin_aggregates_multiple_pairs() -> None:
    positions = (
        position(margin_mode=MarginMode.CROSS),
        position(
            symbol="ETHUSDT",
            side=Side.SHORT,
            entry="50",
            margin_mode=MarginMode.CROSS,
        ),
    )
    account = MarginAccount(
        wallet_balance=Decimal("1000"),
        available_balance=Decimal("1000"),
        positions=positions,
    )
    metrics = margin_engine().evaluate(
        account=account,
        mark_prices={"BTCUSDT": Decimal("101"), "ETHUSDT": Decimal("49")},
        mode=MarginMode.CROSS,
    )
    assert metrics.initial_margin > 0
    assert metrics.unrealized_pnl == Decimal("2")


def test_insufficient_margin_rejects_order() -> None:
    result = run(
        (snapshot(),),
        (intent(quantity="5"),),
        account=MarginAccount(
            wallet_balance=Decimal("1"),
            available_balance=Decimal("1"),
        ),
    )
    assert result.orders[0].rejection_reason == "insufficient_margin"


def test_open_fill_reserves_explicit_isolated_margin() -> None:
    simulator = engine()
    order_intent = intent()
    fill = Fill(
        fill_id="sim_fill_open_margin",
        order_id=order_intent.deterministic_order_id,
        symbol="BTCUSDT",
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        liquidity_role=LiquidityRole.TAKER,
        timestamp_utc=BASE_TIME,
        fee=Decimal("0.04"),
        remaining_quantity=Decimal("0"),
        source_event_id="sim_evt_margin",
    )
    account = simulator._apply_fill_to_account(  # noqa: SLF001
        account=MarginAccount(
            wallet_balance=Decimal("1000"),
            available_balance=Decimal("1000"),
        ),
        intent=order_intent,
        fill=fill,
    )
    assert account.available_balance == Decimal("978")
    assert account.positions[0].isolated_margin == Decimal("22")


def test_close_fill_releases_margin_and_applies_loss() -> None:
    simulator = engine()
    open_intent = intent()
    close_intent = intent(side=Side.SELL, reduce_only=True)
    open_fill = Fill(
        fill_id="sim_fill_open_margin",
        order_id=open_intent.deterministic_order_id,
        symbol="BTCUSDT",
        side=Side.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        liquidity_role=LiquidityRole.TAKER,
        timestamp_utc=BASE_TIME,
        fee=Decimal("0.04"),
        remaining_quantity=Decimal("0"),
        source_event_id="sim_evt_margin_open",
    )
    close_fill = replace(
        open_fill,
        fill_id="sim_fill_close_margin",
        order_id=close_intent.deterministic_order_id,
        side=Side.SELL,
        price=Decimal("90"),
        source_event_id="sim_evt_margin_close",
    )
    opened = simulator._apply_fill_to_account(  # noqa: SLF001
        account=MarginAccount(
            wallet_balance=Decimal("1000"),
            available_balance=Decimal("1000"),
        ),
        intent=open_intent,
        fill=open_fill,
    )
    closed = simulator._apply_fill_to_account(  # noqa: SLF001
        account=opened,
        intent=close_intent,
        fill=close_fill,
    )
    assert closed.positions == ()
    assert closed.realized_pnl == Decimal("-10")
    assert closed.available_balance == Decimal("990")


@pytest.mark.parametrize(
    ("side", "mark"),
    [(Side.LONG, "70"), (Side.SHORT, "130")],
)
def test_long_and_short_liquidation(side: Side, mark: str) -> None:
    value = position(side=side, isolated_margin="5")
    account = MarginAccount(
        wallet_balance=Decimal("100"),
        available_balance=Decimal("95"),
        positions=(value,),
    )
    result = margin_engine().liquidate(
        position=value,
        account=account,
        mark_price=Decimal(mark),
        allow_partial=False,
    )
    assert result.liquidated is True
    assert result.partial_liquidation is False
    assert result.residual_position is None
    assert result.penalty > 0


def test_partial_liquidation_can_preserve_residual_position() -> None:
    value = position(quantity="10", isolated_margin="205")
    account = MarginAccount(
        wallet_balance=Decimal("1000"),
        available_balance=Decimal("795"),
        positions=(value,),
    )
    result = margin_engine().liquidate(
        position=value,
        account=account,
        mark_price=Decimal("79"),
        allow_partial=True,
    )
    assert result.liquidated is True
    assert result.liquidation_quantity > 0
    if result.partial_liquidation:
        assert result.residual_position is not None
    else:
        assert result.bankruptcy_shortfall >= 0


def test_conservative_gap_policy_prioritizes_liquidation() -> None:
    assert (
        resolve_stop_vs_liquidation(
            stop_reachable=True,
            liquidation_reachable=True,
            intrabar_order_known=False,
        )
        == "LIQUIDATION_FIRST"
    )


@pytest.mark.parametrize(
    ("stop", "liquidation", "expected"),
    [
        (True, False, "STOP_ONLY"),
        (False, True, "LIQUIDATION_ONLY"),
        (False, False, "NO_TRIGGER"),
    ],
)
def test_stop_liquidation_resolution_states(
    stop: bool,
    liquidation: bool,
    expected: str,
) -> None:
    assert (
        resolve_stop_vs_liquidation(
            stop_reachable=stop,
            liquidation_reachable=liquidation,
            intrabar_order_known=True,
            stop_first_when_known=True,
        )
        == expected
    )


def test_portfolio_exposure_blocks_missing_cross_symbol_correlation() -> None:
    positions = (
        position(margin_mode=MarginMode.CROSS),
        position(
            symbol="ETHUSDT",
            side=Side.SHORT,
            entry="50",
            margin_mode=MarginMode.CROSS,
        ),
    )
    exposure = aggregate_exposure(
        positions=positions,
        mark_prices={"BTCUSDT": Decimal("100"), "ETHUSDT": Decimal("50")},
        correlations=None,
    )
    assert exposure["correlation_status"] == "blocked_missing_correlations"
    assert exposure["correlated_exposure"] is None
    assert exposure["risk_manager_updated"] is False


def test_quarantined_b02_input_never_becomes_authoritative() -> None:
    events, intents, _ = build_synthetic_fixture(input_mode="legacy_quarantined")
    result = run(events, intents, input_authority=authority(quarantined=True))
    assert result.status == "warning"
    assert result.reason == "input_not_authoritative"
    assert result.authoritative_result is False


def test_fixture_result_is_explicitly_non_authoritative() -> None:
    report = build_futures_execution_realism_report(project_root=Path.cwd())
    assert report["status"] == "ok"
    assert report["fixture_only"] is True
    assert report["authoritative_result"] is False
    assert report["fixture_only_runs"] == 1


def test_same_input_config_and_seed_produce_same_result_hash() -> None:
    events, intents, source_authority = build_synthetic_fixture()
    first = run(events, intents, input_authority=source_authority)
    second = run(events, intents, input_authority=source_authority)
    assert first.deterministic_result_hash == second.deterministic_result_hash


def test_no_write_is_default(tmp_path: Path) -> None:
    report = build_futures_execution_realism_report(project_root=tmp_path)
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data").exists()


def test_write_report_is_restricted_to_data_reports(tmp_path: Path) -> None:
    report = build_futures_execution_realism_report(
        project_root=tmp_path,
        write_report=True,
    )
    assert report["write_performed"] is True
    assert (tmp_path / "data/reports/futures_execution_realism_engine_v2.json").is_file()
    assert (tmp_path / "data/reports/futures_execution_realism_engine_v2.md").is_file()
    assert report["manifest_write_performed"] is True
    assert not (tmp_path / "data/runtime").exists()


def test_write_report_rejects_path_outside_reports(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="report_output_outside_data_reports"):
        build_futures_execution_realism_report(
            project_root=tmp_path,
            write_report=True,
            output_json="outside.json",
        )


def test_execution_manifest_never_overwrites_previous_execution(
    tmp_path: Path,
) -> None:
    build_futures_execution_realism_report(
        project_root=tmp_path,
        write_report=True,
    )
    with pytest.raises(
        ManifestValidationError,
        match="execution_manifest_already_exists",
    ):
        build_futures_execution_realism_report(
            project_root=tmp_path,
            write_report=True,
        )


def test_execution_manifest_contains_required_hashes_and_seed(tmp_path: Path) -> None:
    report = build_futures_execution_realism_report(project_root=tmp_path, seed=99)
    payload = report["execution_manifest"]["canonical_payload"]
    assert payload["dataset_hash"] == report["dataset_hash"]
    assert payload["cost_model_hash"] == report["cost_model"]["version"] or len(
        payload["cost_model_hash"]
    ) == 64
    assert payload["config_hash"] == report["engine_config"]["latency_profile_hash"] or len(
        payload["config_hash"]
    ) == 64
    assert payload["seed"] == 99
    assert report["manifest_reproducible"] is True


def test_cli_json_executes_without_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--project-root", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["write_performed"] is False
    assert not (tmp_path / "data").exists()


def test_markdown_exposes_research_boundary(tmp_path: Path) -> None:
    report = build_futures_execution_realism_report(project_root=tmp_path)
    markdown = render_execution_markdown(report)
    assert "Research only: `True`" in markdown
    assert "No exchange, Freqtrade, RiskManager" in markdown


def test_safety_flags_remain_closed() -> None:
    assert SAFETY_FLAGS["paper_only"] is True
    assert SAFETY_FLAGS["shadow_only"] is True
    assert SAFETY_FLAGS["research_only"] is True
    for name in (
        "operational_authority",
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "changes_model",
        "updates_freqtrade",
        "updates_risk_manager",
        "writes_active_signals",
        "writes_runtime",
    ):
        assert SAFETY_FLAGS[name] is False


def test_package_does_not_import_operational_execution_modules() -> None:
    package_root = (
        Path(__file__).resolve().parents[1]
        / "smartcrypto/research/futures_execution_realism_v2"
    )
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in package_root.glob("*.py")
    ).lower()
    assert "import freqtrade" not in source
    assert "import ccxt" not in source
    assert "risk_manager import" not in source
    assert "active_freqtrade_signals.json" not in source
