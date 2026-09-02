from datetime import datetime, timedelta, timezone

from smartcrypto.research.portfolio_intelligence import (
    AlphaDefinition,
    CandidateEVEstimate,
    CandidateOpportunity,
    CorrelationObservation,
    OpenPositionOpportunity,
    OpportunityBookRequest,
    PortfolioAllocatorConfig,
    PortfolioAllocatorRequest,
    RemainingEVEstimate,
    ReplacementInput,
    ResearchAction,
    RiskPenaltyEstimate,
    TransitionCostEstimate,
    allocate_shadow_portfolio,
    build_alpha_registry,
    build_opportunity_book,
)

T = datetime(2026, 8, 28, 16, 30, tzinfo=timezone.utc)
H = "f" * 64


def _candidate(candidate_id: str, symbol: str, ev: float, action=ResearchAction.PROCEED_RESEARCH) -> CandidateOpportunity:
    return CandidateOpportunity(
        candidate_id=candidate_id,
        symbol=symbol,
        side="long",
        strategy_id="trend-v1",
        ensemble_decision_id=f"ens-{candidate_id}",
        research_action=action,
        observed_at_utc=T - timedelta(seconds=20),
        available_at_utc=T - timedelta(seconds=10),
        candidate_ev=CandidateEVEstimate(
            estimate_id=f"ev-{candidate_id}",
            value_usdt=ev,
            semantics="EXPECTED_NET_PNL_USDT_EX_REPLACEMENT_COSTS",
            generated_at_utc=T - timedelta(seconds=15),
            available_at_utc=T - timedelta(seconds=10),
            confidence=0.8,
            source_hash=H,
        ),
        capital_required_usdt=100.0,
        expected_holding_seconds=3600.0,
        alpha_age_seconds=30.0,
        source_hash=H,
    )


def _position() -> OpenPositionOpportunity:
    return OpenPositionOpportunity(
        position_id="pos-btc",
        symbol="BTCUSDT",
        side="long",
        strategy_id="trend-v1",
        opened_at_utc=T - timedelta(hours=2),
        observed_at_utc=T - timedelta(seconds=20),
        available_at_utc=T - timedelta(seconds=10),
        capital_locked_usdt=100.0,
        position_age_seconds=7200.0,
        remaining_ev=RemainingEVEstimate(
            estimate_id="rev-btc",
            value_usdt=2.0,
            semantics="EXPECTED_REMAINING_NET_PNL_USDT",
            generated_at_utc=T - timedelta(seconds=15),
            available_at_utc=T - timedelta(seconds=10),
            confidence=0.7,
            source_hash=H,
        ),
        source_hash=H,
    )


def _replacement(candidate_id: str) -> ReplacementInput:
    return ReplacementInput(
        candidate_id=candidate_id,
        position_id="pos-btc",
        transition_cost=TransitionCostEstimate(
            candidate_id=candidate_id,
            position_id="pos-btc",
            exit_cost_usdt=0.2,
            entry_cost_usdt=0.3,
            churn_cost_usdt=0.5,
            generated_at_utc=T - timedelta(seconds=8),
            available_at_utc=T - timedelta(seconds=5),
            source_hash=H,
        ),
        risk_penalty=RiskPenaltyEstimate(
            candidate_id=candidate_id,
            position_id="pos-btc",
            value_usdt=1.0,
            generated_at_utc=T - timedelta(seconds=8),
            available_at_utc=T - timedelta(seconds=5),
            source_hash=H,
        ),
    )


def _registry():
    return build_alpha_registry(
        [
            AlphaDefinition(
                strategy_id="trend-v1",
                sleeve="directional",
                version="v1",
                feature_set_hash=H,
                hypothesis="Causal directional trend edge in validated regimes.",
                supported_regimes=("TREND",),
            )
        ],
        created_at_utc=T,
    )


def _corr(a: str, b: str, value: float) -> CorrelationObservation:
    return CorrelationObservation(
        symbol_a=a,
        symbol_b=b,
        correlation=value,
        sample_count=100,
        generated_at_utc=T - timedelta(minutes=1),
        available_at_utc=T - timedelta(seconds=30),
        source_hash=H,
    )


def test_w4_abstain_is_never_selected_even_with_high_ev() -> None:
    book = build_opportunity_book(
        OpportunityBookRequest(
            request_id="req-abstain",
            decision_time_utc=T,
            candidates=(
                _candidate("cand-ok", "ETHUSDT", 5.0),
                _candidate("cand-abstain", "SOLUSDT", 100.0, ResearchAction.ABSTAIN),
            ),
        )
    )
    allocation = allocate_shadow_portfolio(
        PortfolioAllocatorRequest(
            request_id="alloc-abstain",
            decision_time_utc=T,
            opportunity_book=book,
            alpha_registry=_registry(),
        ),
        PortfolioAllocatorConfig(
            top_n=1,
            max_positions=2,
            shadow_capital_budget_usdt=1000.0,
            max_symbol_concentration_fraction=0.6,
            missing_correlation_policy="ALLOW",
        ),
    )
    assert [item.candidate_id for item in allocation.selected] == ["cand-ok"]
    abstain = next(item for item in allocation.rejected if item.candidate_id == "cand-abstain")
    assert "w4_abstain" in abstain.reasons
    assert allocation.sends_orders is False
    assert allocation.riskmanager_final_authority is True


