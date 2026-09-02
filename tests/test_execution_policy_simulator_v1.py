from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smartcrypto.research.execution_intelligence import (
    ExecutionCostModel,
    ExecutionIntelligenceRequest,
    ExecutionPolicyName,
    ExecutionScenario,
    ExecutionStatus,
    MarketSlice,
    Side,
    build_snapshot,
)

UTC = timezone.utc
HASH_A = "a" * 64
BASE = datetime(2026, 8, 28, 18, 0, tzinfo=UTC)


def market(
    index: int,
    seconds: int,
    *,
    bid: float = 100.0,
    ask: float = 100.2,
    bid_qty: float = 3.0,
    ask_qty: float = 3.0,
    last: float = 100.1,
    volume: float = 20.0,
    available_delay: int = 0,
) -> MarketSlice:
    event_time = BASE + timedelta(seconds=seconds)
    return MarketSlice(
        slice_id=f"slice-{index}",
        source_id="fixture",
        symbol="BTCUSDT",
        event_time_utc=event_time,
        available_at_utc=event_time + timedelta(seconds=available_delay),
        best_bid=bid,
        best_ask=ask,
        bid_quantity=bid_qty,
        ask_quantity=ask_qty,
        last_price=last,
        traded_volume=volume,
        volatility_bps=10.0,
        source_hash=HASH_A,
    )


def scenario(policy: ExecutionPolicyName, *, scenario_id: str | None = None) -> ExecutionScenario:
    path = (
        market(1, 0, last=100.1),
        market(2, 2, bid=99.9, ask=100.1, last=100.0, volume=30.0),
        market(3, 4, bid=99.8, ask=100.0, last=99.9, volume=30.0),
        market(4, 6, bid=99.7, ask=99.9, last=99.8, volume=30.0),
    )
    return ExecutionScenario(
        scenario_id=scenario_id or f"scenario-{policy.value}",
        strategy_id="execution-research-v1",
        symbol="BTCUSDT",
        side=Side.BUY,
        quantity=4.0,
        policy=policy,
        decision_time_utc=BASE,
        market_path=path,
        submit_latency_ms=0,
        timeout_seconds=8,
        participation_cap=0.5,
        reprice_interval_seconds=2,
        max_reprices=3,
        twap_slices=3,
        twap_interval_seconds=2,
        aggressive_limit_bps=50,
        cost_model=ExecutionCostModel(
            maker_fee_bps=1.0,
            taker_fee_bps=4.0,
            base_slippage_bps=0.5,
            impact_coefficient_bps=3.0,
            latency_penalty_bps_per_second=0.1,
        ),
    )


def test_all_four_execution_policies_are_supported_and_replay_deterministic() -> None:
    request = ExecutionIntelligenceRequest(
        request_id="w8-all-policies",
        decision_time_utc=BASE,
        execution_scenarios=tuple(
            scenario(policy) for policy in ExecutionPolicyName
        ),
    )
    first = build_snapshot(request)
    second = build_snapshot(request)
    assert first.snapshot_id == second.snapshot_id
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert {item.policy for item in first.execution_evaluations} == set(ExecutionPolicyName)
    assert first.safety.sends_orders is False
    assert first.safety.exchange_private_access is False
    assert first.execution_policy_paper_authorized is False
    assert first.edge_proven is False


def test_aggressive_limit_reports_partial_fill_spread_slippage_latency_and_impact() -> None:
    item = scenario(ExecutionPolicyName.AGGRESSIVE_LIMIT).model_copy(
        update={
            "quantity": 20.0,
            "submit_latency_ms": 250,
            "participation_cap": 0.10,
        }
    )
    snapshot = build_snapshot(
        ExecutionIntelligenceRequest(
            request_id="aggressive-partial",
            decision_time_utc=BASE,
            execution_scenarios=(item,),
        )
    )
    result = snapshot.execution_evaluations[0]
    assert result.status == ExecutionStatus.PARTIAL
    assert 0 < result.fill_rate < 1
    assert result.partial_fill is True
    assert result.timed_out is False
    assert result.spread_bps is not None and result.spread_bps > 0
    assert result.slippage_bps is not None and result.slippage_bps > 0
    assert result.market_impact_bps is not None and result.market_impact_bps > 0
    assert result.latency_ms == 250
    assert result.total_execution_cost_bps is not None and result.total_execution_cost_bps > 0


def test_single_limit_uses_only_market_rows_available_before_timeout() -> None:
    item = scenario(ExecutionPolicyName.SINGLE_LIMIT)
    snapshot = build_snapshot(
        ExecutionIntelligenceRequest(
            request_id="single-limit-causal",
            decision_time_utc=BASE,
            execution_scenarios=(item,),
        )
    )
    result = snapshot.execution_evaluations[0]
    assert result.lookahead_detected is False
    assert result.latest_used_available_at_utc is not None
    assert result.latest_used_available_at_utc <= BASE + timedelta(seconds=8)


