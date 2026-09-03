"""Prospective Control x Treatment evidence accounting for AIBOT Parity Paper.

The module never routes traffic, publishes signals, changes Freqtrade, changes
risk, or submits orders.  The A/B arm is an analytical assignment only.  The
observed Paper baseline remains untouched while the Treatment applies the
already-produced AIBOT shadow action counterfactually to authoritative outcomes
that become available strictly after the preregistered decision timestamp.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from smartcrypto.research.paper_ab_edge_selector import (
    ExperimentConfig,
    deterministic_bootstrap_delta_expectancy,
)

SCHEMA_VERSION = "aibot_parity_paper_ab_soak_v1"
DECISION = "COLLECT_PROSPECTIVE_EVIDENCE"
ASSIGNMENT_VERSION = "sha256-v1"
CONTROL_DEFINITION = "FREQTRADE_PAPER_BASELINE_OBSERVED_ONLY"
TREATMENT_DEFINITION = "AIBOT_PARITY_SHADOW_COUNTERFACTUAL_ONLY"
ALLOWED_ACTIONS = frozenset({"ACCEPT", "REJECT", "ABSTAIN"})

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "read_only_by_default": True,
    "operational_authority": False,
    "traffic_split_performed": False,
    "paper_behavior_changed": False,
    "treatment_runtime_assignment_performed": False,
    "writes_active_signals": False,
    "signal_published": False,
    "sends_orders": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "changes_strategy": False,
    "changes_risk": False,
    "changes_stake": False,
    "changes_leverage": False,
    "changes_roi": False,
    "changes_stoploss": False,
    "changes_universe": False,
    "changes_model": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "paper_treatment_release_allowed": False,
    "paper_activation_performed": False,
    "qlib_security_gate_bypassed": False,
}

REQUIRED_ROW_FALSE_FIELDS = (
    "operational_authority",
    "signal_published",
    "writes_active_signals",
    "sends_orders",
    "changes_risk",
    "changes_model",
)


def _stable_sha256(payload: Any) -> str:
    rendered = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _parse_utc(value: object, *, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field}_required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field}_timezone_required")
    parsed = parsed.astimezone(UTC)
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field}_must_be_utc")
    return parsed


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


@dataclass(frozen=True)
class Preregistration:
    """Immutable prospective experiment contract.

    Threshold defaults deliberately mirror ``paper_ab_edge_selector_v1`` so
    this layer does not invent a competing financial gate.
    """

    experiment_id: str
    preregistered_start_utc: str
    software_dod_merge_sha: str
    assignment_salt_version: str = ASSIGNMENT_VERSION
    minimum_observations_per_arm: int = 200
    minimum_observation_days: int = 45
    minimum_profit_factor: float = 1.10
    bootstrap_iterations: int = 5000
    bootstrap_seed: int = 20260820
    confidence_level: float = 0.95
    control_definition: str = CONTROL_DEFINITION
    treatment_definition: str = TREATMENT_DEFINITION

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id_required")
        _parse_utc(self.preregistered_start_utc, field="preregistered_start_utc")
        sha = self.software_dod_merge_sha.strip().lower()
        if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
            raise ValueError("software_dod_merge_sha_invalid")
        ExperimentConfig(
            experiment_id=self.experiment_id,
            assignment_salt_version=self.assignment_salt_version,
            minimum_observations_per_arm=self.minimum_observations_per_arm,
            minimum_observation_days=self.minimum_observation_days,
            minimum_profit_factor=self.minimum_profit_factor,
            bootstrap_iterations=self.bootstrap_iterations,
            bootstrap_seed=self.bootstrap_seed,
            confidence_level=self.confidence_level,
        )
        if self.control_definition != CONTROL_DEFINITION:
            raise ValueError("control_definition_must_remain_frozen")
        if self.treatment_definition != TREATMENT_DEFINITION:
            raise ValueError("treatment_definition_must_remain_shadow_only")

    @property
    def start_time(self) -> datetime:
        return _parse_utc(
            self.preregistered_start_utc,
            field="preregistered_start_utc",
        )

    @property
    def experiment_config(self) -> ExperimentConfig:
        return ExperimentConfig(
            experiment_id=self.experiment_id,
            assignment_salt_version=self.assignment_salt_version,
            minimum_observations_per_arm=self.minimum_observations_per_arm,
            minimum_observation_days=self.minimum_observation_days,
            minimum_profit_factor=self.minimum_profit_factor,
            bootstrap_iterations=self.bootstrap_iterations,
            bootstrap_seed=self.bootstrap_seed,
            confidence_level=self.confidence_level,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["preregistered_start_utc"] = _iso(self.start_time)
        return payload


def build_preregistration(payload: Mapping[str, Any]) -> Preregistration:
    return Preregistration(
        experiment_id=str(payload.get("experiment_id") or ""),
        preregistered_start_utc=str(payload.get("preregistered_start_utc") or ""),
        software_dod_merge_sha=str(payload.get("software_dod_merge_sha") or ""),
        assignment_salt_version=str(
            payload.get("assignment_salt_version") or ASSIGNMENT_VERSION
        ),
        minimum_observations_per_arm=int(
            payload.get("minimum_observations_per_arm", 200)
        ),
        minimum_observation_days=int(payload.get("minimum_observation_days", 45)),
        minimum_profit_factor=float(payload.get("minimum_profit_factor", 1.10)),
        bootstrap_iterations=int(payload.get("bootstrap_iterations", 5000)),
        bootstrap_seed=int(payload.get("bootstrap_seed", 20260820)),
        confidence_level=float(payload.get("confidence_level", 0.95)),
        control_definition=str(
            payload.get("control_definition") or CONTROL_DEFINITION
        ),
        treatment_definition=str(
            payload.get("treatment_definition") or TREATMENT_DEFINITION
        ),
    )


def load_preregistration(path: str | Path) -> Preregistration:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("preregistration_payload_must_be_object")
    return build_preregistration(payload)


def _analytical_assignment(config: Preregistration, candidate_id: str) -> dict[str, str]:
    material = f"{config.experiment_id}|{candidate_id}".encode("utf-8")
    digest_bytes = hashlib.sha256(material).digest()
    digest = digest_bytes.hex()
    return {
        "assignment_id": f"ab-{digest}",
        "assignment_material_sha256": digest,
        "arm": "CONTROL" if digest_bytes[0] < 128 else "TREATMENT",
    }


def _arm_metrics(rows: Sequence[Mapping[str, Any]], arm: str) -> dict[str, Any]:
    values = [
        float(row["effective_arm_pnl_usdt"])
        for row in rows
        if row.get("arm") == arm
        and _finite_float(row.get("effective_arm_pnl_usdt")) is not None
    ]
    gross_profit = sum(value for value in values if value > 0.0)
    gross_loss = abs(sum(value for value in values if value < 0.0))
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return {
        "arm": arm,
        "trade_count": len(values),
        "net_pnl": float(sum(values)),
        "expectancy": float(sum(values) / len(values)) if values else None,
        "profit_factor": (float(gross_profit / gross_loss) if gross_loss > 0.0 else None),
        "win_rate": (
            float(sum(1 for value in values if value >= 0.0) / len(values))
            if values
            else None
        ),
        "max_drawdown": float(max_drawdown) if values else None,
    }


def _observed_days(rows: Sequence[Mapping[str, Any]], field: str) -> float:
    timestamps: list[datetime] = []
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        try:
            timestamps.append(_parse_utc(value, field=field))
        except ValueError:
            continue
    if len(timestamps) < 2:
        return 0.0
    return float((max(timestamps) - min(timestamps)).total_seconds() / 86_400.0)


def _delta(a: object, b: object) -> float | None:
    left = _finite_float(a)
    right = _finite_float(b)
    return left - right if left is not None and right is not None else None


def _financial_evidence(
    *,
    config: Preregistration,
    completed_rows: list[dict[str, Any]],
    integrity_blockers: Sequence[str],
) -> dict[str, Any]:
    control = _arm_metrics(completed_rows, "CONTROL")
    treatment = _arm_metrics(completed_rows, "TREATMENT")
    control_count = int(control["trade_count"])
    treatment_count = int(treatment["trade_count"])
    observed_days = _observed_days(completed_rows, "observed_at_utc")
    sample_gate = bool(
        control_count >= config.minimum_observations_per_arm
        and treatment_count >= config.minimum_observations_per_arm
    )
    period_gate = bool(observed_days >= config.minimum_observation_days)

    common = {
        "control_count": control_count,
        "treatment_count": treatment_count,
        "observed_days": observed_days,
        "sample_gate_passed": sample_gate,
        "period_gate_passed": period_gate,
        "minimum_observations_per_arm": config.minimum_observations_per_arm,
        "minimum_observation_days": config.minimum_observation_days,
        "minimum_profit_factor": config.minimum_profit_factor,
        "confidence_level": config.confidence_level,
        "bootstrap_iterations": config.bootstrap_iterations,
        "bootstrap_seed": config.bootstrap_seed,
        "delta_net_pnl": _delta(treatment["net_pnl"], control["net_pnl"]),
        "delta_expectancy": _delta(treatment["expectancy"], control["expectancy"]),
        "delta_profit_factor": _delta(
            treatment["profit_factor"], control["profit_factor"]
        ),
        "delta_max_drawdown": _delta(
            treatment["max_drawdown"], control["max_drawdown"]
        ),
    }

    if integrity_blockers:
        return {
            **common,
            "status": "EVIDENCE_BLOCKED",
            "reason": str(integrity_blockers[0]),
            "expectancy_ci_lower": None,
            "expectancy_ci_upper": None,
            "bootstrap_method": None,
            "treatment_profit_factor_gate": False,
            "edge_ci_gate": False,
            "blockers": list(dict.fromkeys(integrity_blockers)),
            "evidence_sufficient_for_release_review": False,
        }
    if control_count == 0 or treatment_count == 0:
        return {
            **common,
            "status": "EVIDENCE_BLOCKED",
            "reason": "PROSPECTIVE_OUTCOME_SAMPLE_NOT_YET_BALANCED",
            "expectancy_ci_lower": None,
            "expectancy_ci_upper": None,
            "bootstrap_method": None,
            "treatment_profit_factor_gate": False,
            "edge_ci_gate": False,
            "blockers": ["PROSPECTIVE_OUTCOME_SAMPLE_NOT_YET_BALANCED"],
            "evidence_sufficient_for_release_review": False,
        }
    if not sample_gate or not period_gate:
        blockers: list[str] = []
        if not sample_gate:
            blockers.append("MINIMUM_OBSERVATIONS_PER_ARM_NOT_MET")
        if not period_gate:
            blockers.append("MINIMUM_OBSERVATION_DAYS_NOT_MET")
        return {
            **common,
            "status": "INSUFFICIENT_SAMPLE",
            "reason": blockers[0],
            "expectancy_ci_lower": None,
            "expectancy_ci_upper": None,
            "bootstrap_method": None,
            "treatment_profit_factor_gate": False,
            "edge_ci_gate": False,
            "blockers": blockers,
            "evidence_sufficient_for_release_review": False,
        }

    frame = pd.DataFrame(completed_rows)
    bootstrap = deterministic_bootstrap_delta_expectancy(
        frame,
        iterations=config.bootstrap_iterations,
        seed=config.bootstrap_seed,
        confidence_level=config.confidence_level,
    )
    ci_lower = _finite_float(bootstrap.get("ci_lower"))
    ci_upper = _finite_float(bootstrap.get("ci_upper"))
    treatment_pf = _finite_float(treatment.get("profit_factor"))
    pf_gate = bool(
        treatment_pf is not None and treatment_pf >= config.minimum_profit_factor
    )
    edge_ci_gate = bool(ci_lower is not None and ci_lower > 0.0)
    blockers = []
    if bootstrap.get("status") != "AVAILABLE":
        blockers.append("BOOTSTRAP_CI_UNAVAILABLE")
    if not pf_gate:
        blockers.append("TREATMENT_PROFIT_FACTOR_GATE_FAILED")
    if not edge_ci_gate:
        blockers.append("DELTA_EXPECTANCY_CI_NOT_STRICTLY_POSITIVE")

    delta_expectancy = _finite_float(common["delta_expectancy"])
    if edge_ci_gate and pf_gate:
        status = "INCREMENTAL_EDGE_RESEARCH_ONLY"
        reason = "incremental_edge_prospective_research_evidence_only"
    elif delta_expectancy is not None and delta_expectancy > 0.0:
        status = "PROMISING_NOT_PROVEN"
        reason = blockers[0] if blockers else "positive_point_estimate_not_proven"
    else:
        status = "NO_INCREMENTAL_EDGE"
        reason = blockers[0] if blockers else "non_positive_incremental_expectancy"

    return {
        **common,
        "status": status,
        "reason": reason,
        "expectancy_ci_lower": ci_lower,
        "expectancy_ci_upper": ci_upper,
        "bootstrap_method": bootstrap.get("method"),
        "bootstrap_successful_iterations": int(
            bootstrap.get("successful_iterations", 0)
        ),
        "treatment_profit_factor_gate": pf_gate,
        "edge_ci_gate": edge_ci_gate,
        "blockers": blockers,
        "evidence_sufficient_for_release_review": bool(
            status == "INCREMENTAL_EDGE_RESEARCH_ONLY"
        ),
    }


def evaluate_prospective_ab_soak(
    preregistration: Preregistration,
    candidate_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Evaluate only post-preregistration, point-in-time shadow candidate rows."""

    start = preregistration.start_time
    assignments: list[dict[str, Any]] = []
    completed_rows: list[dict[str, Any]] = []
    integrity_blockers: list[str] = []
    blocker_counts: dict[str, int] = {}
    exact_duplicate_count = 0
    pre_registration_excluded_count = 0
    pending_outcome_count = 0
    qlib_blocked_external_count = 0
    seen: dict[str, dict[str, Any]] = {}

    def add_blocker(reason: str) -> None:
        integrity_blockers.append(reason)
        blocker_counts[reason] = blocker_counts.get(reason, 0) + 1

    for raw in candidate_rows:
        row = dict(raw)
        candidate_id = _text(row.get("candidate_id"))
        cycle_id = _text(row.get("cycle_id"))
        if candidate_id is None or cycle_id is None:
            add_blocker("CANDIDATE_OR_CYCLE_ID_MISSING")
            continue
        try:
            observed = _parse_utc(row.get("observed_at_utc"), field="observed_at_utc")
        except ValueError:
            add_blocker("OBSERVED_AT_UTC_INVALID")
            continue
        if observed < start:
            pre_registration_excluded_count += 1
            continue
        if row.get("point_in_time_valid") is not True:
            add_blocker("POINT_IN_TIME_NOT_VALID")
            continue
        if row.get("financial_config_unchanged") is not True:
            add_blocker("FINANCIAL_CONFIG_PARITY_NOT_PROVEN")
            continue
        if row.get("paper_only") is not True or row.get("shadow_only") is not True:
            add_blocker("PAPER_SHADOW_CONTRACT_NOT_PROVEN")
            continue
        violated = [field for field in REQUIRED_ROW_FALSE_FIELDS if row.get(field) is not False]
        if violated:
            add_blocker("SAFETY_FLAG_VIOLATION:" + ",".join(sorted(violated)))
            continue

        action = str(row.get("treatment_action") or "").strip().upper()
        if action not in ALLOWED_ACTIONS:
            add_blocker("TREATMENT_ACTION_INVALID")
            continue
        risk_decision = str(row.get("riskmanager_shadow_decision") or "").strip().upper()
        if action == "ACCEPT" and risk_decision != "ALLOW":
            add_blocker("ACCEPT_WITHOUT_RISKMANAGER_SHADOW_ALLOW")
            continue
        if str(row.get("qlib_status") or "").strip().upper() == "BLOCKED_EXTERNAL":
            qlib_blocked_external_count += 1

        assigned = _analytical_assignment(preregistration, candidate_id)
        assignment = {
            **assigned,
            "experiment_id": preregistration.experiment_id,
            "assignment_version": preregistration.assignment_salt_version,
            "candidate_id": candidate_id,
            "cycle_id": cycle_id,
            "observed_at_utc": _iso(observed),
            "treatment_action": action,
            "riskmanager_shadow_decision": risk_decision or "UNKNOWN",
            "symbol": _text(row.get("symbol")),
            "side": _text(row.get("side")),
            "regime": _text(row.get("regime")),
            "qlib_status": _text(row.get("qlib_status")) or "UNKNOWN",
            "paper_only": True,
            "shadow_only": True,
            "operational_authority": False,
            "signal_published": False,
            "writes_active_signals": False,
            "sends_orders": False,
            "changes_risk": False,
            "changes_model": False,
        }
        assignment_id = str(assignment["assignment_id"])
        prior = seen.get(assignment_id)
        if prior is not None:
            comparable = dict(assignment)
            if prior == comparable:
                exact_duplicate_count += 1
                continue
            add_blocker("ASSIGNMENT_ID_CONFLICT")
            continue
        seen[assignment_id] = dict(assignment)

        outcome_time_raw = row.get("outcome_available_at_utc")
        pnl = _finite_float(row.get("realized_net_pnl_usdt"))
        if outcome_time_raw is None and pnl is None:
            pending_outcome_count += 1
            assignments.append(assignment)
            continue
        if outcome_time_raw is None or pnl is None:
            add_blocker("PARTIAL_OUTCOME_PAYLOAD")
            assignments.append(assignment)
            continue
        try:
            outcome_time = _parse_utc(
                outcome_time_raw,
                field="outcome_available_at_utc",
            )
        except ValueError:
            add_blocker("OUTCOME_AVAILABLE_AT_UTC_INVALID")
            assignments.append(assignment)
            continue
        if outcome_time <= observed:
            add_blocker("OUTCOME_NOT_STRICTLY_AFTER_DECISION")
            assignments.append(assignment)
            continue

        effective_pnl = pnl
        if assignment["arm"] == "TREATMENT" and action != "ACCEPT":
            effective_pnl = 0.0
        completed = {
            **assignment,
            "outcome_available_at_utc": _iso(outcome_time),
            "realized_net_pnl_usdt": pnl,
            "effective_arm_pnl_usdt": effective_pnl,
        }
        assignments.append(completed)
        completed_rows.append(completed)

    unique_cycles = len({str(row["cycle_id"]) for row in assignments})
    soak_days = _observed_days(assignments, "observed_at_utc")
    action_counts = {
        action: sum(1 for row in assignments if row.get("treatment_action") == action)
        for action in sorted(ALLOWED_ACTIONS)
    }
    arm_counts = {
        arm: sum(1 for row in assignments if row.get("arm") == arm)
        for arm in ("CONTROL", "TREATMENT")
    }
    evidence = _financial_evidence(
        config=preregistration,
        completed_rows=completed_rows,
        integrity_blockers=integrity_blockers,
    )
    if integrity_blockers:
        soak_status = "BLOCKED_INTEGRITY"
    elif evidence["status"] in {"EVIDENCE_BLOCKED", "INSUFFICIENT_SAMPLE"}:
        soak_status = "COLLECTING"
    else:
        soak_status = "RESEARCH_EVALUATION_AVAILABLE"

    prereg = preregistration.to_dict()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked" if integrity_blockers else "ok",
        "reason": (
            integrity_blockers[0]
            if integrity_blockers
            else str(evidence.get("reason") or "prospective_collection_started")
        ),
        "decision": DECISION,
        "preregistration": {
            **prereg,
            "preregistration_sha256": _stable_sha256(prereg),
            "assignment_material": "experiment_id|candidate_id",
            "allocation": "50_50_ANALYTICAL_ONLY",
        },
        "design": {
            "prospective_only": True,
            "pre_registration_cutoff_enforced": True,
            "assignment_is_analytical_only": True,
            "traffic_split_performed": False,
            "control_definition": CONTROL_DEFINITION,
            "treatment_definition": TREATMENT_DEFINITION,
            "control_effective_pnl": "AUTHORITATIVE_REALIZED_PAPER_OUTCOME",
            "treatment_effective_pnl": (
                "REALIZED_OUTCOME_IF_SHADOW_ACCEPT_ELSE_ZERO"
            ),
            "retrospective_outcome_used_for_assignment": False,
            "causal_claim_allowed": False,
            "qlib_blocked_external_isolated": True,
        },
        "soak_health": {
            "soak_status": soak_status,
            "input_row_count": len(candidate_rows),
            "eligible_assignment_count": len(assignments),
            "completed_outcome_count": len(completed_rows),
            "pending_outcome_count": pending_outcome_count,
            "pre_registration_excluded_count": pre_registration_excluded_count,
            "exact_duplicate_count": exact_duplicate_count,
            "unique_cycle_count": unique_cycles,
            "soak_observed_days": soak_days,
            "arm_counts": arm_counts,
            "treatment_action_counts": action_counts,
            "qlib_blocked_external_count": qlib_blocked_external_count,
            "integrity_blocker_count": len(integrity_blockers),
            "integrity_blockers": list(dict.fromkeys(integrity_blockers)),
            "blocker_counts": dict(sorted(blocker_counts.items())),
        },
        "arms": {
            "CONTROL": _arm_metrics(completed_rows, "CONTROL"),
            "TREATMENT": _arm_metrics(completed_rows, "TREATMENT"),
        },
        "financial_evidence": evidence,
        "next_gate": {
            "evidence_sufficient_for_release_review": bool(
                evidence.get("evidence_sufficient_for_release_review")
            ),
            "paper_treatment_release_allowed": False,
            "paper_activation_performed": False,
            "required_future_decision": "SEPARATE_EXPLICIT_RELEASE_REVIEW",
        },
        "assignment_sample": assignments[:20],
        "safety": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
        "write_requested": False,
        "write_performed": False,
        "write_report_performed": False,
        "write_assignments_performed": False,
        "assignments_appended": 0,
    }
    return report, assignments


__all__ = [
    "ASSIGNMENT_VERSION",
    "CONTROL_DEFINITION",
    "DECISION",
    "SAFETY_FLAGS",
    "SCHEMA_VERSION",
    "TREATMENT_DEFINITION",
    "Preregistration",
    "build_preregistration",
    "evaluate_prospective_ab_soak",
    "load_preregistration",
]
