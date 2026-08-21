"""Immutable contracts for the research-only Paper A/B Edge Selector V1."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping


SCHEMA_VERSION = "paper_ab_edge_selector_v1"
DECISION = "MANTER_BASELINE"

FINANCIAL_EVIDENCE_STATES = frozenset(
    {
        "EVIDENCE_BLOCKED",
        "INSUFFICIENT_SAMPLE",
        "NO_INCREMENTAL_EDGE",
        "PROMISING_NOT_PROVEN",
        "INCREMENTAL_EDGE_RESEARCH_ONLY",
    }
)

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "read_only": True,
    "operational_authority": False,
    "writes_sqlite": False,
    "writes_runtime": False,
    "writes_active_signals": False,
    "writes_active_model": False,
    "writes_active_registry": False,
    "trains_active_model": False,
    "promotes_model": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "changes_strategy": False,
    "changes_risk": False,
    "changes_stake": False,
    "changes_leverage": False,
    "changes_max_open_trades": False,
    "sends_orders": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "treatment_release_allowed": False,
}

REQUIRED_TREATMENT_GATES = (
    "candidate_ev_ready",
    "regression_quality_gate",
    "classification_quality_gate",
    "calibration_gate",
    "monotonicity_gate",
    "drift_gate",
    "qlib_lineage_gate",
    "trader_master_linkage_gate",
)


@dataclass(frozen=True)
class ExperimentConfig:
    """Deterministic A/B research configuration.

    ``assignment_salt_version`` is metadata for the assignment contract.  The
    V1 allocation material itself is intentionally fixed to
    ``experiment_id + '|' + candidate_id`` to preserve the canonical contract.
    """

    experiment_id: str
    assignment_salt_version: str = "sha256-v1"
    minimum_observations_per_arm: int = 200
    minimum_observation_days: int = 45
    minimum_profit_factor: float = 1.10
    bootstrap_iterations: int = 5000
    bootstrap_seed: int = 20260820
    confidence_level: float = 0.95
    treatment_ev_threshold: float = 0.0

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id_required")
        if not self.assignment_salt_version.strip():
            raise ValueError("assignment_salt_version_required")
        if self.minimum_observations_per_arm < 1:
            raise ValueError("minimum_observations_per_arm_must_be_positive")
        if self.minimum_observation_days < 0:
            raise ValueError("minimum_observation_days_must_be_non_negative")
        if self.minimum_profit_factor <= 0:
            raise ValueError("minimum_profit_factor_must_be_positive")
        if self.bootstrap_iterations < 100:
            raise ValueError("bootstrap_iterations_must_be_at_least_100")
        if not 0.5 < self.confidence_level < 1.0:
            raise ValueError("confidence_level_out_of_range")
        if not math.isfinite(float(self.treatment_ev_threshold)):
            raise ValueError("treatment_ev_threshold_must_be_finite")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AssignmentRecord:
    assignment_id: str | None
    experiment_id: str
    assignment_version: str
    candidate_id: str | None
    assignment_material_sha256: str | None
    arm: str | None
    status: str
    observed_at_utc: str | None
    candidate_linkage_status: str
    point_in_time_consumable: bool
    branch2_compatible: bool
    financial_estimate_trusted: bool
    candidate_ev: float | None
    candidate_ev_status: str
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blockers"] = list(self.blockers)
        return payload


@dataclass(frozen=True)
class ABObservation:
    assignment_id: str
    candidate_id: str
    estimate_id: str | None
    estimate_subject_id: str | None
    arm: str
    observed_at_utc: str
    symbol: str | None
    side: str | None
    regime: str | None
    candidate_ev: float
    treatment_action: str
    trade_id: int
    outcome_available_at_utc: str
    realized_net_pnl_usdt: float
    effective_arm_pnl_usdt: float
    capital_hours: float | None = None
    duration_hours: float | None = None
    fees: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArmFinancialMetrics:
    arm: str
    trade_count: int
    eligible_count: int
    accepted_count: int
    rejected_count: int
    net_pnl: float
    expectancy: float | None
    profit_factor: float | None
    win_rate: float | None
    payoff_ratio: float | None
    max_drawdown: float | None
    optional_metrics: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["optional_metrics"] = dict(self.optional_metrics)
        return payload


@dataclass(frozen=True)
class IncrementalEdgeEvidence:
    status: str
    reason: str
    treatment_evaluable: bool
    eligible_treatment_count: int
    control_count: int
    treatment_count: int
    observed_days: float
    sample_gate_passed: bool
    period_gate_passed: bool
    delta_net_pnl: float | None
    delta_expectancy: float | None
    delta_profit_factor: float | None
    delta_max_drawdown: float | None
    expectancy_ci_lower: float | None
    expectancy_ci_upper: float | None
    confidence_level: float
    bootstrap_iterations: int
    bootstrap_seed: int
    bootstrap_method: str | None
    effective_sample: int
    treatment_profit_factor_gate: bool
    edge_ci_gate: bool
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in FINANCIAL_EVIDENCE_STATES:
            raise ValueError(f"invalid_financial_evidence_status:{self.status}")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blockers"] = list(self.blockers)
        return payload