def test_future_market_row_is_not_visible_before_its_available_time() -> None:
    future_only = ExecutionScenario(
        scenario_id="future-hidden",
        strategy_id="execution-research-v1",
        symbol="BTCUSDT",
        side=Side.BUY,
        quantity=1.0,
        policy=ExecutionPolicyName.AGGRESSIVE_LIMIT,
        decision_time_utc=BASE,
        market_path=(market(1, 10),),
        submit_latency_ms=0,
        timeout_seconds=1,
    )
    snapshot = build_snapshot(
        ExecutionIntelligenceRequest(
            request_id="future-hidden-request",
            decision_time_utc=BASE,
            execution_scenarios=(future_only,),
        )
    )
    result = snapshot.execution_evaluations[0]
    assert snapshot.status == ExecutionStatus.BLOCKED
    assert result.status == ExecutionStatus.BLOCKED
    assert result.filled_quantity == 0
    assert result.arrival_mid_price is None
    assert result.used_market_slice_ids == ()


def test_maker_reprice_is_bounded_and_idempotent() -> None:
    item = scenario(ExecutionPolicyName.MAKER_REPRICE)
    result = build_snapshot(
        ExecutionIntelligenceRequest(
            request_id="maker-reprice",
            decision_time_utc=BASE,
            execution_scenarios=(item,),
        )
    ).execution_evaluations[0]
    assert result.reprice_count <= item.max_reprices
    assert result.child_order_count == result.reprice_count + 1
    assert all(fill.liquidity_role.value == "maker" for fill in result.fills)


def test_twap_has_bounded_children_and_does_not_double_count_one_slice_capacity() -> None:
    one_slice = market(1, 0, bid_qty=1.0, ask_qty=1.0, volume=2.0)
    item = scenario(ExecutionPolicyName.TWAP).model_copy(
        update={
            "market_path": (one_slice,),
            "quantity": 10.0,
            "twap_slices": 4,
            "twap_interval_seconds": 1.0,
            "participation_cap": 0.5,
        }
    )
    result = build_snapshot(
        ExecutionIntelligenceRequest(
            request_id="twap-capacity",
            decision_time_utc=BASE,
            execution_scenarios=(item,),
        )
    ).execution_evaluations[0]
    assert result.child_order_count == 4
    assert result.filled_quantity <= 2.0 + 1e-12
    assert result.fill_rate < 1


def test_contract_blocks_crossed_book_and_non_utc() -> None:
    with pytest.raises(ValueError, match="crossed_or_locked_top_of_book"):
        market(1, 0, bid=100.2, ask=100.2)
    with pytest.raises(ValueError, match="timestamp_must_be_timezone_aware"):
        MarketSlice(
            slice_id="bad-time",
            source_id="fixture",
            symbol="BTCUSDT",
            event_time_utc=datetime(2026, 8, 28, 18, 0),
            available_at_utc=BASE,
            best_bid=100,
            best_ask=101,
            bid_quantity=1,
            ask_quantity=1,
            last_price=100.5,
            traded_volume=1,
            source_hash=HASH_A,
        )


def test_w8_package_has_no_operational_execution_imports_or_network_clients() -> None:
    package_root = Path("smartcrypto/research/execution_intelligence")
    text = "\n".join(path.read_text(encoding="utf-8") for path in package_root.glob("*.py"))
    forbidden = (
        "smartcrypto.execution.order_manager",
        "smartcrypto.risk.risk_manager",
        "ccxt",
        "requests.",
        "httpx",
        "aiohttp",
        "create_order",
        "cancel_order",
    )
    assert all(token not in text for token in forbidden)


def test_request_rejects_execution_scenario_with_different_decision_clock() -> None:
    item = scenario(ExecutionPolicyName.SINGLE_LIMIT).model_copy(
        update={"decision_time_utc": BASE + timedelta(seconds=1)}
    )
    with pytest.raises(ValueError, match="execution_scenario_decision_time_mismatch"):
        ExecutionIntelligenceRequest(
            request_id="decision-clock-mismatch",
            decision_time_utc=BASE,
            execution_scenarios=(item,),
        )


def test_simulated_api_timeout_fails_closed_without_fill() -> None:
    item = scenario(ExecutionPolicyName.AGGRESSIVE_LIMIT).model_copy(
        update={"simulate_api_timeout": True}
    )
    snapshot = build_snapshot(
        ExecutionIntelligenceRequest(
            request_id="simulated-api-timeout",
            decision_time_utc=BASE,
            execution_scenarios=(item,),
        )
    )
    result = snapshot.execution_evaluations[0]
    assert snapshot.status == ExecutionStatus.BLOCKED
    assert result.status == ExecutionStatus.BLOCKED
    assert result.reason == "simulated_api_timeout_before_acknowledgement"
    assert result.filled_quantity == 0
    assert result.fills == ()
    assert result.used_market_slice_ids == ()
    assert result.timed_out is True
