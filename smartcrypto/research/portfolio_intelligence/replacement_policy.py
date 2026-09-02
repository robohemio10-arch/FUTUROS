"""Deterministic shadow-only replacement economics."""

from __future__ import annotations

from datetime import datetime

from .contracts import (
    CandidateOpportunity,
    OpenPositionOpportunity,
    ReplacementEvaluation,
    ReplacementInput,
    ReplacementStatus,
    stable_id,
)


def evaluate_replacement(
    *,
    candidate: CandidateOpportunity,
    position: OpenPositionOpportunity,
    replacement_input: ReplacementInput | None,
    decision_time_utc: datetime,
    min_replacement_delta_usdt: float = 0.0,
) -> ReplacementEvaluation:
    identity = {
        "candidate_id": candidate.candidate_id,
        "position_id": position.position_id,
        "decision_time_utc": decision_time_utc,
    }
    evaluation_id = stable_id("replacement", identity)
    candidate_errors = candidate.point_in_time_errors(decision_time_utc)
    position_errors = position.point_in_time_errors(decision_time_utc)
    if candidate_errors or position_errors:
        return ReplacementEvaluation(
            evaluation_id=evaluation_id,
            candidate_id=candidate.candidate_id,
            position_id=position.position_id,
            status=ReplacementStatus.INVALID_POINT_IN_TIME,
            would_replace_shadow=False,
            reason="candidate_or_position_invalid_point_in_time",
            point_in_time_valid=False,
        )
    if position.remaining_ev is None:
        return ReplacementEvaluation(
            evaluation_id=evaluation_id,
            candidate_id=candidate.candidate_id,
            position_id=position.position_id,
            status=ReplacementStatus.NOT_EVALUABLE,
            candidate_ev_usdt=candidate.candidate_ev.value_usdt,
            would_replace_shadow=False,
            reason="remaining_ev_missing",
            point_in_time_valid=True,
        )
    if replacement_input is None:
        return ReplacementEvaluation(
            evaluation_id=evaluation_id,
            candidate_id=candidate.candidate_id,
            position_id=position.position_id,
            status=ReplacementStatus.NOT_EVALUABLE,
            candidate_ev_usdt=candidate.candidate_ev.value_usdt,
            remaining_ev_usdt=position.remaining_ev.value_usdt,
            would_replace_shadow=False,
            reason="transition_or_risk_cost_missing",
            point_in_time_valid=True,
        )
    cost_errors = replacement_input.transition_cost.point_in_time_errors(decision_time_utc)
    risk_errors = replacement_input.risk_penalty.point_in_time_errors(decision_time_utc)
    if cost_errors or risk_errors:
        return ReplacementEvaluation(
            evaluation_id=evaluation_id,
            candidate_id=candidate.candidate_id,
            position_id=position.position_id,
            status=ReplacementStatus.INVALID_POINT_IN_TIME,
            candidate_ev_usdt=candidate.candidate_ev.value_usdt,
            remaining_ev_usdt=position.remaining_ev.value_usdt,
            would_replace_shadow=False,
            reason="replacement_cost_invalid_point_in_time",
            point_in_time_valid=False,
        )

    candidate_ev = candidate.candidate_ev.value_usdt
    remaining_ev = position.remaining_ev.value_usdt
    switching_cost = replacement_input.transition_cost.switching_cost_usdt
    risk_penalty = replacement_input.risk_penalty.value_usdt
    replacement_delta = candidate_ev - remaining_ev - switching_cost - risk_penalty
    return ReplacementEvaluation(
        evaluation_id=evaluation_id,
        candidate_id=candidate.candidate_id,
        position_id=position.position_id,
        status=ReplacementStatus.EVALUABLE,
        candidate_ev_usdt=candidate_ev,
        remaining_ev_usdt=remaining_ev,
        switching_cost_usdt=switching_cost,
        risk_penalty_usdt=risk_penalty,
        replacement_delta_usdt=replacement_delta,
        would_replace_shadow=replacement_delta > min_replacement_delta_usdt,
        reason=(
            "positive_net_replacement_delta"
            if replacement_delta > min_replacement_delta_usdt
            else "replacement_delta_not_positive_enough"
        ),
        point_in_time_valid=True,
    )