def test_missing_correlation_blocks_direct_selection_by_default() -> None:
    book = build_opportunity_book(
        OpportunityBookRequest(
            request_id="req-corr",
            decision_time_utc=T,
            candidates=(_candidate("cand-eth", "ETHUSDT", 5.0),),
            open_positions=(_position(),),
        )
    )
    allocation = allocate_shadow_portfolio(
        PortfolioAllocatorRequest(
            request_id="alloc-corr",
            decision_time_utc=T,
            opportunity_book=book,
            alpha_registry=_registry(),
        ),
        PortfolioAllocatorConfig(max_positions=2, top_n=1),
    )
    assert allocation.selected_count == 0
    assert allocation.rejected_count == 1


def test_positive_replacement_delta_can_replace_when_capacity_full() -> None:
    candidate = _candidate("cand-eth", "ETHUSDT", 9.0)
    book = build_opportunity_book(
        OpportunityBookRequest(
            request_id="req-replace",
            decision_time_utc=T,
            candidates=(candidate,),
            open_positions=(_position(),),
            replacement_inputs=(_replacement("cand-eth"),),
        )
    )
    request = PortfolioAllocatorRequest(
        request_id="alloc-replace",
        decision_time_utc=T,
        opportunity_book=book,
        correlations=(_corr("BTCUSDT", "ETHUSDT", 0.2),),
        alpha_registry=_registry(),
    )
    config = PortfolioAllocatorConfig(
        top_n=1,
        max_positions=1,
        max_positions_per_symbol=1,
        shadow_capital_budget_usdt=1000.0,
        max_symbol_concentration_fraction=0.6,
        max_pairwise_correlation=0.8,
    )
    one = allocate_shadow_portfolio(request, config)
    two = allocate_shadow_portfolio(request, config)
    assert one.allocation_id == two.allocation_id
    assert one.selected_count == 1
    decision = one.selected[0]
    assert decision.action.value == "REPLACE_SHADOW"
    assert decision.replacement_position_id == "pos-btc"
    assert decision.replacement_delta_usdt == 5.0
    assert decision.operational_authority is False


def test_high_correlation_blocks_selection() -> None:
    book = build_opportunity_book(
        OpportunityBookRequest(
            request_id="req-highcorr",
            decision_time_utc=T,
            candidates=(_candidate("cand-eth", "ETHUSDT", 5.0),),
            open_positions=(_position(),),
        )
    )
    allocation = allocate_shadow_portfolio(
        PortfolioAllocatorRequest(
            request_id="alloc-highcorr",
            decision_time_utc=T,
            opportunity_book=book,
            correlations=(_corr("BTCUSDT", "ETHUSDT", 0.95),),
            alpha_registry=_registry(),
        ),
        PortfolioAllocatorConfig(max_positions=2, top_n=1, max_pairwise_correlation=0.8),
    )
    assert allocation.selected_count == 0


def test_unregistered_strategy_is_blocked() -> None:
    candidate = _candidate("cand-eth", "ETHUSDT", 5.0)
    payload = candidate.model_dump(mode="python")
    payload["strategy_id"] = "unknown-v1"
    candidate = CandidateOpportunity.model_validate(payload)
    book = build_opportunity_book(
        OpportunityBookRequest(
            request_id="req-registry",
            decision_time_utc=T,
            candidates=(candidate,),
        )
    )
    allocation = allocate_shadow_portfolio(
        PortfolioAllocatorRequest(
            request_id="alloc-registry",
            decision_time_utc=T,
            opportunity_book=book,
            alpha_registry=_registry(),
        ),
        PortfolioAllocatorConfig(top_n=1, max_positions=1),
    )
    assert allocation.selected_count == 0
    assert "strategy_not_registered" in allocation.rejected[0].reasons


def test_invalid_open_position_blocks_allocator_fail_closed() -> None:
    position = _position()
    payload = position.model_dump(mode="python")
    payload["remaining_ev"] = RemainingEVEstimate(
        estimate_id="rev-future",
        value_usdt=2.0,
        semantics="EXPECTED_REMAINING_NET_PNL_USDT",
        generated_at_utc=T + timedelta(seconds=1),
        available_at_utc=T + timedelta(seconds=2),
        confidence=0.7,
        source_hash=H,
    )
    position = OpenPositionOpportunity.model_validate(payload)
    book = build_opportunity_book(
        OpportunityBookRequest(
            request_id="req-invalid-position",
            decision_time_utc=T,
            candidates=(_candidate("cand-eth", "ETHUSDT", 8.0),),
            open_positions=(position,),
        )
    )
    allocation = allocate_shadow_portfolio(
        PortfolioAllocatorRequest(
            request_id="alloc-invalid-position",
            decision_time_utc=T,
            opportunity_book=book,
            alpha_registry=_registry(),
        ),
        PortfolioAllocatorConfig(top_n=1, max_positions=2, missing_correlation_policy="ALLOW"),
    )
    assert allocation.status.value == "BLOCKED"
    assert allocation.selected_count == 0
    assert allocation.reason == "invalid_open_position_point_in_time_fail_closed"
