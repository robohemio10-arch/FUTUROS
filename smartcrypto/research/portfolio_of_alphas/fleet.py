"""Read-only Fleet Control snapshot builder for W6."""

from __future__ import annotations

from smartcrypto.research.portfolio_intelligence.contracts import stable_id

from .contracts import (
    FleetControlRequest,
    FleetControlSnapshot,
    FleetHealthStatus,
    FleetInstanceState,
    PortfolioStatus,
)


def build_fleet_control_snapshot(request: FleetControlRequest) -> FleetControlSnapshot:
    states: list[FleetInstanceState] = []
    all_strategy_ids: set[str] = set()
    discovery_pit_invalid = False
    discovered_service_names: set[str] = set()
    for discovery in request.discovered_services:
        discovered_service_names.add(discovery.service_name)
        if discovery.point_in_time_errors(request.decision_time_utc):
            discovery_pit_invalid = True

    for instance in sorted(request.instances, key=lambda item: item.instance_id):
        all_strategy_ids.update(instance.strategy_ids)
        pit_errors = instance.point_in_time_errors(request.decision_time_utc)
        if pit_errors:
            states.append(
                FleetInstanceState(
                    instance_id=instance.instance_id,
                    service_name=instance.service_name,
                    runtime_mode=instance.runtime_mode,
                    strategy_ids=tuple(sorted(instance.strategy_ids)),
                    health_status=FleetHealthStatus.BLOCKED,
                    reason="invalid_point_in_time_fleet_observation",
                    observation_age_seconds=None,
                    point_in_time_valid=False,
                    point_in_time_errors=pit_errors,
                )
            )
            continue

        age_seconds = max(
            0.0,
            (request.decision_time_utc - instance.available_at_utc).total_seconds(),
        )
        if instance.is_stale(request.decision_time_utc):
            health_status = FleetHealthStatus.STALE
            reason = "stale_fleet_observation"
        else:
            health_status = instance.source_health
            reason = instance.reason

        states.append(
            FleetInstanceState(
                instance_id=instance.instance_id,
                service_name=instance.service_name,
                runtime_mode=instance.runtime_mode,
                strategy_ids=tuple(sorted(instance.strategy_ids)),
                health_status=health_status,
                reason=reason,
                observation_age_seconds=age_seconds,
                point_in_time_valid=True,
                point_in_time_errors=(),
            )
        )

    observed_service_names = {item.service_name for item in states}
    unobserved_service_names = discovered_service_names - observed_service_names

    healthy_count = sum(item.health_status is FleetHealthStatus.HEALTHY for item in states)
    degraded_count = sum(item.health_status is FleetHealthStatus.DEGRADED for item in states)
    stale_count = sum(item.health_status is FleetHealthStatus.STALE for item in states)
    blocked_count = sum(item.health_status is FleetHealthStatus.BLOCKED for item in states)
    unavailable_count = sum(item.health_status is FleetHealthStatus.UNAVAILABLE for item in states)
    invalid_count = sum(not item.point_in_time_valid for item in states)

    if not states and not discovered_service_names:
        status = PortfolioStatus.BLOCKED
        reason = "fleet_inventory_and_observation_empty"
    elif not states and discovered_service_names:
        status = PortfolioStatus.PARTIAL
        reason = "fleet_discovered_without_health_observations"
    elif blocked_count == len(states) or unavailable_count == len(states):
        status = PortfolioStatus.BLOCKED
        reason = "fleet_has_no_usable_instances"
    elif discovery_pit_invalid or unobserved_service_names or invalid_count or blocked_count or stale_count or degraded_count or unavailable_count:
        status = PortfolioStatus.PARTIAL
        reason = "fleet_readonly_snapshot_ready_with_degraded_instances"
    else:
        status = PortfolioStatus.READY
        reason = "fleet_readonly_snapshot_ready"

    canonical = {
        "request_id": request.request_id,
        "decision_time_utc": request.decision_time_utc,
        "instances": [item.model_dump(mode="json") for item in states],
    }
    return FleetControlSnapshot(
        fleet_id=stable_id("fleet-readonly", canonical),
        request_id=request.request_id,
        decision_time_utc=request.decision_time_utc,
        created_at_utc=request.decision_time_utc,
        status=status,
        reason=reason,
        instances=tuple(states),
        instance_count=len(states),
        discovered_service_names=tuple(sorted(discovered_service_names)),
        unobserved_service_names=tuple(sorted(unobserved_service_names)),
        healthy_count=healthy_count,
        degraded_count=degraded_count,
        stale_count=stale_count,
        blocked_count=blocked_count,
        strategy_ids_observed=tuple(sorted(all_strategy_ids)),
        point_in_time_valid_for_used_inputs=invalid_count == 0 and not discovery_pit_invalid,
    )
