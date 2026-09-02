from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from smartcrypto.research.portfolio_intelligence import (
    CandidateEVEstimate,
    CandidateOpportunity,
    OpenPositionOpportunity,
    RemainingEVEstimate,
    ReplacementInput,
    ResearchAction,
    RiskPenaltyEstimate,
    TransitionCostEstimate,
    evaluate_replacement,
)

T = datetime(2026, 8, 28, 16, 30, tzinfo=timezone.utc)
H = "b" * 64


def _candidate() -> CandidateOpportunity:
    return CandidateOpportunity(
        candidate_id="cand-1",
        symbol="ETHUSDT",
        side="short",
        strategy_id="breakout-v1",
        ensemble_decision_id="ensemble-1",
        research_action=ResearchAction.PROCEED_RESEARCH,
        observed_at_utc=T - timedelta(seconds=20),
        available_at_utc=T - timedelta(seconds=10),
        candidate_ev=CandidateEVEstimate(
            estimate_id="cev-1",
            value_usdt=10.0,
            semantics="EXPECTED_NET_PNL_USDT_EX_REPLACEMENT_COSTS",
            generated_at_utc=T - timedelta(seconds=15),
            available_at_utc=T - timedelta(seconds=10),
            confidence=0.8,
            source_hash=H,
        ),
        capital_required_usdt=100.0,
        expected_holding_seconds=3600.0,
        alpha_age_seconds=60.0,
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
            estimate_id="rev-1",
            value_usdt=4.0,
            semantics="EXPECTED_REMAINING_NET_PNL_USDT",
            generated_at_utc=T - timedelta(seconds=15),
            available_at_utc=T - timedelta(seconds=10),
            confidence=0.7,
            source_hash=H,
        ),
        source_hash=H,
    )


def _replacement_input(*, available_at=T - timedelta(seconds=5)) -> ReplacementInput:
    return ReplacementInput(
        candidate_id="cand-1",
        position_id="pos-1",
        transition_cost=TransitionCostEstimate(
            candidate_id="cand-1",
            position_id="pos-1",
            exit_cost_usdt=0.5,
            entry_cost_usdt=0.75,
            churn_cost_usdt=0.25,
            generated_at_utc=T - timedelta(seconds=8),
            available_at_utc=available_at,
            source_hash=H,
        ),
        risk_penalty=RiskPenaltyEstimate(
            candidate_id="cand-1",
            position_id="pos-1",
            value_usdt=1.0,
            generated_at_utc=T - timedelta(seconds=8),
            available_at_utc=available_at,
            source_hash=H,
        ),
    )


def test_replacement_delta_uses_all_costs_and_never_authorizes() -> None:
    result = evaluate_replacement(
        candidate=_candidate(),
        position=_position(),
        replacement_input=_replacement_input(),
        decision_time_utc=T,
    )
    assert result.status.value == "EVALUABLE"
    assert result.switching_cost_usdt == pytest.approx(1.5)
    assert result.risk_penalty_usdt == pytest.approx(1.0)
    assert result.replacement_delta_usdt == pytest.approx(10.0 - 4.0 - 1.5 - 1.0)
    assert result.would_replace_shadow is True
    assert result.replacement_authorized is False
    assert result.replacement_executed is False


def test_future_cost_is_invalid_point_in_time() -> None:
    result = evaluate_replacement(
        candidate=_candidate(),
        position=_position(),
        replacement_input=_replacement_input(available_at=T + timedelta(seconds=1)),
        decision_time_utc=T,
    )
    assert result.status.value == "INVALID_POINT_IN_TIME"
    assert result.would_replace_shadow is False


def test_future_outcome_fields_are_rejected_by_contract() -> None:
    payload = _candidate().model_dump(mode="python")
    payload["close_time_utc"] = T + timedelta(hours=1)
    payload["realized_pnl_usdt"] = 99.0
    with pytest.raises(ValidationError):
        CandidateOpportunity.model_validate(payload)
