from datetime import datetime, timedelta, timezone

from smartcrypto.research.portfolio_intelligence import (
    CandidateEVEstimate,
    CandidateOpportunity,
    OpenPositionOpportunity,
    OpportunityBookRequest,
    RemainingEVEstimate,
    ReplacementInput,
    ResearchAction,
    RiskPenaltyEstimate,
    TransitionCostEstimate,
    build_opportunity_book,
)

T = datetime(2026, 8, 28, 16, 30, tzinfo=timezone.utc)
H = "e" * 64


def _candidate(candidate_id: str, action: ResearchAction, ev: float, *, future: bool = False) -> CandidateOpportunity:
    available = T + timedelta(seconds=1) if future else T - timedelta(seconds=10)
    return CandidateOpportunity(
        candidate_id=candidate_id,
        symbol="ETHUSDT" if candidate_id != "cand-3" else "SOLUSDT",
        side="long",
        strategy_id="trend-v1",
        ensemble_decision_id=f"ensemble-{candidate_id}",
        research_action=action,
        observed_at_utc=T - timedelta(seconds=20),
        available_at_utc=available,
        candidate_ev=CandidateEVEstimate(
            estimate_id=f"ev-{candidate_id}",
            value_usdt=ev,
            semantics="EXPECTED_NET_PNL_USDT_EX_REPLACEMENT_COSTS",
            generated_at_utc=T - timedelta(seconds=15),
            available_at_utc=available,
            confidence=0.8,
            source_hash=H,
        ),
        capital_required_usdt=100.0,
        expected_holding_seconds=3600.0,
        alpha_age_seconds=120.0,
        source_hash=H,
    )


def _position() -> OpenPositionOpportunity:
    return OpenPositionOpportunity(
        position_id="pos-1",
        symbol="BTCUSDT",
        side="long",
        strategy_id="trend-v1",
        opened_at_utc=T - timedelta(hours=2),
        observed_at_utc=T - timedelta(seconds=20),
        available_at_utc=T - timedelta(seconds=10),
        capital_locked_usdt=100.0,
        position_age_seconds=7200.0,
        remaining_ev=RemainingEVEstimate(
            estimate_id="remaining-1",
            value_usdt=2.0,
            semantics="EXPECTED_REMAINING_NET_PNL_USDT",
            generated_at_utc=T - timedelta(seconds=15),
            available_at_utc=T - timedelta(seconds=10),
            confidence=0.7,
            source_hash=H,
        ),
        source_hash=H,
    )


def _replacement() -> ReplacementInput:
    return ReplacementInput(
        candidate_id="cand-1",
        position_id="pos-1",
        transition_cost=TransitionCostEstimate(
            candidate_id="cand-1",
            position_id="pos-1",
            exit_cost_usdt=0.2,
            entry_cost_usdt=0.3,
            churn_cost_usdt=0.5,
            generated_at_utc=T - timedelta(seconds=8),
            available_at_utc=T - timedelta(seconds=5),
            source_hash=H,
        ),
        risk_penalty=RiskPenaltyEstimate(
            candidate_id="cand-1",
            position_id="pos-1",
            value_usdt=1.0,
            generated_at_utc=T - timedelta(seconds=8),
            available_at_utc=T - timedelta(seconds=5),
            source_hash=H,
        ),
    )


def test_book_preserves_abstain_and_excludes_future_evidence() -> None:
    request = OpportunityBookRequest(
        request_id="book-request-1",
        decision_time_utc=T,
        candidates=(
            _candidate("cand-1", ResearchAction.PROCEED_RESEARCH, 8.0),
            _candidate("cand-2", ResearchAction.ABSTAIN, 20.0),
            _candidate("cand-3", ResearchAction.PROCEED_RESEARCH, 30.0, future=True),
        ),
        open_positions=(_position(),),
        replacement_inputs=(_replacement(),),
    )
    book = build_opportunity_book(request)
    assert book.status.value == "PARTIAL"
    assert book.valid_candidate_count == 1
    assert book.abstained_candidate_count == 1
    assert book.invalid_candidate_count == 1
    cand3 = next(item for item in book.candidates if item.candidate_id == "cand-3")
    assert cand3.point_in_time_valid is False
    assert "candidate_available_after_decision" in cand3.point_in_time_errors
    cand2 = next(item for item in book.candidates if item.candidate_id == "cand-2")
    assert cand2.research_action is ResearchAction.ABSTAIN


def test_book_id_is_deterministic_and_replacement_economics_are_lineaged() -> None:
    request = OpportunityBookRequest(
        request_id="book-request-2",
        decision_time_utc=T,
        candidates=(_candidate("cand-1", ResearchAction.PROCEED_RESEARCH, 8.0),),
        open_positions=(_position(),),
        replacement_inputs=(_replacement(),),
    )
    one = build_opportunity_book(request)
    two = build_opportunity_book(request)
    assert one.book_id == two.book_id
    assert one.created_at_utc == T
    replacement = one.replacements[0]
    assert replacement.replacement_delta_usdt == 4.0
    assert replacement.would_replace_shadow is True
    assert one.sends_orders is False
    assert one.operational_authority is False


def test_duplicate_candidate_lineage_is_rejected() -> None:
    candidate = _candidate("cand-1", ResearchAction.PROCEED_RESEARCH, 8.0)
    try:
        OpportunityBookRequest(
            request_id="book-request-duplicate",
            decision_time_utc=T,
            candidates=(candidate, candidate),
        )
    except ValueError as exc:
        assert "duplicate_candidate_id" in str(exc)
    else:
        raise AssertionError("duplicate candidate lineage must be rejected")
