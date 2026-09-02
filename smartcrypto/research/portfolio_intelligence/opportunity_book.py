"""Opportunity Book V2 built from causal W4-approved candidate evidence."""

from __future__ import annotations

from .capital_hours import required_capital_hours
from .contracts import (
    OpportunityBookRequest,
    OpportunityBookSnapshot,
    OpportunityCandidateView,
    OpportunityStatus,
    ResearchAction,
    stable_id,
)
from .remaining_edge import build_open_position_view
from .replacement_policy import evaluate_replacement


def build_opportunity_book(
    request: OpportunityBookRequest,
    *,
    min_replacement_delta_usdt: float = 0.0,
) -> OpportunityBookSnapshot:
    candidate_views: list[OpportunityCandidateView] = []
    valid_candidates = 0
    abstained_candidates = 0
    invalid_candidates = 0
    for candidate in request.candidates:
        errors = candidate.point_in_time_errors(request.decision_time_utc)
        valid = not errors
        if not valid:
            invalid_candidates += 1
        elif candidate.research_action is ResearchAction.ABSTAIN:
            abstained_candidates += 1
        else:
            valid_candidates += 1
        capital_hours = required_capital_hours(candidate)
        candidate_views.append(
            OpportunityCandidateView(
                candidate_id=candidate.candidate_id,
                symbol=candidate.symbol,
                side=candidate.side,
                strategy_id=candidate.strategy_id,
                research_action=candidate.research_action,
                candidate_ev_usdt=candidate.candidate_ev.value_usdt,
                capital_required_usdt=candidate.capital_required_usdt,
                expected_holding_seconds=candidate.expected_holding_seconds,
                required_capital_hours=capital_hours,
                ev_per_capital_hour=candidate.candidate_ev.value_usdt / capital_hours,
                alpha_age_seconds=candidate.alpha_age_seconds,
                point_in_time_valid=valid,
                point_in_time_errors=errors,
                source_hash=candidate.source_hash,
            )
        )
    candidate_views.sort(
        key=lambda item: (
            not item.point_in_time_valid,
            item.research_action is ResearchAction.ABSTAIN,
            -item.candidate_ev_usdt,
            item.candidate_id,
        )
    )

    position_views = tuple(
        build_open_position_view(position, request.decision_time_utc)
        for position in request.open_positions
    )
    replacement_input_by_pair = {
        (item.candidate_id, item.position_id): item for item in request.replacement_inputs
    }
    replacements = []
    for candidate in request.candidates:
        for position in request.open_positions:
            replacements.append(
                evaluate_replacement(
                    candidate=candidate,
                    position=position,
                    replacement_input=replacement_input_by_pair.get(
                        (candidate.candidate_id, position.position_id)
                    ),
                    decision_time_utc=request.decision_time_utc,
                    min_replacement_delta_usdt=min_replacement_delta_usdt,
                )
            )
    replacements.sort(key=lambda item: (item.candidate_id, item.position_id))

    invalid_positions = sum(not position.point_in_time_valid for position in position_views)
    status = OpportunityStatus.READY
    reason = "opportunity_book_ready"
    if not request.candidates:
        status = OpportunityStatus.BLOCKED
        reason = "no_candidates"
    elif invalid_candidates or invalid_positions:
        status = OpportunityStatus.PARTIAL
        reason = "invalid_point_in_time_inputs_excluded"
    elif valid_candidates == 0:
        status = OpportunityStatus.PARTIAL
        reason = "no_non_abstained_valid_candidates"

    canonical = {
        "request_id": request.request_id,
        "decision_time_utc": request.decision_time_utc,
        "candidates": [item.model_dump(mode="json") for item in candidate_views],
        "open_positions": [item.model_dump(mode="json") for item in position_views],
        "replacements": [item.model_dump(mode="json") for item in replacements],
    }
    return OpportunityBookSnapshot(
        book_id=stable_id("opportunity-book", canonical),
        request_id=request.request_id,
        decision_time_utc=request.decision_time_utc,
        created_at_utc=request.decision_time_utc,
        status=status,
        reason=reason,
        candidates=tuple(candidate_views),
        open_positions=position_views,
        replacements=tuple(replacements),
        candidate_count=len(candidate_views),
        valid_candidate_count=valid_candidates,
        abstained_candidate_count=abstained_candidates,
        invalid_candidate_count=invalid_candidates,
        capital_locked_usdt=sum(item.capital_locked_usdt for item in position_views),
        capital_hours_total=sum(item.capital_hours_consumed for item in position_views),
        point_in_time_valid_for_used_inputs=(invalid_candidates == 0 and invalid_positions == 0),
    )
