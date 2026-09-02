from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smartcrypto.research.portfolio_intelligence import AlphaDefinition, build_alpha_registry
from smartcrypto.research.portfolio_of_alphas import (
    FleetControlRequest,
    FleetHealthStatus,
    FleetInstanceObservation,
    FleetRuntimeMode,
    PortfolioStatus,
    build_fleet_control_snapshot,
    discover_freqtrade_services_from_compose,
)

T = datetime(2026, 8, 28, 17, 30, tzinfo=timezone.utc)
H = "2" * 64


def _registry():
    return build_alpha_registry(
        [
            AlphaDefinition(
                strategy_id="trend-v1",
                sleeve="directional",
                version="1",
                feature_set_hash=H,
                hypothesis="trend continuation alpha hypothesis",
            )
        ],
        created_at_utc=T,
    )


def _instance(instance_id: str, *, available=T, health=FleetHealthStatus.HEALTHY):
    return FleetInstanceObservation(
        instance_id=instance_id,
        service_name=f"freqtrade-{instance_id}",
        runtime_mode=FleetRuntimeMode.PAPER,
        strategy_ids=("trend-v1",),
        observed_at_utc=available,
        available_at_utc=available,
        stale_after_seconds=120.0,
        source_health=health,
        reason="offline_fleet_fixture",
        source_hash=H,
    )


def test_multi_instance_fleet_is_deterministic_and_readonly() -> None:
    request = FleetControlRequest(
        request_id="f1",
        decision_time_utc=T,
        instances=(_instance("b"), _instance("a")),
        alpha_registry=_registry(),
    )
    one = build_fleet_control_snapshot(request)
    two = build_fleet_control_snapshot(request.model_copy(update={"instances": tuple(reversed(request.instances))}))
    assert one.fleet_id == two.fleet_id
    assert one.status is PortfolioStatus.READY
    assert one.instance_count == 2
    assert one.network_probe_executed is False
    assert one.docker_control_executed is False
    assert one.sends_orders is False


def test_stale_instance_is_explicit_partial() -> None:
    old = T - timedelta(minutes=10)
    snapshot = build_fleet_control_snapshot(
        FleetControlRequest(
            request_id="f2",
            decision_time_utc=T,
            instances=(_instance("a", available=old),),
            alpha_registry=_registry(),
        )
    )
    assert snapshot.status is PortfolioStatus.PARTIAL
    assert snapshot.stale_count == 1
    assert snapshot.instances[0].health_status is FleetHealthStatus.STALE


def test_future_fleet_observation_is_blocked() -> None:
    future = T + timedelta(seconds=1)
    snapshot = build_fleet_control_snapshot(
        FleetControlRequest(
            request_id="f3",
            decision_time_utc=T,
            instances=(_instance("a", available=future),),
            alpha_registry=_registry(),
        )
    )
    assert snapshot.status is PortfolioStatus.BLOCKED
    assert snapshot.blocked_count == 1
    assert snapshot.point_in_time_valid_for_used_inputs is False


def test_duplicate_instance_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate_fleet_instance_id"):
        FleetControlRequest(
            request_id="f4",
            decision_time_utc=T,
            instances=(_instance("a"), _instance("a")),
            alpha_registry=_registry(),
        )


def test_unregistered_strategy_is_rejected() -> None:
    bad = FleetInstanceObservation(
        instance_id="a",
        service_name="freqtrade-a",
        runtime_mode=FleetRuntimeMode.PAPER,
        strategy_ids=("unknown-v1",),
        observed_at_utc=T,
        available_at_utc=T,
        stale_after_seconds=120.0,
        source_health=FleetHealthStatus.HEALTHY,
        reason="offline_fleet_fixture",
        source_hash=H,
    )
    with pytest.raises(ValueError, match="fleet_instance_contains_unregistered_strategy_id"):
        FleetControlRequest(
            request_id="f5",
            decision_time_utc=T,
            instances=(bad,),
            alpha_registry=_registry(),
        )


def test_compose_discovery_is_readonly_and_finds_multiple_freqtrade_services(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """services:
  freqtrade-a:
    image: freqtradeorg/freqtrade:stable
  worker:
    image: python:3.12
  bot-b:
    image: freqtradeorg/freqtrade:stable
""",
        encoding="utf-8",
    )
    discovered = discover_freqtrade_services_from_compose(
        compose,
        project_root=tmp_path,
        decision_time_utc=T,
    )
    assert [item.service_name for item in discovered] == ["bot-b", "freqtrade-a"]
    assert all(item.network_probe_executed is False for item in discovered)
    assert all(item.docker_control_executed is False for item in discovered)


def test_discovered_service_without_health_is_partial(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n  freqtrade-paper:\n    image: freqtradeorg/freqtrade:stable\n",
        encoding="utf-8",
    )
    discovered = discover_freqtrade_services_from_compose(
        compose,
        project_root=tmp_path,
        decision_time_utc=T,
    )
    snapshot = build_fleet_control_snapshot(
        FleetControlRequest(
            request_id="f6",
            decision_time_utc=T,
            instances=(),
            discovered_services=discovered,
            alpha_registry=_registry(),
        )
    )
    assert snapshot.status is PortfolioStatus.PARTIAL
    assert snapshot.unobserved_service_names == ("freqtrade-paper",)
