"""Deterministic Top-N shadow allocator with capacity/correlation/concentration gates."""

from __future__ import annotations

from dataclasses import dataclass

from .alpha_registry import registered_strategy_ids
from .capacity import build_virtual_state, capacity_reasons
from .contracts import (
    AllocationAction,
    AllocationDecision,
    MissingCorrelationPolicy,
    OpportunityCandidateView,
    OpportunityStatus,
    PortfolioAllocationSnapshot,
    PortfolioAllocatorConfig,
    PortfolioAllocatorRequest,
    ReplacementEvaluation,
    ReplacementStatus,
    ResearchAction,
    stable_id,
)
from .correlation import build_correlation_lookup, correlation_for


@dataclass(frozen=True)
class _VirtualPosition:
    position_id: str
    symbol: str
    capital_usdt: float
    existing: bool


def allocate_shadow_portfolio(
    request: PortfolioAllocatorRequest,
    config: PortfolioAllocatorConfig,
) -> PortfolioAllocationSnapshot:
    lookup = build_correlation_lookup(request.correlations, request.decision_time_utc)
    registered = registered_strategy_ids(request.alpha_registry)
    replacement_lookup = _replacement_lookup(request.opportunity_book.replacements)
    virtual_positions = [
        _VirtualPosition(
            position_id=item.position_id,
            symbol=item.symbol,
            capital_usdt=item.capital_locked_usdt,
            existing=True,
        )
        for item in request.opportunity_book.open_positions
        if item.point_in_time_valid
    ]
    existing_capital = sum(item.capital_usdt for item in virtual_positions)

    candidates = list(request.opportunity_book.candidates)
    candidates.sort(key=lambda item: _candidate_sort_key(item, config))
    selected: list[AllocationDecision] = []
    rejected: list[AllocationDecision] = []

    invalid_open_positions = [
        item.position_id for item in request.opportunity_book.open_positions if not item.point_in_time_valid
    ]
    if invalid_open_positions:
        for rank, candidate in enumerate(candidates, start=1):
            rejected.append(
                _skip(
                    candidate,
                    rank,
                    "invalid_open_position_point_in_time_fail_closed",
                    config=config,
                )
            )
        canonical = {
            "request_id": request.request_id,
            "decision_time_utc": request.decision_time_utc,
            "selected": [],
            "rejected": [item.model_dump(mode="json") for item in rejected],
            "config": config.model_dump(mode="json"),
            "invalid_open_positions": sorted(invalid_open_positions),
        }
        return PortfolioAllocationSnapshot(
            allocation_id=stable_id("portfolio-allocation", canonical),
            request_id=request.request_id,
            decision_time_utc=request.decision_time_utc,
            created_at_utc=request.decision_time_utc,
            status=OpportunityStatus.BLOCKED,
            reason="invalid_open_position_point_in_time_fail_closed",
            selected=(),
            rejected=tuple(rejected),
            selected_count=0,
            rejected_count=len(rejected),
            existing_position_count=len(request.opportunity_book.open_positions),
            projected_position_count=len(request.opportunity_book.open_positions),
            existing_capital_usdt=sum(
                item.capital_locked_usdt for item in request.opportunity_book.open_positions
            ),
            selected_capital_usdt=0.0,
            projected_capital_usdt=sum(
                item.capital_locked_usdt for item in request.opportunity_book.open_positions
            ),
            shadow_capital_budget_usdt=config.shadow_capital_budget_usdt,
            top_n=config.top_n,
            correlation_constraints_applied=True,
            concentration_constraints_applied=True,
            capacity_constraints_applied=True,
            replacement_evaluations_used=False,
        )

    for rank, candidate in enumerate(candidates, start=1):
        base_reasons = _base_candidate_reasons(candidate, config, registered)
        if base_reasons:
            rejected.append(_skip(candidate, rank, *base_reasons, config=config))
            continue
        if len(selected) >= config.top_n:
            rejected.append(_skip(candidate, rank, "top_n_limit_reached", config=config))
            continue

        direct = _evaluate_direct_selection(
            candidate=candidate,
            rank=rank,
            virtual_positions=virtual_positions,
            correlation_lookup=lookup,
            config=config,
        )
        if direct is not None:
            selected.append(direct)
            virtual_positions.append(
                _VirtualPosition(
                    position_id=f"candidate:{candidate.candidate_id}",
                    symbol=candidate.symbol,
                    capital_usdt=candidate.capital_required_usdt,
                    existing=False,
                )
            )
            continue

        replacement = _best_replacement(
            candidate=candidate,
            rank=rank,
            virtual_positions=virtual_positions,
            replacement_lookup=replacement_lookup,
            correlation_lookup=lookup,
            config=config,
        )
        if replacement is not None:
            selected.append(replacement)
            assert replacement.replacement_position_id is not None
            virtual_positions = [
                item
                for item in virtual_positions
                if item.position_id != replacement.replacement_position_id
            ]
            virtual_positions.append(
                _VirtualPosition(
                    position_id=f"candidate:{candidate.candidate_id}",
                    symbol=candidate.symbol,
                    capital_usdt=candidate.capital_required_usdt,
                    existing=False,
                )
            )
            continue

        rejected.append(_skip(candidate, rank, "capacity_or_portfolio_constraints", config=config))

    selected_capital = sum(item.allocated_capital_usdt for item in selected)
    projected_capital = sum(item.capital_usdt for item in virtual_positions)
    status = OpportunityStatus.READY if selected else OpportunityStatus.PARTIAL
    reason = "shadow_allocation_ready" if selected else "no_candidate_selected"
    canonical = {
        "request_id": request.request_id,
        "decision_time_utc": request.decision_time_utc,
        "selected": [item.model_dump(mode="json") for item in selected],
        "rejected": [item.model_dump(mode="json") for item in rejected],
        "config": config.model_dump(mode="json"),
    }
    return PortfolioAllocationSnapshot(
        allocation_id=stable_id("portfolio-allocation", canonical),
        request_id=request.request_id,
        decision_time_utc=request.decision_time_utc,
        created_at_utc=request.decision_time_utc,
        status=status,
        reason=reason,
        selected=tuple(selected),
        rejected=tuple(rejected),
        selected_count=len(selected),
        rejected_count=len(rejected),
        existing_position_count=len(request.opportunity_book.open_positions),
        projected_position_count=len(virtual_positions),
        existing_capital_usdt=existing_capital,
        selected_capital_usdt=selected_capital,
        projected_capital_usdt=projected_capital,
        shadow_capital_budget_usdt=config.shadow_capital_budget_usdt,
        top_n=config.top_n,
        correlation_constraints_applied=True,
        concentration_constraints_applied=True,
        capacity_constraints_applied=True,
        replacement_evaluations_used=bool(request.opportunity_book.replacements),
    )


