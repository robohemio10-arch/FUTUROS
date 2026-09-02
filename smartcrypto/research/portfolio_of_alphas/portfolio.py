"""Deterministic Portfolio of Alphas builder for W6."""

from __future__ import annotations

from smartcrypto.research.portfolio_intelligence.contracts import stable_id

from .contracts import (
    AlphaHealthStatus,
    AlphaPortfolioRequest,
    AlphaPortfolioSnapshot,
    AlphaSleeveSnapshot,
    AlphaStrategyState,
    PortfolioStatus,
)


def build_portfolio_of_alphas(request: AlphaPortfolioRequest) -> AlphaPortfolioSnapshot:
    observations = {item.strategy_id: item for item in request.health_observations}
    sleeve_by_strategy = {
        strategy_id: sleeve.sleeve_id
        for sleeve in request.sleeves
        for strategy_id in sleeve.strategy_ids
    }

    states: list[AlphaStrategyState] = []
    missing_health: list[str] = []
    blocked: list[str] = []
    eligible: list[str] = []

    definitions = sorted(request.alpha_registry.definitions, key=lambda item: item.strategy_id)
    for definition in definitions:
        strategy_id = definition.strategy_id
        sleeve_id = sleeve_by_strategy[strategy_id]
        observation = observations.get(strategy_id)
        if observation is None:
            missing_health.append(strategy_id)
            state = AlphaStrategyState(
                strategy_id=strategy_id,
                sleeve_id=sleeve_id,
                status=AlphaHealthStatus.UNKNOWN,
                quality_score=None,
                health_age_seconds=None,
                health_reason="missing_health_observation",
                point_in_time_valid=True,
                point_in_time_errors=(),
            )
            states.append(state)
            continue

        pit_errors = observation.point_in_time_errors(request.decision_time_utc)
        if pit_errors:
            blocked.append(strategy_id)
            states.append(
                AlphaStrategyState(
                    strategy_id=strategy_id,
                    sleeve_id=sleeve_id,
                    status=AlphaHealthStatus.BLOCKED,
                    quality_score=None,
                    health_age_seconds=None,
                    health_reason="invalid_point_in_time_health_observation",
                    point_in_time_valid=False,
                    point_in_time_errors=pit_errors,
                )
            )
            continue

        age_seconds = max(
            0.0,
            (request.decision_time_utc - observation.available_at_utc).total_seconds(),
        )
        health_status: AlphaHealthStatus = observation.status
        reason = observation.reason
        if observation.is_stale(request.decision_time_utc):
            health_status = AlphaHealthStatus.DEGRADED
            reason = "stale_alpha_health_observation"

        if health_status is AlphaHealthStatus.HEALTHY:
            eligible.append(strategy_id)
        elif health_status in {AlphaHealthStatus.BLOCKED, AlphaHealthStatus.PAUSED_RESEARCH}:
            blocked.append(strategy_id)

        states.append(
            AlphaStrategyState(
                strategy_id=strategy_id,
                sleeve_id=sleeve_id,
                status=health_status,
                quality_score=observation.quality_score,
                health_age_seconds=age_seconds,
                health_reason=reason,
                point_in_time_valid=True,
                point_in_time_errors=(),
            )
        )

    state_by_strategy = {item.strategy_id: item for item in states}
    sleeve_snapshots: list[AlphaSleeveSnapshot] = []
    for sleeve in sorted(request.sleeves, key=lambda item: item.sleeve_id):
        sleeve_states = [state_by_strategy[item] for item in sleeve.strategy_ids]
        sleeve_eligible = tuple(
            sorted(item.strategy_id for item in sleeve_states if item.status is AlphaHealthStatus.HEALTHY)
        )
        sleeve_blocked = tuple(
            sorted(
                item.strategy_id
                for item in sleeve_states
                if item.status in {AlphaHealthStatus.BLOCKED, AlphaHealthStatus.PAUSED_RESEARCH}
            )
        )
        quality_values = [item.quality_score for item in sleeve_states if item.quality_score is not None]
        mean_quality = None if not quality_values else sum(quality_values) / len(quality_values)
        if len(sleeve_blocked) == len(sleeve_states):
            sleeve_status: PortfolioStatus = PortfolioStatus.BLOCKED
        elif len(sleeve_eligible) == len(sleeve_states):
            sleeve_status = PortfolioStatus.READY
        else:
            sleeve_status = PortfolioStatus.PARTIAL
        sleeve_snapshots.append(
            AlphaSleeveSnapshot(
                sleeve_id=sleeve.sleeve_id,
                strategy_ids=tuple(sorted(sleeve.strategy_ids)),
                eligible_strategy_ids=sleeve_eligible,
                blocked_strategy_ids=sleeve_blocked,
                status=sleeve_status,
                mean_quality_score=mean_quality,
                capital_budget_fraction=sleeve.capital_budget_fraction,
                max_concurrent_positions=sleeve.max_concurrent_positions,
            )
        )

    invalid_pit_count = sum(not item.point_in_time_valid for item in states)
    if states and len(blocked) == len(states):
        portfolio_status: PortfolioStatus = PortfolioStatus.BLOCKED
        reason = "all_alpha_strategies_blocked"
    elif invalid_pit_count > 0 or missing_health or any(
        item.status is not AlphaHealthStatus.HEALTHY for item in states
    ):
        portfolio_status = PortfolioStatus.PARTIAL
        reason = "portfolio_of_alphas_ready_with_degraded_or_missing_health"
    else:
        portfolio_status = PortfolioStatus.READY
        reason = "portfolio_of_alphas_ready"

    canonical = {
        "request_id": request.request_id,
        "decision_time_utc": request.decision_time_utc,
        "registry_id": request.alpha_registry.registry_id,
        "strategies": [item.model_dump(mode="json") for item in states],
        "sleeves": [item.model_dump(mode="json") for item in sleeve_snapshots],
    }
    return AlphaPortfolioSnapshot(
        portfolio_id=stable_id("portfolio-of-alphas", canonical),
        request_id=request.request_id,
        registry_id=request.alpha_registry.registry_id,
        decision_time_utc=request.decision_time_utc,
        created_at_utc=request.decision_time_utc,
        status=portfolio_status,
        reason=reason,
        strategies=tuple(states),
        sleeves=tuple(sleeve_snapshots),
        strategy_count=len(states),
        sleeve_count=len(sleeve_snapshots),
        eligible_strategy_ids=tuple(sorted(eligible)),
        blocked_strategy_ids=tuple(sorted(set(blocked))),
        missing_health_strategy_ids=tuple(sorted(missing_health)),
        point_in_time_valid_for_used_inputs=invalid_pit_count == 0,
    )
