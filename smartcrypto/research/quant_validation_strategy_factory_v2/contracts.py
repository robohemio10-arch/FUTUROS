"""Versioned contracts for the B04 quantitative validation protocol.

The module contains only deterministic, research-only domain contracts.  It has
no persistence authority and no operational integration with Freqtrade,
RiskManager, Qlib runtime, AI Shadow runtime, registries, or order submission.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping

PROTOCOL_ID = "smart_futuros_quant_validation_strategy_factory"
PROTOCOL_VERSION = "2.0.0"
SCHEMA_VERSION = "quant_validation_strategy_factory_v2"
B03_EXECUTION_ENGINE_VERSION = "futures_execution_realism_engine_v2"

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "automatic_promotion_allowed": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "writes_active_signals": False,
    "writes_active_registry": False,
    "writes_active_model_artifact": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
}


class ContractError(ValueError):
    """Raised when a versioned protocol or candidate contract is invalid."""


class SplitMode(StrEnum):
    EXPANDING = "expanding"
    ROLLING = "rolling"
    ANCHORED = "anchored"


class StepStatus(StrEnum):
    # Protocol status literal; never credential material.
    PASS = "PASS"  # nosec B105
    BLOCKED = "BLOCKED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED_INSUFFICIENT_SAMPLE = "BLOCKED_INSUFFICIENT_SAMPLE"


class CandidateDecision(StrEnum):
    REJECTED_DATA_QUALITY = "REJECTED_DATA_QUALITY"
    REJECTED_LEAKAGE = "REJECTED_LEAKAGE"
    REJECTED_INSUFFICIENT_SAMPLE = "REJECTED_INSUFFICIENT_SAMPLE"
    REJECTED_OVERFIT = "REJECTED_OVERFIT"
    REJECTED_UNSTABLE_PARAMETERS = "REJECTED_UNSTABLE_PARAMETERS"
    REJECTED_NEGATIVE_OOS = "REJECTED_NEGATIVE_OOS"
    REJECTED_MATERIAL_NEGATIVE_SEGMENT = "REJECTED_MATERIAL_NEGATIVE_SEGMENT"
    REJECTED_RISK_OF_RUIN = "REJECTED_RISK_OF_RUIN"
    REJECTED_COST_SENSITIVITY = "REJECTED_COST_SENSITIVITY"
    RESEARCH_CHALLENGER = "RESEARCH_CHALLENGER"
    RESEARCH_BASELINE_CONTROL = "RESEARCH_BASELINE_CONTROL"


class DatasetAuthority(StrEnum):
    SYNTHETIC_FIXTURE = "synthetic_fixture"
    PERMANENT_QUARANTINE = "permanent_quarantine"
    HISTORICAL_RESEARCH_VERIFIED = "historical_research_verified"
    PAPER_OUTCOME_RECONCILED = "paper_outcome_reconciled"

    @property
    def can_produce_authoritative_research(self) -> bool:
        return self is DatasetAuthority.PAPER_OUTCOME_RECONCILED


@dataclass(frozen=True)
class TemporalSplitContract:
    mode: SplitMode = SplitMode.EXPANDING
    fold_count: int = 3
    validation_rows: int = 20
    test_rows: int = 20
    minimum_train_rows: int = 60
    rolling_train_rows: int = 120
    purge_seconds: int = 0
    embargo_seconds: int = 86_400
    feature_lookback_seconds: int = 0
    label_horizon_seconds: int = 0

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.fold_count <= 0:
            errors.append("fold_count_must_be_positive")
        if self.validation_rows <= 0:
            errors.append("validation_rows_must_be_positive")
        if self.test_rows <= 0:
            errors.append("test_rows_must_be_positive")
        if self.minimum_train_rows <= 0:
            errors.append("minimum_train_rows_must_be_positive")
        if self.rolling_train_rows < self.minimum_train_rows:
            errors.append("rolling_train_rows_below_minimum_train_rows")
        for name in (
            "purge_seconds",
            "embargo_seconds",
            "feature_lookback_seconds",
            "label_horizon_seconds",
        ):
            if int(getattr(self, name)) < 0:
                errors.append(f"{name}_must_be_non_negative")
        return errors


@dataclass(frozen=True)
class RobustnessContract:
    monte_carlo_simulations: int = 1_000
    block_bootstrap_size: int = 10
    cpcv_group_count: int = 5
    cpcv_test_group_count: int = 2
    ruin_threshold_fraction: float = 0.30
    max_risk_of_ruin: float = 0.05
    max_pbo: float = 0.50
    significance_level: float = 0.05
    annualization_factor: int = 365
    seed: int = 42

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.monte_carlo_simulations < 100:
            errors.append("monte_carlo_simulations_below_100")
        if self.block_bootstrap_size <= 0:
            errors.append("block_bootstrap_size_must_be_positive")
        if self.cpcv_group_count < 3:
            errors.append("cpcv_group_count_below_3")
        if not 0 < self.cpcv_test_group_count < self.cpcv_group_count:
            errors.append("invalid_cpcv_test_group_count")
        for name in ("ruin_threshold_fraction", "max_risk_of_ruin", "max_pbo"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                errors.append(f"{name}_outside_unit_interval")
        if not 0.0 < self.significance_level < 1.0:
            errors.append("significance_level_outside_open_unit_interval")
        if self.annualization_factor <= 0:
            errors.append("annualization_factor_must_be_positive")
        return errors


@dataclass(frozen=True)
class AcceptanceGates:
    minimum_total_trades: int = 100
    minimum_trades_per_fold: int = 20
    minimum_trades_per_segment: int = 15
    minimum_oos_profit_factor: float = 1.0
    minimum_oos_expectancy: float = 0.0
    minimum_deflated_sharpe_probability: float = 0.95
    maximum_white_reality_check_pvalue: float = 0.05
    minimum_parameter_stability: float = 0.60
    maximum_cost_drag_ratio: float = 0.75
    material_negative_segment_expectancy: float = -0.01

    def validate(self) -> list[str]:
        errors: list[str] = []
        for name in (
            "minimum_total_trades",
            "minimum_trades_per_fold",
            "minimum_trades_per_segment",
        ):
            if int(getattr(self, name)) <= 0:
                errors.append(f"{name}_must_be_positive")
        if self.minimum_oos_profit_factor < 0:
            errors.append("minimum_oos_profit_factor_must_be_non_negative")
        for name in (
            "minimum_deflated_sharpe_probability",
            "maximum_white_reality_check_pvalue",
            "minimum_parameter_stability",
            "maximum_cost_drag_ratio",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                errors.append(f"{name}_outside_unit_interval")
        return errors


@dataclass(frozen=True)
class ValidationProtocol:
    protocol_id: str = PROTOCOL_ID
    protocol_version: str = PROTOCOL_VERSION
    execution_engine_version: str = B03_EXECUTION_ENGINE_VERSION
    split: TemporalSplitContract = field(default_factory=TemporalSplitContract)
    robustness: RobustnessContract = field(default_factory=RobustnessContract)
    gates: AcceptanceGates = field(default_factory=AcceptanceGates)
    required_steps: tuple[str, ...] = (
        "data_quality",
        "anti_leakage",
        "temporal_split",
        "event_driven_execution",
        "cost_reconciliation",
        "walk_forward",
        "cpcv_pbo",
        "monte_carlo",
        "multiple_testing",
        "parameter_stability",
        "oos_segments",
        "scorecard",
    )
    safety_flags: Mapping[str, bool] = field(default_factory=lambda: dict(SAFETY_FLAGS))

    def validate(self) -> list[str]:
        errors = [*self.split.validate(), *self.robustness.validate(), *self.gates.validate()]
        if self.protocol_id != PROTOCOL_ID:
            errors.append("unexpected_protocol_id")
        if not self.protocol_version:
            errors.append("missing_protocol_version")
        if self.execution_engine_version != B03_EXECUTION_ENGINE_VERSION:
            errors.append("unsupported_execution_engine_version")
        if len(set(self.required_steps)) != len(self.required_steps):
            errors.append("duplicate_required_steps")
        if not self.required_steps:
            errors.append("required_steps_empty")
        for key, expected in SAFETY_FLAGS.items():
            if self.safety_flags.get(key) is not expected:
                errors.append(f"unsafe_safety_flag:{key}")
        return sorted(set(errors))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["split"]["mode"] = self.split.mode.value
        payload["required_steps"] = list(self.required_steps)
        payload["safety_flags"] = dict(self.safety_flags)
        return payload

    @property
    def protocol_hash(self) -> str:
        errors = self.validate()
        if errors:
            raise ContractError("invalid_protocol:" + ",".join(errors))
        return stable_hash(self.to_dict())


@dataclass(frozen=True)
class StrategyCandidate:
    candidate_family: str
    strategy_version: str
    parameters: Mapping[str, Any]
    baseline_control: bool = False
    rationale: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.candidate_family.strip():
            errors.append("candidate_family_empty")
        if not self.strategy_version.strip():
            errors.append("strategy_version_empty")
        if not isinstance(self.parameters, Mapping):
            errors.append("parameters_must_be_mapping")
        return errors

    def canonical_payload(self) -> dict[str, Any]:
        errors = self.validate()
        if errors:
            raise ContractError("invalid_candidate:" + ",".join(errors))
        return {
            "candidate_family": self.candidate_family.strip(),
            "strategy_version": self.strategy_version.strip(),
            "parameters": json_safe(dict(self.parameters)),
            "baseline_control": bool(self.baseline_control),
            "rationale": self.rationale.strip(),
        }

    @property
    def candidate_id(self) -> str:
        digest = stable_hash(self.canonical_payload())
        return f"candidate_{digest[:24]}"

    @property
    def parameter_hash(self) -> str:
        return stable_hash(json_safe(dict(self.parameters)))


@dataclass(frozen=True)
class StepEvidence:
    step: str
    status: StepStatus
    reason: str
    metrics: Mapping[str, Any] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "status": self.status.value,
            "reason": self.reason,
            "metrics": json_safe(dict(self.metrics)),
            "blockers": list(self.blockers),
        }


def validate_step_coverage(protocol: ValidationProtocol, steps: Mapping[str, StepEvidence]) -> list[str]:
    errors: list[str] = []
    for name in protocol.required_steps:
        evidence = steps.get(name)
        if evidence is None:
            errors.append(f"missing_protocol_step:{name}")
            continue
        if evidence.status is StepStatus.NOT_APPLICABLE and not evidence.reason.strip():
            errors.append(f"not_applicable_without_justification:{name}")
    unknown = sorted(set(steps) - set(protocol.required_steps))
    errors.extend(f"unknown_protocol_step:{name}" for name in unknown)
    return errors


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def stable_json(payload: Any) -> str:
    return json.dumps(json_safe(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_safe(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
    return value
