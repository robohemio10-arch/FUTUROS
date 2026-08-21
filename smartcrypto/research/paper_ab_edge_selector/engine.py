"""Research-only Paper A/B Edge Selector V1.

This module builds a deterministic, point-in-time A/B evidence harness.  It has
no operational authority and never changes Freqtrade, risk, active signals,
models or runtime state.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from smartcrypto.analysis.paper_financial_performance import compute_financial_metrics
from smartcrypto.research.financial_ai_research_engine import FinancialAIResearchEngine
from smartcrypto.research.paper_edge_foundation.foundation import (
    SourceIntegrityError,
    file_sha256,
    prepare_closed_trades,
    read_authoritative_paper_source,
)

from .assignment import assign_candidate
from .contracts import (
    DECISION,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    ABObservation,
    ArmFinancialMetrics,
    ExperimentConfig,
    IncrementalEdgeEvidence,
)
from .persistence import (
    resolve_assignments_path,
    resolve_report_path,
    write_assignments_idempotent,
    write_report,
)


DEFAULT_QLIB_SECURITY_REPORT = Path(
    "data/reports/qlib_dependency_security_hardening_v1.json"
)
TRADE_SUBJECT_PATTERN = re.compile(r"^trade:(?P<trade_id>\d+)$", re.IGNORECASE)
MINIMUM_SEGMENT_ROWS_PER_ARM = 20


class PaperABEdgeSelectorError(RuntimeError):
    """Controlled domain failure for the A/B research harness."""


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is None:
            value = value.tz_localize("UTC")
        else:
            value = value.tz_convert("UTC")
        return value.isoformat().replace("+00:00", "Z")
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _iso(value: Any) -> str | None:
    if value is None or value is pd.NaT:
        return None
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat().replace("+00:00", "Z")


def _read_qlib_security_evidence(
    root: Path,
    value: str | Path | None,
) -> dict[str, Any]:
    selected = Path(value) if value is not None else DEFAULT_QLIB_SECURITY_REPORT
    path = selected if selected.is_absolute() else root / selected
    path = path.resolve()
    if not path.exists() or not path.is_file():
        return {
            "status": "SOURCE_MISSING",
            "reason": "qlib_dependency_security_report_missing",
            "path": str(path),
            "sha256": None,
            "approved_security_clean_resolution_found": False,
            "qlib_security_gate_passed": False,
            "gate_passed": False,
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "status": "SOURCE_UNVERIFIED",
            "reason": "qlib_dependency_security_report_invalid",
            "path": str(path),
            "sha256": None,
            "approved_security_clean_resolution_found": False,
            "qlib_security_gate_passed": False,
            "gate_passed": False,
        }

    if not isinstance(payload, Mapping):
        return {
            "status": "SOURCE_UNVERIFIED",
            "reason": "qlib_dependency_security_report_not_object",
            "path": str(path),
            "sha256": file_sha256(path),
            "approved_security_clean_resolution_found": False,
            "qlib_security_gate_passed": False,
            "gate_passed": False,
        }

    status = str(payload.get("status") or "UNKNOWN")
    reason = str(payload.get("reason") or "UNKNOWN")
    approved = payload.get("approved_security_clean_resolution_found") is True
    reported_gate = payload.get("qlib_security_gate_passed") is True
    gate_passed = bool(status.lower() == "ok" and approved and reported_gate)
    return {
        "status": status,
        "reason": reason,
        "path": str(path),
        "sha256": file_sha256(path),
        "schema_version": payload.get("schema_version"),
        "decision": payload.get("decision"),
        "approved_security_clean_resolution_found": approved,
        "qlib_security_gate_passed": reported_gate,
        "gate_passed": gate_passed,
    }


def _control_frame(closed: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(index=closed.index)
    frame["trade_id"] = pd.to_numeric(closed["id"], errors="coerce").astype("Int64")
    frame["__pnl"] = pd.to_numeric(closed["close_profit_abs"], errors="coerce")
    frame["observed_at_utc"] = pd.to_datetime(closed["open_date"], utc=True, errors="coerce")
    frame["outcome_available_at_utc"] = pd.to_datetime(
        closed["close_date"], utc=True, errors="coerce"
    )
    frame["symbol"] = closed["pair"].astype(str)
    frame["side"] = closed["side"].astype(str)
    frame["duration_hours"] = pd.to_numeric(
        closed.get("duration_minutes", pd.Series(index=closed.index, dtype=float)),
        errors="coerce",
    ) / 60.0
    frame["stake_amount"] = pd.to_numeric(
        closed.get("stake_amount", pd.Series(index=closed.index, dtype=float)),
        errors="coerce",
    )
    frame["capital_hours"] = frame["stake_amount"] * frame["duration_hours"]

    fee_open = pd.to_numeric(
        closed.get("fee_open_cost", pd.Series(index=closed.index, dtype=float)),
        errors="coerce",
    )
    fee_close = pd.to_numeric(
        closed.get("fee_close_cost", pd.Series(index=closed.index, dtype=float)),
        errors="coerce",
    )
    if fee_open.notna().any() or fee_close.notna().any():
        frame["fees"] = fee_open.fillna(0.0) + fee_close.fillna(0.0)
    else:
        frame["fees"] = np.nan
    return frame.reset_index(drop=True)


def _numeric_column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")


def _optional_metrics(frame: pd.DataFrame, accepted_mask: pd.Series | None = None) -> dict[str, Any]:
    if frame.empty:
        return {}
    mask = (
        accepted_mask.reindex(frame.index, fill_value=False)
        if accepted_mask is not None
        else pd.Series(True, index=frame.index)
    )
    output: dict[str, Any] = {}

    capital = _numeric_column(frame, "capital_hours")
    if capital.notna().any():
        output["capital_hours"] = float(capital.where(mask, 0.0).fillna(0.0).sum())

    duration = _numeric_column(frame, "duration_hours")
    if duration.notna().any():
        output["time_in_market_hours"] = float(duration.where(mask, 0.0).fillna(0.0).sum())

    fees = _numeric_column(frame, "fees")
    if fees.notna().any():
        output["fees"] = float(fees.where(mask, 0.0).fillna(0.0).sum())

    return output


def _arm_metrics(frame: pd.DataFrame, arm: str) -> tuple[ArmFinancialMetrics, dict[str, Any]]:
    if frame.empty:
        raw = compute_financial_metrics(pd.DataFrame({"__pnl": pd.Series(dtype=float)}))
        return (
            ArmFinancialMetrics(
                arm=arm,
                trade_count=0,
                eligible_count=0,
                accepted_count=0,
                rejected_count=0,
                net_pnl=0.0,
                expectancy=None,
                profit_factor=None,
                win_rate=None,
                payoff_ratio=None,
                max_drawdown=None,
                optional_metrics={},
            ),
            raw,
        )

    pnl_frame = pd.DataFrame({"__pnl": pd.to_numeric(frame["effective_arm_pnl_usdt"], errors="coerce")})
    raw = compute_financial_metrics(pnl_frame)
    accepted = frame["treatment_action"].eq("ACCEPT")
    metrics = ArmFinancialMetrics(
        arm=arm,
        trade_count=int(len(frame)),
        eligible_count=int(len(frame)),
        accepted_count=int(accepted.sum()),
        rejected_count=int((~accepted).sum()),
        net_pnl=float(raw.get("total_pnl") or 0.0),
        expectancy=_finite_float(raw.get("expectancy")),
        profit_factor=_finite_float(raw.get("profit_factor")),
        win_rate=_finite_float(raw.get("win_rate")),
        payoff_ratio=_finite_float(raw.get("payoff_ratio")),
        max_drawdown=_finite_float(raw.get("max_drawdown")),
        optional_metrics=_optional_metrics(frame, accepted),
    )
    return metrics, raw


def _trade_id_from_estimate(estimate: Mapping[str, Any]) -> int | None:
    direct = estimate.get("trade_id")
    if direct is not None:
        try:
            return int(direct)
        except (TypeError, ValueError):
            return None
    subject = str(estimate.get("estimate_subject_id") or "")
    match = TRADE_SUBJECT_PATTERN.fullmatch(subject)
    return int(match.group("trade_id")) if match else None


def _observed_days(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    timestamps = pd.to_datetime(frame["observed_at_utc"], utc=True, errors="coerce").dropna()
    if len(timestamps) < 2:
        return 0.0
    return float((timestamps.max() - timestamps.min()).total_seconds() / 86_400.0)


def deterministic_bootstrap_delta_expectancy(
    observations: pd.DataFrame,
    *,
    iterations: int,
    seed: int,
    confidence_level: float,
) -> dict[str, Any]:
    """Bootstrap treatment-control expectancy delta deterministically.

    UTC day clusters are resampled when two or more days are available.  With
    only one day the function falls back to arm-stratified IID bootstrap and
    reports that method explicitly.
    """

    if observations.empty:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "method": None,
            "iterations": int(iterations),
            "successful_iterations": 0,
            "seed": int(seed),
            "confidence_level": float(confidence_level),
            "ci_lower": None,
            "ci_upper": None,
            "effective_sample": 0,
        }

    frame = observations.copy()
    frame["__pnl"] = pd.to_numeric(frame["effective_arm_pnl_usdt"], errors="coerce")
    frame["__ts"] = pd.to_datetime(frame["observed_at_utc"], utc=True, errors="coerce")
    frame = frame.loc[frame["__pnl"].notna() & frame["__ts"].notna()].copy()
    control = frame.loc[frame["arm"].eq("CONTROL"), "__pnl"].to_numpy(dtype=float)
    treatment = frame.loc[frame["arm"].eq("TREATMENT"), "__pnl"].to_numpy(dtype=float)
    effective_sample = min(len(control), len(treatment))
    if not len(control) or not len(treatment):
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "method": None,
            "iterations": int(iterations),
            "successful_iterations": 0,
            "seed": int(seed),
            "confidence_level": float(confidence_level),
            "ci_lower": None,
            "ci_upper": None,
            "effective_sample": int(effective_sample),
        }

    rng = np.random.default_rng(int(seed))
    deltas: list[float] = []
    frame["__day"] = frame["__ts"].dt.strftime("%Y-%m-%d")
    days = sorted(frame["__day"].unique().tolist())

    if len(days) >= 2:
        method = "temporal_cluster_day_bootstrap"
        for _ in range(int(iterations)):
            selected_days = rng.choice(days, size=len(days), replace=True)
            sampled = pd.concat(
                [frame.loc[frame["__day"].eq(day)] for day in selected_days],
                ignore_index=True,
            )
            c = sampled.loc[sampled["arm"].eq("CONTROL"), "__pnl"]
            t = sampled.loc[sampled["arm"].eq("TREATMENT"), "__pnl"]
            if c.empty or t.empty:
                continue
            deltas.append(float(t.mean() - c.mean()))
    else:
        method = "arm_stratified_iid_bootstrap_fallback"
        for _ in range(int(iterations)):
            c = rng.choice(control, size=len(control), replace=True)
            t = rng.choice(treatment, size=len(treatment), replace=True)
            deltas.append(float(np.mean(t) - np.mean(c)))

    if not deltas:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "method": method,
            "iterations": int(iterations),
            "successful_iterations": 0,
            "seed": int(seed),
            "confidence_level": float(confidence_level),
            "ci_lower": None,
            "ci_upper": None,
            "effective_sample": int(effective_sample),
        }

    alpha = (1.0 - float(confidence_level)) / 2.0
    return {
        "status": "AVAILABLE",
        "method": method,
        "iterations": int(iterations),
        "successful_iterations": int(len(deltas)),
        "seed": int(seed),
        "confidence_level": float(confidence_level),
        "ci_lower": float(np.quantile(deltas, alpha)),
        "ci_upper": float(np.quantile(deltas, 1.0 - alpha)),
        "effective_sample": int(effective_sample),
    }


def _delta(a: float | None, b: float | None) -> float | None:
    return float(a - b) if a is not None and b is not None else None


def _financial_evidence(
    observations: pd.DataFrame,
    *,
    control_metrics: ArmFinancialMetrics,
    control_raw: Mapping[str, Any],
    treatment_metrics: ArmFinancialMetrics,
    treatment_raw: Mapping[str, Any],
    config: ExperimentConfig,
    global_blockers: Sequence[str],
) -> IncrementalEdgeEvidence:
    control_count = int(control_metrics.trade_count)
    treatment_count = int(treatment_metrics.trade_count)
    observed_days = _observed_days(observations)
    sample_gate = bool(
        control_count >= config.minimum_observations_per_arm
        and treatment_count >= config.minimum_observations_per_arm
    )
    period_gate = bool(observed_days >= config.minimum_observation_days)

    if global_blockers or treatment_count == 0:
        blockers = tuple(dict.fromkeys([*global_blockers, "NO_EVALUABLE_TREATMENT_OBSERVATIONS"]))
        return IncrementalEdgeEvidence(
            status="EVIDENCE_BLOCKED",
            reason=blockers[0],
            treatment_evaluable=False,
            eligible_treatment_count=treatment_count,
            control_count=control_count,
            treatment_count=treatment_count,
            observed_days=observed_days,
            sample_gate_passed=sample_gate,
            period_gate_passed=period_gate,
            delta_net_pnl=None,
            delta_expectancy=None,
            delta_profit_factor=None,
            delta_max_drawdown=None,
            expectancy_ci_lower=None,
            expectancy_ci_upper=None,
            confidence_level=config.confidence_level,
            bootstrap_iterations=config.bootstrap_iterations,
            bootstrap_seed=config.bootstrap_seed,
            bootstrap_method=None,
            effective_sample=min(control_count, treatment_count),
            treatment_profit_factor_gate=False,
            edge_ci_gate=False,
            blockers=blockers,
        )

    delta_net = _delta(treatment_metrics.net_pnl, control_metrics.net_pnl)
    delta_exp = _delta(treatment_metrics.expectancy, control_metrics.expectancy)
    delta_pf = _delta(treatment_metrics.profit_factor, control_metrics.profit_factor)
    delta_dd = _delta(treatment_metrics.max_drawdown, control_metrics.max_drawdown)

    if not sample_gate or not period_gate:
        blockers: list[str] = []
        if not sample_gate:
            blockers.append("MINIMUM_OBSERVATIONS_PER_ARM_NOT_MET")
        if not period_gate:
            blockers.append("MINIMUM_OBSERVATION_DAYS_NOT_MET")
        return IncrementalEdgeEvidence(
            status="INSUFFICIENT_SAMPLE",
            reason=blockers[0],
            treatment_evaluable=True,
            eligible_treatment_count=treatment_count,
            control_count=control_count,
            treatment_count=treatment_count,
            observed_days=observed_days,
            sample_gate_passed=sample_gate,
            period_gate_passed=period_gate,
            delta_net_pnl=delta_net,
            delta_expectancy=delta_exp,
            delta_profit_factor=delta_pf,
            delta_max_drawdown=delta_dd,
            expectancy_ci_lower=None,
            expectancy_ci_upper=None,
            confidence_level=config.confidence_level,
            bootstrap_iterations=config.bootstrap_iterations,
            bootstrap_seed=config.bootstrap_seed,
            bootstrap_method=None,
            effective_sample=min(control_count, treatment_count),
            treatment_profit_factor_gate=False,
            edge_ci_gate=False,
            blockers=tuple(blockers),
        )

    bootstrap = deterministic_bootstrap_delta_expectancy(
        observations,
        iterations=config.bootstrap_iterations,
        seed=config.bootstrap_seed,
        confidence_level=config.confidence_level,
    )
    ci_lower = _finite_float(bootstrap.get("ci_lower"))
    ci_upper = _finite_float(bootstrap.get("ci_upper"))
    treatment_pf = treatment_metrics.profit_factor
    treatment_pf_gate = bool(
        treatment_pf is not None and treatment_pf >= config.minimum_profit_factor
    )
    edge_ci_gate = bool(ci_lower is not None and ci_lower > 0.0)

    blockers: list[str] = []
    if bootstrap.get("status") != "AVAILABLE":
        blockers.append("BOOTSTRAP_CI_UNAVAILABLE")
    if not treatment_pf_gate:
        blockers.append("TREATMENT_PROFIT_FACTOR_GATE_FAILED")
    if not edge_ci_gate:
        blockers.append("DELTA_EXPECTANCY_CI_NOT_STRICTLY_POSITIVE")

    if edge_ci_gate and treatment_pf_gate:
        status = "INCREMENTAL_EDGE_RESEARCH_ONLY"
        reason = "incremental_edge_research_evidence_only"
    elif delta_exp is not None and delta_exp > 0.0 and ci_upper is not None and ci_upper > 0.0:
        status = "PROMISING_NOT_PROVEN"
        reason = blockers[0] if blockers else "positive_point_estimate_not_proven"
    else:
        status = "NO_INCREMENTAL_EDGE"
        reason = blockers[0] if blockers else "non_positive_incremental_expectancy"

    return IncrementalEdgeEvidence(
        status=status,
        reason=reason,
        treatment_evaluable=True,
        eligible_treatment_count=treatment_count,
        control_count=control_count,
        treatment_count=treatment_count,
        observed_days=observed_days,
        sample_gate_passed=sample_gate,
        period_gate_passed=period_gate,
        delta_net_pnl=delta_net,
        delta_expectancy=delta_exp,
        delta_profit_factor=delta_pf,
        delta_max_drawdown=delta_dd,
        expectancy_ci_lower=ci_lower,
        expectancy_ci_upper=ci_upper,
        confidence_level=config.confidence_level,
        bootstrap_iterations=int(bootstrap["iterations"]),
        bootstrap_seed=int(bootstrap["seed"]),
        bootstrap_method=str(bootstrap.get("method")) if bootstrap.get("method") else None,
        effective_sample=int(bootstrap.get("effective_sample", 0)),
        treatment_profit_factor_gate=treatment_pf_gate,
        edge_ci_gate=edge_ci_gate,
        blockers=tuple(blockers),
    )


def _segment_summary(observations: pd.DataFrame) -> dict[str, Any]:
    if observations.empty:
        return {}

    frame = observations.copy()
    frame["observed_at_utc"] = pd.to_datetime(frame["observed_at_utc"], utc=True, errors="coerce")
    frame["time_window"] = frame["observed_at_utc"].dt.strftime("%Y-%m")

    candidate_ev = pd.to_numeric(frame["candidate_ev"], errors="coerce")
    if candidate_ev.notna().sum() >= 4:
        try:
            ranked = candidate_ev.rank(method="first")
            frame["score_bucket"] = pd.qcut(
                ranked,
                q=4,
                labels=["Q1", "Q2", "Q3", "Q4"],
                duplicates="drop",
            ).astype(str)
        except ValueError:
            frame["score_bucket"] = "UNAVAILABLE"
    else:
        frame["score_bucket"] = "UNAVAILABLE"

    dimensions = ("symbol", "side", "regime", "score_bucket", "time_window")
    output: dict[str, Any] = {}
    for dimension in dimensions:
        if dimension not in frame.columns or frame[dimension].isna().all():
            output[dimension] = []
            continue
        rows: list[dict[str, Any]] = []
        for value, group in frame.groupby(dimension, dropna=False, sort=True):
            control = group.loc[group["arm"].eq("CONTROL")]
            treatment = group.loc[group["arm"].eq("TREATMENT")]
            if (
                len(control) < MINIMUM_SEGMENT_ROWS_PER_ARM
                or len(treatment) < MINIMUM_SEGMENT_ROWS_PER_ARM
            ):
                rows.append(
                    {
                        "segment": "UNKNOWN" if pd.isna(value) else str(value),
                        "status": "INSUFFICIENT_SAMPLE",
                        "control_count": int(len(control)),
                        "treatment_count": int(len(treatment)),
                    }
                )
                continue
            control_metrics, _ = _arm_metrics(control, "CONTROL")
            treatment_metrics, _ = _arm_metrics(treatment, "TREATMENT")
            rows.append(
                {
                    "segment": "UNKNOWN" if pd.isna(value) else str(value),
                    "status": "ok",
                    "control": control_metrics.to_dict(),
                    "treatment": treatment_metrics.to_dict(),
                    "delta_expectancy": _delta(
                        treatment_metrics.expectancy, control_metrics.expectancy
                    ),
                    "delta_net_pnl": _delta(
                        treatment_metrics.net_pnl, control_metrics.net_pnl
                    ),
                }
            )
        output[dimension] = rows
    return output


def _global_blockers(
    financial_report: Mapping[str, Any],
    security_evidence: Mapping[str, Any],
    assignment_records: Sequence[Mapping[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    gates = financial_report.get("gates", {})
    for gate in (
        "candidate_ev_ready",
        "regression_quality_gate",
        "classification_quality_gate",
        "calibration_gate",
        "monotonicity_gate",
        "drift_gate",
        "qlib_lineage_gate",
        "trader_master_linkage_gate",
    ):
        if not isinstance(gates, Mapping) or gates.get(gate) is not True:
            blockers.append(f"GLOBAL_GATE_FALSE:{gate}")

    if security_evidence.get("gate_passed") is not True:
        blockers.append(
            "QLIB_DEPENDENCY_SECURITY_BLOCKED:"
            + str(security_evidence.get("reason") or "UNKNOWN")
        )

    if int(financial_report.get("dataset", {}).get("candidate_linked_row_count", 0)) <= 0:
        blockers.append("CANDIDATE_LINKED_ROWS_ZERO")

    if not any(record.get("status") == "ASSIGNED" for record in assignment_records):
        blockers.append("NO_ELIGIBLE_CANDIDATE_ASSIGNMENTS")
    return list(dict.fromkeys(blockers))


class PaperABEdgeSelectorEngine:
    """Build A/B evidence without operational authority or state mutation."""

    def __init__(self, config: ExperimentConfig) -> None:
        self.config = config

    def run(
        self,
        *,
        project_root: str | Path,
        paper_db: str | Path,
        feature_source: str | Path | None = None,
        qlib_source: str | Path | None = None,
        regime_source: str | Path | None = None,
        trader_master_source: str | Path | None = None,
        execution_cost_source: str | Path | None = None,
        qlib_security_report: str | Path | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        root = Path(project_root).resolve()
        paper = read_authoritative_paper_source(paper_db)
        closed, paper_counts = prepare_closed_trades(paper["trades"])
        baseline_frame = _control_frame(closed)
        baseline_raw = compute_financial_metrics(baseline_frame[["__pnl"]])

        financial_report, estimates = FinancialAIResearchEngine().run(
            project_root=root,
            paper_db=paper_db,
            feature_source=feature_source,
            qlib_source=qlib_source,
            regime_source=regime_source,
            trader_master_source=trader_master_source,
            execution_cost_source=execution_cost_source,
        )
        security = _read_qlib_security_evidence(root, qlib_security_report)
        gates = financial_report.get("gates", {})
        if not isinstance(gates, Mapping):
            gates = {}

        outcomes = {
            int(row["trade_id"]): row.to_dict()
            for _, row in baseline_frame.iterrows()
            if pd.notna(row["trade_id"])
        }

        assignment_objects = [
            assign_candidate(
                self.config,
                estimate,
                global_gates=gates,
                qlib_security_evidence=security,
            )
            for estimate in estimates
        ]
        assignment_records = [item.to_dict() for item in assignment_objects]
        assigned_records = [
            item.to_dict() for item in assignment_objects if item.status == "ASSIGNED"
        ]

        observations: list[ABObservation] = []
        outcome_blockers = Counter()
        for assignment, estimate in zip(assignment_objects, estimates):
            if assignment.status != "ASSIGNED" or assignment.assignment_id is None or assignment.arm is None:
                continue
            trade_id = _trade_id_from_estimate(estimate)
            if trade_id is None:
                outcome_blockers["OUTCOME_TRADE_ID_UNAVAILABLE"] += 1
                continue
            outcome = outcomes.get(trade_id)
            if outcome is None:
                outcome_blockers["OUTCOME_NOT_FOUND_IN_AUTHORITATIVE_PAPER"] += 1
                continue
            observed_at = _iso(assignment.observed_at_utc)
            outcome_at = _iso(outcome.get("outcome_available_at_utc"))
            if observed_at is None or outcome_at is None:
                outcome_blockers["OUTCOME_LINEAGE_TIMESTAMP_INVALID"] += 1
                continue
            if pd.Timestamp(outcome_at) <= pd.Timestamp(observed_at):
                outcome_blockers["OUTCOME_NOT_STRICTLY_AFTER_ASSIGNMENT"] += 1
                continue
            realized = _finite_float(outcome.get("__pnl"))
            candidate_ev = _finite_float(assignment.candidate_ev)
            if realized is None or candidate_ev is None:
                outcome_blockers["NON_FINITE_OUTCOME_OR_CANDIDATE_EV"] += 1
                continue

            if assignment.arm == "CONTROL":
                action = "ACCEPT"
                effective_pnl = realized
            else:
                action = "ACCEPT" if candidate_ev > self.config.treatment_ev_threshold else "REJECT"
                effective_pnl = realized if action == "ACCEPT" else 0.0

            observations.append(
                ABObservation(
                    assignment_id=assignment.assignment_id,
                    candidate_id=str(assignment.candidate_id),
                    estimate_id=str(estimate.get("estimate_id")) if estimate.get("estimate_id") else None,
                    estimate_subject_id=(
                        str(estimate.get("estimate_subject_id"))
                        if estimate.get("estimate_subject_id")
                        else None
                    ),
                    arm=assignment.arm,
                    observed_at_utc=observed_at,
                    symbol=str(outcome.get("symbol") or "") or None,
                    side=str(outcome.get("side") or "") or None,
                    regime=(str(estimate.get("regime")) if estimate.get("regime") is not None else None),
                    candidate_ev=candidate_ev,
                    treatment_action=action,
                    trade_id=trade_id,
                    outcome_available_at_utc=outcome_at,
                    realized_net_pnl_usdt=realized,
                    effective_arm_pnl_usdt=effective_pnl,
                    capital_hours=_finite_float(outcome.get("capital_hours")),
                    duration_hours=_finite_float(outcome.get("duration_hours")),
                    fees=_finite_float(outcome.get("fees")),
                )
            )

        observation_rows = [item.to_dict() for item in observations]
        observation_frame = pd.DataFrame(observation_rows)
        if observation_frame.empty:
            observation_frame = pd.DataFrame(
                columns=[
                    "assignment_id",
                    "candidate_id",
                    "estimate_id",
                    "estimate_subject_id",
                    "arm",
                    "observed_at_utc",
                    "symbol",
                    "side",
                    "regime",
                    "candidate_ev",
                    "treatment_action",
                    "trade_id",
                    "outcome_available_at_utc",
                    "realized_net_pnl_usdt",
                    "effective_arm_pnl_usdt",
                    "capital_hours",
                    "duration_hours",
                    "fees",
                ]
            )

        control_obs = observation_frame.loc[observation_frame["arm"].eq("CONTROL")].copy()
        treatment_obs = observation_frame.loc[observation_frame["arm"].eq("TREATMENT")].copy()
        control_metrics, control_raw = _arm_metrics(control_obs, "CONTROL")
        treatment_metrics, treatment_raw = _arm_metrics(treatment_obs, "TREATMENT")

        global_blockers = _global_blockers(financial_report, security, assignment_records)
        global_blockers.extend(
            f"{key}:{count}" for key, count in sorted(outcome_blockers.items())
        )
        global_blockers = list(dict.fromkeys(global_blockers))

        evidence = _financial_evidence(
            observation_frame,
            control_metrics=control_metrics,
            control_raw=control_raw,
            treatment_metrics=treatment_metrics,
            treatment_raw=treatment_raw,
            config=self.config,
            global_blockers=global_blockers,
        )

        candidate_linked_rows = int(
            financial_report.get("dataset", {}).get("candidate_linked_row_count", 0)
        )
        eligible_control_count = int(
            sum(item.status == "ASSIGNED" and item.arm == "CONTROL" for item in assignment_objects)
        )
        eligible_treatment_count = int(
            sum(item.status == "ASSIGNED" and item.arm == "TREATMENT" for item in assignment_objects)
        )
        blocker_counts = Counter(
            blocker
            for record in assignment_objects
            for blocker in record.blockers
        )

        report_status = "BLOCKED" if evidence.status == "EVIDENCE_BLOCKED" else "PARTIAL"
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": report_status,
            "reason": evidence.reason,
            "decision": DECISION,
            "experiment": self.config.to_dict(),
            "design": {
                "method": "OFFLINE_POINT_IN_TIME_AB_REPLAY",
                "assignment_material": "experiment_id|candidate_id",
                "assignment_hash": "SHA256",
                "allocation": "50_50",
                "control_action": "BASELINE_ACCEPT_OBSERVED_PAPER_TRADE",
                "treatment_action": "ACCEPT_IF_CANDIDATE_EV_GT_THRESHOLD_ELSE_REJECT",
                "rejected_treatment_effective_pnl": 0.0,
                "causal_claim_allowed": False,
                "retrospective_outcome_used_for_assignment": False,
                "trade_id_used_as_candidate_id": False,
            },
            "sources": {
                "paper_db": {
                    "path": str(paper["path"]),
                    "sha256_before": paper["sha256_before"],
                    "sha256_after": paper["sha256_after"],
                    "source_hash_invariant": paper["sha256_before"] == paper["sha256_after"],
                    "sqlite_integrity_check": paper["sqlite_integrity_check"],
                    **paper_counts,
                },
                "financial_ai": financial_report.get("sources", {}),
                "qlib_dependency_security": security,
            },
            "paper_baseline": {
                "pnl_authority": "FREQTRADE_CLOSE_PROFIT_ABS",
                "open_trades_excluded_from_outcomes": True,
                "closed_trade_count": int(len(closed)),
                "metrics": {
                    "trade_count": int(baseline_raw.get("trades", 0)),
                    "net_pnl": float(baseline_raw.get("total_pnl") or 0.0),
                    "expectancy": _finite_float(baseline_raw.get("expectancy")),
                    "profit_factor": _finite_float(baseline_raw.get("profit_factor")),
                    "win_rate": _finite_float(baseline_raw.get("win_rate")),
                    "payoff_ratio": _finite_float(baseline_raw.get("payoff_ratio")),
                    "max_drawdown": _finite_float(baseline_raw.get("max_drawdown")),
                    "optional_metrics": _optional_metrics(baseline_frame),
                },
            },
            "financial_ai": {
                "status": financial_report.get("status"),
                "reason": financial_report.get("reason"),
                "decision": financial_report.get("decision"),
                "blockers": list(financial_report.get("blockers", [])),
                "gates": dict(gates),
                "candidate_estimate_count": int(
                    financial_report.get("candidate_estimates", {}).get("estimate_count", len(estimates))
                ),
                "trusted_estimate_count": int(
                    financial_report.get("candidate_estimates", {}).get("trusted_estimate_count", 0)
                ),
                "candidate_ev_generated_count": int(
                    financial_report.get("candidate_estimates", {}).get(
                        "candidate_ev_generated_count", 0
                    )
                ),
                "candidate_ev_blocked_count": int(
                    financial_report.get("candidate_estimates", {}).get(
                        "candidate_ev_blocked_count", len(estimates)
                    )
                ),
            },
            "candidate_linked_rows": candidate_linked_rows,
            "assignment": {
                "estimate_count": int(len(estimates)),
                "assigned_count": int(len(assigned_records)),
                "eligible_control_count": eligible_control_count,
                "eligible_treatment_count": eligible_treatment_count,
                "ineligible_count": int(len(estimates) - len(assigned_records)),
                "blocker_counts": dict(sorted(blocker_counts.items())),
                "outcome_blocker_counts": dict(sorted(outcome_blockers.items())),
                "persistable_assignment_count": int(len(assigned_records)),
            },
            "eligible_treatment_count": eligible_treatment_count,
            "treatment_evaluable": bool(evidence.treatment_evaluable),
            "arms": {
                "CONTROL": control_metrics.to_dict(),
                "TREATMENT": treatment_metrics.to_dict(),
            },
            "financial_evidence": evidence.to_dict(),
            "segments": _segment_summary(observation_frame),
            "software_dod": {
                "status": "PASS",
                "deterministic_assignment": True,
                "point_in_time_contract": True,
                "authoritative_pnl_contract": True,
                "research_only_contract": True,
                "financial_evidence_can_fail_independently": True,
            },
            "treatment_release": {
                "status": "BLOCKED",
                "allowed": False,
                "reason": "RESEARCH_ONLY_NO_OPERATIONAL_AUTHORITY",
            },
            "treatment_release_allowed": False,
            "safety": dict(SAFETY_FLAGS),
            **SAFETY_FLAGS,
            "write_requested": False,
            "write_performed": False,
            "write_report_performed": False,
            "write_assignments_performed": False,
            "assignments_appended": 0,
        }
        return _json_safe(report), [_json_safe(row) for row in assigned_records]


def _controlled_failure_report(
    *,
    reason: str,
    detail: str | None,
    config: ExperimentConfig,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED",
        "reason": reason,
        "decision": DECISION,
        "experiment": config.to_dict(),
        "candidate_linked_rows": 0,
        "eligible_treatment_count": 0,
        "treatment_evaluable": False,
        "financial_evidence": {
            "status": "EVIDENCE_BLOCKED",
            "reason": reason,
            "treatment_evaluable": False,
            "eligible_treatment_count": 0,
            "blockers": [reason],
        },
        "software_dod": {
            "status": "BLOCKED",
            "reason": reason,
            "error_detail": (detail or "")[:512],
        },
        "treatment_release": {
            "status": "BLOCKED",
            "allowed": False,
            "reason": "RESEARCH_ONLY_NO_OPERATIONAL_AUTHORITY",
        },
        "treatment_release_allowed": False,
        "safety": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
        "write_requested": False,
        "write_performed": False,
        "write_report_performed": False,
        "write_assignments_performed": False,
        "assignments_appended": 0,
    }


def build_paper_ab_edge_selector_v1(
    *,
    project_root: str | Path,
    paper_db: str | Path,
    experiment_id: str,
    feature_source: str | Path | None = None,
    qlib_source: str | Path | None = None,
    regime_source: str | Path | None = None,
    trader_master_source: str | Path | None = None,
    execution_cost_source: str | Path | None = None,
    qlib_security_report: str | Path | None = None,
    minimum_observations_per_arm: int = 200,
    minimum_observation_days: int = 45,
    minimum_profit_factor: float = 1.10,
    bootstrap_iterations: int = 5000,
    bootstrap_seed: int = 20260820,
    confidence_level: float = 0.95,
    write_report_requested: bool = False,
    write_assignments_requested: bool = False,
    output_report: str | Path | None = None,
    output_assignments: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    config = ExperimentConfig(
        experiment_id=experiment_id,
        minimum_observations_per_arm=minimum_observations_per_arm,
        minimum_observation_days=minimum_observation_days,
        minimum_profit_factor=minimum_profit_factor,
        bootstrap_iterations=bootstrap_iterations,
        bootstrap_seed=bootstrap_seed,
        confidence_level=confidence_level,
    )
    report_path = resolve_report_path(root, output_report)
    assignments_path = resolve_assignments_path(root, output_assignments)

    try:
        report, assignments = PaperABEdgeSelectorEngine(config).run(
            project_root=root,
            paper_db=paper_db,
            feature_source=feature_source,
            qlib_source=qlib_source,
            regime_source=regime_source,
            trader_master_source=trader_master_source,
            execution_cost_source=execution_cost_source,
            qlib_security_report=qlib_security_report,
        )
    except (SourceIntegrityError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        report = _controlled_failure_report(
            reason=getattr(exc, "reason", type(exc).__name__),
            detail=str(exc),
            config=config,
        )
        assignments = []

    report["output_report"] = str(report_path)
    report["output_assignments"] = str(assignments_path)
    report["write_requested"] = bool(
        write_report_requested or write_assignments_requested
    )

    if write_assignments_requested:
        appended = write_assignments_idempotent(root, assignments_path, assignments)
        report["assignments_appended"] = int(appended)
        report["write_assignments_performed"] = bool(appended > 0)
        report["write_performed"] = bool(appended > 0)

    if write_report_requested:
        report["write_report_performed"] = True
        report["write_performed"] = True
        write_report(root, report_path, _json_safe(report))

    return _json_safe(report)