def _candidate_sort_key(
    candidate: OpportunityCandidateView,
    config: PortfolioAllocatorConfig,
) -> tuple[bool, bool, float, str]:
    metric = (
        candidate.candidate_ev_usdt
        if config.ranking_metric == "candidate_ev"
        else candidate.ev_per_capital_hour
    )
    return (
        not candidate.point_in_time_valid,
        candidate.research_action is ResearchAction.ABSTAIN,
        -metric,
        candidate.candidate_id,
    )


def _base_candidate_reasons(
    candidate: OpportunityCandidateView,
    config: PortfolioAllocatorConfig,
    registered: frozenset[str],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not candidate.point_in_time_valid:
        reasons.append("invalid_point_in_time")
    if candidate.research_action is ResearchAction.ABSTAIN:
        reasons.append("w4_abstain")
    if candidate.research_action is ResearchAction.DEPRIORITIZE_RESEARCH and not config.allow_deprioritized:
        reasons.append("w4_deprioritized")
    if candidate.candidate_ev_usdt <= config.min_candidate_ev_usdt:
        reasons.append("candidate_ev_below_threshold")
    if config.require_registered_alpha and candidate.strategy_id not in registered:
        reasons.append("strategy_not_registered")
    return tuple(reasons)


def _evaluate_direct_selection(
    *,
    candidate: OpportunityCandidateView,
    rank: int,
    virtual_positions: list[_VirtualPosition],
    correlation_lookup: dict[tuple[str, str], float],
    config: PortfolioAllocatorConfig,
) -> AllocationDecision | None:
    state = build_virtual_state([(item.symbol, item.capital_usdt) for item in virtual_positions])
    reasons = capacity_reasons(
        state=state,
        candidate_symbol=candidate.symbol,
        candidate_capital_usdt=candidate.capital_required_usdt,
        config=config,
    )
    if reasons:
        return None
    corr_ok, max_corr, corr_reason = _correlation_ok(
        candidate.symbol,
        [item.symbol for item in virtual_positions],
        correlation_lookup,
        config,
    )
    if not corr_ok:
        return None
    projected_symbol = state.capital_by_symbol.get(candidate.symbol, 0.0) + candidate.capital_required_usdt
    concentration = projected_symbol / config.shadow_capital_budget_usdt
    objective = _objective(candidate, config)
    return AllocationDecision(
        decision_id=stable_id(
            "allocation-decision",
            {"candidate_id": candidate.candidate_id, "rank": rank, "action": "SELECT_SHADOW"},
        ),
        rank=rank,
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        strategy_id=candidate.strategy_id,
        action=AllocationAction.SELECT_SHADOW,
        allocated_capital_usdt=candidate.capital_required_usdt,
        objective_score=objective,
        pairwise_max_abs_correlation=max_corr,
        symbol_concentration_fraction=concentration,
        reasons=("passed_shadow_portfolio_constraints", corr_reason),
    )


def _best_replacement(
    *,
    candidate: OpportunityCandidateView,
    rank: int,
    virtual_positions: list[_VirtualPosition],
    replacement_lookup: dict[tuple[str, str], ReplacementEvaluation],
    correlation_lookup: dict[tuple[str, str], float],
    config: PortfolioAllocatorConfig,
) -> AllocationDecision | None:
    options: list[tuple[float, _VirtualPosition, ReplacementEvaluation, float | None, float]] = []
    for position in virtual_positions:
        if not position.existing:
            continue
        evaluation = replacement_lookup.get((candidate.candidate_id, position.position_id))
        if (
            evaluation is None
            or evaluation.status is not ReplacementStatus.EVALUABLE
            or not evaluation.would_replace_shadow
            or evaluation.replacement_delta_usdt is None
            or evaluation.replacement_delta_usdt <= config.min_replacement_delta_usdt
        ):
            continue
        remaining_positions = [item for item in virtual_positions if item.position_id != position.position_id]
        state = build_virtual_state([(item.symbol, item.capital_usdt) for item in remaining_positions])
        if capacity_reasons(
            state=state,
            candidate_symbol=candidate.symbol,
            candidate_capital_usdt=candidate.capital_required_usdt,
            config=config,
        ):
            continue
        corr_ok, max_corr, _ = _correlation_ok(
            candidate.symbol,
            [item.symbol for item in remaining_positions],
            correlation_lookup,
            config,
        )
        if not corr_ok:
            continue
        projected_symbol = state.capital_by_symbol.get(candidate.symbol, 0.0) + candidate.capital_required_usdt
        concentration = projected_symbol / config.shadow_capital_budget_usdt
        options.append((evaluation.replacement_delta_usdt, position, evaluation, max_corr, concentration))
    if not options:
        return None
    options.sort(key=lambda item: (-item[0], item[1].position_id))
    delta, position, evaluation, max_corr, concentration = options[0]
    return AllocationDecision(
        decision_id=stable_id(
            "allocation-decision",
            {
                "candidate_id": candidate.candidate_id,
                "rank": rank,
                "action": "REPLACE_SHADOW",
                "position_id": position.position_id,
            },
        ),
        rank=rank,
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        strategy_id=candidate.strategy_id,
        action=AllocationAction.REPLACE_SHADOW,
        allocated_capital_usdt=candidate.capital_required_usdt,
        objective_score=_objective(candidate, config),
        replacement_position_id=position.position_id,
        replacement_delta_usdt=delta,
        pairwise_max_abs_correlation=max_corr,
        symbol_concentration_fraction=concentration,
        reasons=("positive_replacement_delta_shadow_only", evaluation.reason),
    )


def _replacement_lookup(
    evaluations: tuple[ReplacementEvaluation, ...],
) -> dict[tuple[str, str], ReplacementEvaluation]:
    return {(item.candidate_id, item.position_id): item for item in evaluations}


def _correlation_ok(
    candidate_symbol: str,
    portfolio_symbols: list[str],
    lookup: dict[tuple[str, str], float],
    config: PortfolioAllocatorConfig,
) -> tuple[bool, float | None, str]:
    if not portfolio_symbols:
        return True, None, "no_existing_correlation_constraint"
    values: list[float] = []
    for symbol in portfolio_symbols:
        correlation = correlation_for(candidate_symbol, symbol, lookup)
        if correlation is None:
            if config.missing_correlation_policy is MissingCorrelationPolicy.BLOCK:
                return False, None, "missing_correlation_blocked"
            continue
        values.append(abs(correlation))
        if abs(correlation) > config.max_pairwise_correlation + 1e-12:
            return False, max(values), "pairwise_correlation_exceeded"
    return True, (max(values) if values else None), "pairwise_correlation_ok"


def _objective(candidate: OpportunityCandidateView, config: PortfolioAllocatorConfig) -> float:
    return (
        candidate.candidate_ev_usdt
        if config.ranking_metric == "candidate_ev"
        else candidate.ev_per_capital_hour
    )


def _skip(
    candidate: OpportunityCandidateView,
    rank: int,
    *reasons: str,
    config: PortfolioAllocatorConfig,
) -> AllocationDecision:
    return AllocationDecision(
        decision_id=stable_id(
            "allocation-decision",
            {"candidate_id": candidate.candidate_id, "rank": rank, "action": "SKIP", "reasons": reasons},
        ),
        rank=rank,
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        strategy_id=candidate.strategy_id,
        action=AllocationAction.SKIP,
        allocated_capital_usdt=0.0,
        objective_score=_objective(candidate, config),
        reasons=tuple(reasons),
    )
