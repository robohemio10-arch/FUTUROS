from __future__ import annotations

from datetime import datetime, timezone

import pytest

from smartcrypto.research.portfolio_intelligence import AlphaDefinition, build_alpha_registry
from smartcrypto.research.portfolio_of_alphas import (
    AlphaHealthObservation,
    AlphaHealthStatus,
    AlphaPortfolioRequest,
    AlphaSleeveDefinition,
    PortfolioStatus,
    build_portfolio_of_alphas,
)

T = datetime(2026, 8, 28, 17, 30, tzinfo=timezone.utc)
H = "1" * 64


def _registry():
    return build_alpha_registry(
        [
            AlphaDefinition(
                strategy_id="trend-v1",
                sleeve="directional",
                version="1",
                feature_set_hash=H,
                hypothesis="trend continuation alpha hypothesis",
                supported_regimes=("trend",),
            ),
            AlphaDefinition(
                strategy_id="breakout-v1",
                sleeve="directional",
                version="1",
                feature_set_hash=H,
                hypothesis="breakout continuation alpha hypothesis",
                supported_regimes=("trend",),
            ),
        ],
        created_at_utc=T,
    )


def _sleeves():
    return (
        AlphaSleeveDefinition(
            sleeve_id="directional",
            strategy_ids=("trend-v1", "breakout-v1"),
            objective="directional alpha sleeve",
            capital_budget_fraction=0.5,
            max_concurrent_positions=2,
            supported_regimes=("trend",),
        ),
    )


def _health(strategy_id: str, *, status=AlphaHealthStatus.HEALTHY, available=T):
    return AlphaHealthObservation(
        strategy_id=strategy_id,
        observed_at_utc=available,
        available_at_utc=available,
        stale_after_seconds=120.0,
        status=status,
        quality_score=0.9,
        reason="synthetic_health_fixture",
        source_hash=H,
    )


def test_portfolio_of_alphas_is_deterministic_and_ready() -> None:
    request = AlphaPortfolioRequest(
        request_id="p1",
        decision_time_utc=T,
        alpha_registry=_registry(),
        sleeves=_sleeves(),
        health_observations=(_health("trend-v1"), _health("breakout-v1")),
    )
    one = build_portfolio_of_alphas(request)
    two = build_portfolio_of_alphas(request.model_copy(update={"health_observations": tuple(reversed(request.health_observations))}))
    assert one.portfolio_id == two.portfolio_id
    assert one.status is PortfolioStatus.READY
    assert one.eligible_strategy_ids == ("breakout-v1", "trend-v1")
    assert one.sends_orders is False


def test_missing_health_degrades_without_inventing_status() -> None:
    request = AlphaPortfolioRequest(
        request_id="p2",
        decision_time_utc=T,
        alpha_registry=_registry(),
        sleeves=_sleeves(),
        health_observations=(_health("trend-v1"),),
    )
    snapshot = build_portfolio_of_alphas(request)
    assert snapshot.status is PortfolioStatus.PARTIAL
    assert snapshot.missing_health_strategy_ids == ("breakout-v1",)


def test_future_health_fails_closed_for_strategy() -> None:
    future = datetime(2026, 8, 28, 17, 31, tzinfo=timezone.utc)
    request = AlphaPortfolioRequest(
        request_id="p3",
        decision_time_utc=T,
        alpha_registry=_registry(),
        sleeves=_sleeves(),
        health_observations=(_health("trend-v1", available=future), _health("breakout-v1")),
    )
    snapshot = build_portfolio_of_alphas(request)
    state = next(item for item in snapshot.strategies if item.strategy_id == "trend-v1")
    assert snapshot.status is PortfolioStatus.PARTIAL
    assert state.status is AlphaHealthStatus.BLOCKED
    assert state.point_in_time_valid is False


def test_duplicate_cross_sleeve_membership_is_rejected() -> None:
    sleeves = (
        AlphaSleeveDefinition(
            sleeve_id="one",
            strategy_ids=("trend-v1",),
            objective="first sleeve",
            capital_budget_fraction=0.4,
            max_concurrent_positions=1,
        ),
        AlphaSleeveDefinition(
            sleeve_id="two",
            strategy_ids=("trend-v1", "breakout-v1"),
            objective="second sleeve",
            capital_budget_fraction=0.4,
            max_concurrent_positions=2,
        ),
    )
    with pytest.raises(ValueError, match="strategy_id_assigned_to_multiple_sleeves"):
        AlphaPortfolioRequest(
            request_id="p4",
            decision_time_utc=T,
            alpha_registry=_registry(),
            sleeves=sleeves,
        )


def test_registry_must_be_fully_covered() -> None:
    sleeves = (
        AlphaSleeveDefinition(
            sleeve_id="one",
            strategy_ids=("trend-v1",),
            objective="first sleeve",
            capital_budget_fraction=0.4,
            max_concurrent_positions=1,
        ),
    )
    with pytest.raises(ValueError, match="alpha_registry_not_fully_covered_by_sleeves"):
        AlphaPortfolioRequest(
            request_id="p5",
            decision_time_utc=T,
            alpha_registry=_registry(),
            sleeves=sleeves,
        )
