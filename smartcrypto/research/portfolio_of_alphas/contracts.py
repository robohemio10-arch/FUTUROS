"""Immutable contracts for W6 Portfolio of Alphas and Fleet read-only control."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from smartcrypto.research.portfolio_intelligence.contracts import (
    AlphaRegistrySnapshot,
    FrozenContract,
    Identifier,
    Sha256Hex,
    UnitScore,
    require_utc,
)


class PortfolioStatus(str, Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


class AlphaHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    PAUSED_RESEARCH = "PAUSED_RESEARCH"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class FleetHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class FleetRuntimeMode(str, Enum):
    PAPER = "paper"
    DRY_RUN = "dry_run"
    RESEARCH = "research"


class AlphaHealthObservation(FrozenContract):
    schema_version: Literal["alpha_health_observation_v1"] = "alpha_health_observation_v1"
    strategy_id: Identifier
    observed_at_utc: datetime
    available_at_utc: datetime
    stale_after_seconds: float = Field(gt=0.0, allow_inf_nan=False)
    status: AlphaHealthStatus
    quality_score: UnitScore
    reason: str = Field(min_length=1, max_length=300)
    source_hash: Sha256Hex
    research_only: Literal[True] = True

    @field_validator("observed_at_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "AlphaHealthObservation":
        if self.observed_at_utc > self.available_at_utc:
            raise ValueError("alpha_health_observed_after_available")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.observed_at_utc > decision:
            errors.append("alpha_health_observed_after_decision")
        if self.available_at_utc > decision:
            errors.append("alpha_health_available_after_decision")
        return tuple(errors)

    def is_stale(self, decision_time_utc: datetime) -> bool:
        decision = require_utc(decision_time_utc)
        return (decision - self.available_at_utc).total_seconds() > self.stale_after_seconds


class AlphaSleeveDefinition(FrozenContract):
    schema_version: Literal["alpha_sleeve_definition_v1"] = "alpha_sleeve_definition_v1"
    sleeve_id: Identifier
    strategy_ids: tuple[Identifier, ...] = Field(min_length=1)
    objective: str = Field(min_length=3, max_length=300)
    capital_budget_fraction: UnitScore
    max_concurrent_positions: int = Field(ge=1, le=100)
    supported_regimes: tuple[str, ...] = ()
    research_only: Literal[True] = True

    @model_validator(mode="after")
    def _validate_unique_strategy_ids(self) -> "AlphaSleeveDefinition":
        if len(self.strategy_ids) != len(set(self.strategy_ids)):
            raise ValueError("duplicate_strategy_id_within_sleeve")
        return self


class AlphaStrategyState(FrozenContract):
    strategy_id: Identifier
    sleeve_id: Identifier
    status: AlphaHealthStatus
    quality_score: UnitScore | None = None
    health_age_seconds: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    health_reason: str
    point_in_time_valid: bool
    point_in_time_errors: tuple[str, ...] = ()


class AlphaSleeveSnapshot(FrozenContract):
    sleeve_id: Identifier
    strategy_ids: tuple[Identifier, ...]
    eligible_strategy_ids: tuple[Identifier, ...]
    blocked_strategy_ids: tuple[Identifier, ...]
    status: PortfolioStatus
    mean_quality_score: UnitScore | None = None
    capital_budget_fraction: UnitScore
    max_concurrent_positions: int


class AlphaPortfolioRequest(FrozenContract):
    schema_version: Literal["alpha_portfolio_request_v1"] = "alpha_portfolio_request_v1"
    request_id: Identifier
    decision_time_utc: datetime
    alpha_registry: AlphaRegistrySnapshot
    sleeves: tuple[AlphaSleeveDefinition, ...]
    health_observations: tuple[AlphaHealthObservation, ...] = ()

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_registry_and_sleeves(self) -> "AlphaPortfolioRequest":
        if self.alpha_registry.created_at_utc > self.decision_time_utc:
            raise ValueError("alpha_registry_created_after_decision")
        registered = {item.strategy_id for item in self.alpha_registry.definitions}
        sleeve_ids = [item.sleeve_id for item in self.sleeves]
        if len(sleeve_ids) != len(set(sleeve_ids)):
            raise ValueError("duplicate_sleeve_id")
        membership: list[str] = []
        for sleeve in self.sleeves:
            for strategy_id in sleeve.strategy_ids:
                if strategy_id not in registered:
                    raise ValueError("sleeve_contains_unregistered_strategy_id")
                membership.append(strategy_id)
        if len(membership) != len(set(membership)):
            raise ValueError("strategy_id_assigned_to_multiple_sleeves")
        if set(membership) != registered:
            raise ValueError("alpha_registry_not_fully_covered_by_sleeves")
        health_ids = [item.strategy_id for item in self.health_observations]
        if len(health_ids) != len(set(health_ids)):
            raise ValueError("duplicate_alpha_health_observation")
        if any(strategy_id not in registered for strategy_id in health_ids):
            raise ValueError("health_observation_for_unregistered_strategy")
        budget_sum = sum(sleeve.capital_budget_fraction for sleeve in self.sleeves)
        if budget_sum > 1.000000001:
            raise ValueError("sleeve_capital_budget_fraction_exceeds_one")
        return self


class AlphaPortfolioSnapshot(FrozenContract):
    schema_version: Literal["portfolio_of_alphas_v1"] = "portfolio_of_alphas_v1"
    portfolio_id: Identifier
    request_id: Identifier
    registry_id: Identifier
    decision_time_utc: datetime
    created_at_utc: datetime
    status: PortfolioStatus
    reason: str
    strategies: tuple[AlphaStrategyState, ...]
    sleeves: tuple[AlphaSleeveSnapshot, ...]
    strategy_count: int = Field(ge=0)
    sleeve_count: int = Field(ge=0)
    eligible_strategy_ids: tuple[Identifier, ...]
    blocked_strategy_ids: tuple[Identifier, ...]
    missing_health_strategy_ids: tuple[Identifier, ...]
    point_in_time_valid_for_used_inputs: bool
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False

    @field_validator("decision_time_utc", "created_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)


class FleetDiscoveredService(FrozenContract):
    schema_version: Literal["fleet_discovered_service_v1"] = "fleet_discovered_service_v1"
    service_name: Identifier
    compose_path: str = Field(min_length=1, max_length=500)
    image: str | None = Field(default=None, max_length=500)
    discovered_at_utc: datetime
    available_at_utc: datetime
    source_hash: Sha256Hex
    read_only_discovery: Literal[True] = True
    network_probe_executed: Literal[False] = False
    docker_control_executed: Literal[False] = False

    @field_validator("discovered_at_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "FleetDiscoveredService":
        if self.discovered_at_utc > self.available_at_utc:
            raise ValueError("fleet_discovery_after_available")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.discovered_at_utc > decision:
            errors.append("fleet_discovery_after_decision")
        if self.available_at_utc > decision:
            errors.append("fleet_discovery_available_after_decision")
        return tuple(errors)


class FleetInstanceObservation(FrozenContract):
    schema_version: Literal["fleet_instance_observation_v1"] = "fleet_instance_observation_v1"
    instance_id: Identifier
    service_name: Identifier
    runtime_mode: FleetRuntimeMode
    strategy_ids: tuple[Identifier, ...] = ()
    observed_at_utc: datetime
    available_at_utc: datetime
    stale_after_seconds: float = Field(gt=0.0, allow_inf_nan=False)
    source_health: FleetHealthStatus
    reason: str = Field(min_length=1, max_length=300)
    source_hash: Sha256Hex
    read_only_observation: Literal[True] = True
    network_probe_executed: Literal[False] = False
    docker_control_executed: Literal[False] = False
    exchange_private_access: Literal[False] = False
    sends_orders: Literal[False] = False

    @field_validator("observed_at_utc", "available_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_order(self) -> "FleetInstanceObservation":
        if self.observed_at_utc > self.available_at_utc:
            raise ValueError("fleet_observed_after_available")
        if len(self.strategy_ids) != len(set(self.strategy_ids)):
            raise ValueError("duplicate_strategy_id_within_fleet_instance")
        return self

    def point_in_time_errors(self, decision_time_utc: datetime) -> tuple[str, ...]:
        decision = require_utc(decision_time_utc)
        errors: list[str] = []
        if self.observed_at_utc > decision:
            errors.append("fleet_observed_after_decision")
        if self.available_at_utc > decision:
            errors.append("fleet_available_after_decision")
        return tuple(errors)

    def is_stale(self, decision_time_utc: datetime) -> bool:
        decision = require_utc(decision_time_utc)
        return (decision - self.available_at_utc).total_seconds() > self.stale_after_seconds


class FleetInstanceState(FrozenContract):
    instance_id: Identifier
    service_name: Identifier
    runtime_mode: FleetRuntimeMode
    strategy_ids: tuple[Identifier, ...]
    health_status: FleetHealthStatus
    reason: str
    observation_age_seconds: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    point_in_time_valid: bool
    point_in_time_errors: tuple[str, ...] = ()
    read_only_observation: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False


class FleetControlRequest(FrozenContract):
    schema_version: Literal["fleet_control_request_v1"] = "fleet_control_request_v1"
    request_id: Identifier
    decision_time_utc: datetime
    instances: tuple[FleetInstanceObservation, ...]
    discovered_services: tuple[FleetDiscoveredService, ...] = ()
    alpha_registry: AlphaRegistrySnapshot | None = None

    @field_validator("decision_time_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _validate_instances(self) -> "FleetControlRequest":
        instance_ids = [item.instance_id for item in self.instances]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("duplicate_fleet_instance_id")
        discovered_names = [item.service_name for item in self.discovered_services]
        if len(discovered_names) != len(set(discovered_names)):
            raise ValueError("duplicate_discovered_fleet_service")
        if self.alpha_registry is not None:
            if self.alpha_registry.created_at_utc > self.decision_time_utc:
                raise ValueError("fleet_alpha_registry_created_after_decision")
            registered = {item.strategy_id for item in self.alpha_registry.definitions}
            for instance in self.instances:
                if any(strategy_id not in registered for strategy_id in instance.strategy_ids):
                    raise ValueError("fleet_instance_contains_unregistered_strategy_id")
        return self


class FleetControlSnapshot(FrozenContract):
    schema_version: Literal["fleet_control_readonly_v1"] = "fleet_control_readonly_v1"
    fleet_id: Identifier
    request_id: Identifier
    decision_time_utc: datetime
    created_at_utc: datetime
    status: PortfolioStatus
    reason: str
    instances: tuple[FleetInstanceState, ...]
    instance_count: int = Field(ge=0)
    discovered_service_names: tuple[Identifier, ...]
    unobserved_service_names: tuple[Identifier, ...]
    healthy_count: int = Field(ge=0)
    degraded_count: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    strategy_ids_observed: tuple[Identifier, ...]
    point_in_time_valid_for_used_inputs: bool
    paper_only: Literal[True] = True
    shadow_only: Literal[True] = True
    research_only: Literal[True] = True
    read_only: Literal[True] = True
    operational_authority: Literal[False] = False
    sends_orders: Literal[False] = False
    exchange_private_access: Literal[False] = False
    changes_risk: Literal[False] = False
    changes_model: Literal[False] = False
    writes_active_signals: Literal[False] = False
    network_probe_executed: Literal[False] = False
    docker_control_executed: Literal[False] = False

    @field_validator("decision_time_utc", "created_at_utc")
    @classmethod
    def _validate_time(cls, value: datetime) -> datetime:
        return require_utc(value)
