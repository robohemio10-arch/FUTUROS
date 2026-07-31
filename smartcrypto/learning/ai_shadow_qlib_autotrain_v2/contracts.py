"""Typed contracts for governed AI Shadow/Qlib research orchestration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "ai_shadow_qlib_autotrain_v2"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"


class GateStatus(StrEnum):
    """Canonical status values for research gates."""

    OK = "ok"
    BLOCKED = "blocked"
    WARNING = "warning"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class SafetyFlags:
    """Non-negotiable operational boundaries for B05."""

    paper_only: bool = True
    shadow_only: bool = True
    research_only: bool = True
    live_trading_enabled: bool = False
    live_release_allowed: bool = False
    canary_release_allowed: bool = False
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False
    exchange_private_access: bool = False
    sends_orders: bool = False
    changes_risk: bool = False
    changes_model: bool = False
    operational_authority: bool = False
    automatic_promotion: bool = False
    runtime_activation: bool = False
    writes_runtime: bool = False
    writes_active_registry: bool = False
    updates_qlib_runtime: bool = False
    updates_ai_shadow_runtime: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "paper_only": self.paper_only,
            "shadow_only": self.shadow_only,
            "research_only": self.research_only,
            "live_trading_enabled": self.live_trading_enabled,
            "live_release_allowed": self.live_release_allowed,
            "canary_release_allowed": self.canary_release_allowed,
            "order_submission_enabled": self.order_submission_enabled,
            "real_order_submission_enabled": self.real_order_submission_enabled,
            "exchange_private_access": self.exchange_private_access,
            "sends_orders": self.sends_orders,
            "changes_risk": self.changes_risk,
            "changes_model": self.changes_model,
            "operational_authority": self.operational_authority,
            "automatic_promotion": self.automatic_promotion,
            "runtime_activation": self.runtime_activation,
            "writes_runtime": self.writes_runtime,
            "writes_active_registry": self.writes_active_registry,
            "updates_qlib_runtime": self.updates_qlib_runtime,
            "updates_ai_shadow_runtime": self.updates_ai_shadow_runtime,
        }


@dataclass(frozen=True)
class StrategyPolicy:
    """Research-only policy evaluated against each candle/event."""

    policy_id: str
    score_source: str
    enter_threshold: float
    ai_shadow_reject_below: float | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "StrategyPolicy":
        policy_id = _required_text(payload, "policy_id")
        score_source = _required_text(payload, "score_source")
        enter_threshold = _probability(payload.get("enter_threshold"), "enter_threshold")
        raw_reject = payload.get("ai_shadow_reject_below")
        reject = None if raw_reject is None else _probability(raw_reject, "ai_shadow_reject_below")
        return cls(
            policy_id=policy_id,
            score_source=score_source,
            enter_threshold=enter_threshold,
            ai_shadow_reject_below=reject,
        )


@dataclass(frozen=True)
class CadenceContract:
    """Cadence declaration only; this branch never registers a scheduler."""

    operational_check_minutes: int
    feedback_trigger: str
    drift_check_minutes: int
    smoke_training_interval_days: int
    full_training_interval_days: int
    governance_interval_days: int

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CadenceContract":
        return cls(
            operational_check_minutes=_positive_int(
                payload.get("operational_check_minutes"),
                "operational_check_minutes",
            ),
            feedback_trigger=_required_text(payload, "feedback_trigger"),
            drift_check_minutes=_positive_int(
                payload.get("drift_check_minutes"),
                "drift_check_minutes",
            ),
            smoke_training_interval_days=_positive_int(
                payload.get("smoke_training_interval_days"),
                "smoke_training_interval_days",
            ),
            full_training_interval_days=_positive_int(
                payload.get("full_training_interval_days"),
                "full_training_interval_days",
            ),
            governance_interval_days=_positive_int(
                payload.get("governance_interval_days"),
                "governance_interval_days",
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "operational_check_minutes": self.operational_check_minutes,
            "feedback_trigger": self.feedback_trigger,
            "drift_check_minutes": self.drift_check_minutes,
            "smoke_training_interval_days": self.smoke_training_interval_days,
            "full_training_interval_days": self.full_training_interval_days,
            "governance_interval_days": self.governance_interval_days,
            "creates_cron": False,
            "creates_systemd_timer": False,
            "creates_windows_task": False,
            "creates_service": False,
            "registers_scheduler": False,
        }


@dataclass(frozen=True)
class PipelineConfig:
    """Validated configuration for B05 evidence generation."""

    calibration_bins: int
    min_bucket_rows: int
    min_training_sample_rows: int
    max_brier_degradation: float
    max_ece_degradation: float
    max_expected_value_degradation: float
    policies: tuple[StrategyPolicy, ...]
    cadence: CadenceContract

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PipelineConfig":
        policies_payload = payload.get("strategy_policies")
        if not isinstance(policies_payload, list) or not policies_payload:
            raise ValueError("strategy_policies must be a non-empty list")
        policies = tuple(
            StrategyPolicy.from_mapping(item)
            for item in policies_payload
            if isinstance(item, Mapping)
        )
        if len(policies) != len(policies_payload):
            raise ValueError("every strategy_policies item must be an object")
        policy_ids = [policy.policy_id for policy in policies]
        if len(set(policy_ids)) != len(policy_ids):
            raise ValueError("strategy policy ids must be unique")
        cadence_payload = payload.get("cadence")
        if not isinstance(cadence_payload, Mapping):
            raise ValueError("cadence must be an object")
        return cls(
            calibration_bins=_positive_int(payload.get("calibration_bins"), "calibration_bins"),
            min_bucket_rows=_positive_int(payload.get("min_bucket_rows"), "min_bucket_rows"),
            min_training_sample_rows=_positive_int(
                payload.get("min_training_sample_rows"),
                "min_training_sample_rows",
            ),
            max_brier_degradation=_nonnegative_float(
                payload.get("max_brier_degradation"),
                "max_brier_degradation",
            ),
            max_ece_degradation=_nonnegative_float(
                payload.get("max_ece_degradation"),
                "max_ece_degradation",
            ),
            max_expected_value_degradation=_nonnegative_float(
                payload.get("max_expected_value_degradation"),
                "max_expected_value_degradation",
            ),
            policies=policies,
            cadence=CadenceContract.from_mapping(cadence_payload),
        )


DEFAULT_CONFIG_PATH = Path("config/ai_shadow_qlib_autotrain_v2.json")
DEFAULT_REPORT_JSON = Path("data/reports/ai_shadow_qlib_autotrain_v2.json")
DEFAULT_REPORT_MD = Path("data/reports/ai_shadow_qlib_autotrain_v2.md")
DEFAULT_UPSTREAM_DRIFT_REPORT = Path("data/reports/ai_qlib_drift_regime_monitor_v1.json")


def load_pipeline_config(project_root: Path, config_path: str | Path | None) -> PipelineConfig:
    relative_or_absolute = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    path = (
        relative_or_absolute
        if relative_or_absolute.is_absolute()
        else project_root / relative_or_absolute
    )
    parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(parsed, Mapping):
        raise ValueError("B05 config root must be an object")
    return PipelineConfig.from_mapping(parsed)


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _nonnegative_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative number") from exc
    if parsed < 0.0:
        raise ValueError(f"{field} must be a non-negative number")
    return parsed


def _probability(value: Any, field: str) -> float:
    parsed = _nonnegative_float(value, field)
    if parsed > 1.0:
        raise ValueError(f"{field} must be between 0 and 1")
    return parsed
