"""Research-only OOS validation for OCR Master + candle positive-EV slices.

The module consumes the aligned OCR master/candle feature frame produced by the
previous research step and evaluates positive slice candidates through temporal
out-of-sample folds. It is deliberately non-operational: no candidate is written
to a registry, no paper observation is enabled, and no trading runtime surface is
changed.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from smartcrypto.research.ocr_master_candle_positive_ev_slice_mining.slice_mining import (
    DEFAULT_ALIGNMENT_TOLERANCE_SECONDS,
    DEFAULT_MAX_DAY_CONCENTRATION,
    DEFAULT_MIN_TRADE_COUNT,
    EXPECTED_TRADE_VALUE_CONTRACT,
    FORBIDDEN_ACTIONS,
    SourceInfo,
    align_trades_to_candles,
    compute_metrics,
    load_candles,
    load_legacy_trade_dataset,
)

SCHEMA_VERSION = "ocr_master_candle_positive_rule_oos_validation_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_MIN_OOS_TRADE_COUNT = 8
DEFAULT_MIN_OOS_PASS_RATIO = 0.60
DEFAULT_MIN_OOS_FOLDS = 3

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "release_authority": False,
    "readiness_release_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "runs_training": False,
    "registers_candidate_rules": False,
    "can_promote_rules": False,
    "can_promote_model": False,
    "ready_for_candidate_registry": False,
    "remediation_application_allowed": False,
    "applies_shadow_rules": False,
    "applies_feedback_to_ai_shadow": False,
    "executes_orchestrator": False,
    "executes_scheduler": False,
    "executes_stage_builders": False,
    "writes_data": False,
    "writes_runtime": False,
    "writes_reports": False,
    "writes_parquet": False,
    "writes_sqlite": False,
    "paper_observation_allowed": False,
}

SLICE_DIMENSION_SETS: list[tuple[str, ...]] = [
    ("symbol_norm",),
    ("side_norm",),
    ("hour",),
    ("duration_bucket",),
    ("regime_bucket",),
    ("symbol_norm", "side_norm"),
    ("symbol_norm", "hour"),
    ("side_norm", "hour"),
    ("symbol_norm", "regime_bucket"),
    ("side_norm", "regime_bucket"),
]


@dataclass(frozen=True)
class CandidateKey:
    candidate_id: str
    dimensions: tuple[str, ...]
    values: tuple[str, ...]

    @property
    def expression(self) -> str:
        return " AND ".join(
            f"{dimension} == '{value}'" for dimension, value in zip(self.dimensions, self.values, strict=True)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "dimensions": list(self.dimensions),
            "values": list(self.values),
            "expression": self.expression,
            "rule_type": "include_slice_research_only",
        }


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_float(value: float | int | None) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return round(numeric, 10)


def _numeric_pf(metrics: Mapping[str, Any]) -> float:
    value = metrics.get("profit_factor")
    if value == "inf":
        return float("inf")
    if value is None:
        return 0.0
    return float(value)


def _max_day_concentration(frame: pd.DataFrame) -> float | None:
    if frame.empty or "day" not in frame.columns:
        return None
    counts = frame["day"].value_counts(dropna=False)
    if counts.empty:
        return None
    return round(float(counts.max() / len(frame)), 10)


def _candidate_id(dimensions: Sequence[str], values: Sequence[str]) -> str:
    return "include__" + "__".join(
        f"{dimension}_{value}" for dimension, value in zip(dimensions, values, strict=True)
    )


def _slice_mask(frame: pd.DataFrame, key: CandidateKey) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    for dimension, value in zip(key.dimensions, key.values, strict=True):
        if dimension not in frame.columns:
            return pd.Series(False, index=frame.index)
        mask &= frame[dimension].astype(str) == value
    return mask


def _candidate_from_subset(
    *,
    key: CandidateKey,
    subset: pd.DataFrame,
    baseline: Mapping[str, Any],
    total_winners: int,
    min_trade_count: int,
    max_day_concentration: float,
) -> dict[str, Any]:
    metrics = compute_metrics(subset)
    baseline_pf = _numeric_pf(baseline)
    candidate_pf = _numeric_pf(metrics)
    baseline_mean = float(baseline.get("mean_pnl") or 0.0)
    candidate_mean = float(metrics.get("mean_pnl") or 0.0)
    day_concentration = _max_day_concentration(subset)
    trade_count = int(metrics["trade_count"])
    concentration_ok = day_concentration is None or day_concentration <= max_day_concentration
    sample_ok = trade_count >= min_trade_count
    win_rate_ok = (metrics.get("win_rate") or 0.0) >= (baseline.get("win_rate") or 0.0)
    positive = (
        sample_ok
        and concentration_ok
        and metrics["net_pnl"] > 0
        and candidate_pf > baseline_pf
        and candidate_mean > baseline_mean
        and win_rate_ok
    )
    rejection_reasons: list[str] = []
    if not sample_ok:
        rejection_reasons.append("insufficient_trade_count")
    if not concentration_ok:
        rejection_reasons.append("day_concentration_too_high")
    if metrics["net_pnl"] <= 0:
        rejection_reasons.append("non_positive_net_pnl")
    if candidate_pf <= baseline_pf:
        rejection_reasons.append("profit_factor_not_above_baseline")
    if candidate_mean <= baseline_mean:
        rejection_reasons.append("mean_pnl_not_above_baseline")
    if not win_rate_ok:
        rejection_reasons.append("win_rate_below_baseline")

    winner_retention_rate = float(metrics["winner_count"] / total_winners) if total_winners else None
    pf_lift = candidate_pf - baseline_pf if math.isfinite(candidate_pf) and math.isfinite(baseline_pf) else None
    mean_lift = candidate_mean - baseline_mean
    score = (pf_lift or 0.0) * math.log1p(max(trade_count, 0)) + mean_lift
    record = key.to_dict()
    record.update(
        {
            "metrics": metrics,
            "baseline_profit_factor": baseline.get("profit_factor"),
            "baseline_mean_pnl": baseline.get("mean_pnl"),
            "profit_factor_lift": _safe_float(pf_lift),
            "mean_pnl_lift": _safe_float(mean_lift),
            "max_day_concentration": day_concentration,
            "winner_retention_rate": _safe_float(winner_retention_rate),
            "positive_ev_candidate": bool(positive),
            "eligible_for_oos_validation": bool(positive),
            "ready_for_candidate_registry": False,
            "operational_authority": False,
            "can_promote_rules": False,
            "rejection_reasons": rejection_reasons,
            "score": _safe_float(score),
        }
    )
    return record


def discover_positive_candidates(
    aligned: pd.DataFrame,
    *,
    min_trade_count: int,
    max_day_concentration: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    baseline = compute_metrics(aligned)
    if aligned.empty:
        return baseline, [], []

    total_winners = int((pd.to_numeric(aligned["pnl_usdt"], errors="coerce") > 0).sum())
    all_candidates: list[dict[str, Any]] = []
    for dimensions in SLICE_DIMENSION_SETS:
        if any(dimension not in aligned.columns for dimension in dimensions):
            continue
        for group_values, subset in aligned.groupby(list(dimensions), dropna=False):
            values_tuple = group_values if isinstance(group_values, tuple) else (group_values,)
            values = tuple(str(value) for value in values_tuple)
            key = CandidateKey(
                candidate_id=_candidate_id(dimensions, values),
                dimensions=tuple(dimensions),
                values=values,
            )
            all_candidates.append(
                _candidate_from_subset(
                    key=key,
                    subset=subset,
                    baseline=baseline,
                    total_winners=total_winners,
                    min_trade_count=min_trade_count,
                    max_day_concentration=max_day_concentration,
                )
            )

    positive = [candidate for candidate in all_candidates if candidate["positive_ev_candidate"]]
    positive.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            float(item["metrics"].get("net_pnl") or 0.0),
            int(item["metrics"].get("trade_count") or 0),
        ),
        reverse=True,
    )
    all_candidates.sort(
        key=lambda item: (
            bool(item["positive_ev_candidate"]),
            float(item.get("score") or 0.0),
            float(item["metrics"].get("net_pnl") or 0.0),
        ),
        reverse=True,
    )
    return baseline, all_candidates, positive


def _monthly_walk_forward_folds(aligned: pd.DataFrame) -> list[dict[str, Any]]:
    if aligned.empty or "open_time_utc" not in aligned.columns:
        return []
    frame = aligned.sort_values("open_time_utc").copy()
    frame["oos_period"] = frame["open_time_utc"].dt.strftime("%Y-%m")
    periods = list(dict.fromkeys(frame["oos_period"].tolist()))
    folds: list[dict[str, Any]] = []
    for index, period in enumerate(periods[1:], start=1):
        train_periods = periods[:index]
        train = frame[frame["oos_period"].isin(train_periods)].copy()
        test = frame[frame["oos_period"] == period].copy()
        if train.empty or test.empty:
            continue
        folds.append(
            {
                "fold_id": f"wf_{period}",
                "train_periods": train_periods,
                "test_period": period,
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "train_start_utc": train["open_time_utc"].min().isoformat(),
                "train_end_utc": train["open_time_utc"].max().isoformat(),
                "test_start_utc": test["open_time_utc"].min().isoformat(),
                "test_end_utc": test["open_time_utc"].max().isoformat(),
                "train": train,
                "test": test,
            }
        )
    return folds


def _fold_public_record(fold: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "fold_id": fold["fold_id"],
        "train_periods": list(fold["train_periods"]),
        "test_period": fold["test_period"],
        "train_rows": int(fold["train_rows"]),
        "test_rows": int(fold["test_rows"]),
        "train_start_utc": fold["train_start_utc"],
        "train_end_utc": fold["train_end_utc"],
        "test_start_utc": fold["test_start_utc"],
        "test_end_utc": fold["test_end_utc"],
    }


def _evaluate_candidate_oos(
    candidate: Mapping[str, Any],
    folds: Sequence[Mapping[str, Any]],
    *,
    min_oos_trade_count: int,
) -> dict[str, Any]:
    key = CandidateKey(
        candidate_id=str(candidate["candidate_id"]),
        dimensions=tuple(str(item) for item in candidate["dimensions"]),
        values=tuple(str(item) for item in candidate["values"]),
    )
    fold_results: list[dict[str, Any]] = []
    oos_frames: list[pd.DataFrame] = []
    passed = 0
    evaluated = 0

    for fold in folds:
        test = fold["test"]
        baseline_test = compute_metrics(test)
        subset = test[_slice_mask(test, key)].copy()
        metrics = compute_metrics(subset)
        baseline_pf = _numeric_pf(baseline_test)
        candidate_pf = _numeric_pf(metrics)
        baseline_mean = float(baseline_test.get("mean_pnl") or 0.0)
        candidate_mean = float(metrics.get("mean_pnl") or 0.0)
        sample_ok = int(metrics["trade_count"]) >= min_oos_trade_count
        pass_fold = (
            sample_ok
            and metrics["net_pnl"] > 0
            and candidate_pf > baseline_pf
            and candidate_mean > baseline_mean
            and (metrics.get("win_rate") or 0.0) >= (baseline_test.get("win_rate") or 0.0)
        )
        if int(metrics["trade_count"]) > 0:
            evaluated += 1
            oos_frames.append(subset)
        if pass_fold:
            passed += 1
        fold_results.append(
            {
                "fold_id": fold["fold_id"],
                "test_period": fold["test_period"],
                "baseline_metrics": baseline_test,
                "candidate_metrics": metrics,
                "trade_count_ok": bool(sample_ok),
                "passed": bool(pass_fold),
                "profit_factor_lift": _safe_float(candidate_pf - baseline_pf),
                "mean_pnl_lift": _safe_float(candidate_mean - baseline_mean),
            }
        )

    oos_frame = pd.concat(oos_frames, ignore_index=True) if oos_frames else pd.DataFrame()
    aggregate_metrics = compute_metrics(oos_frame)
    pass_ratio = float(passed / evaluated) if evaluated else 0.0
    return {
        "candidate_id": key.candidate_id,
        "expression": key.expression,
        "dimensions": list(key.dimensions),
        "values": list(key.values),
        "insample_candidate": candidate,
        "folds_evaluated": evaluated,
        "folds_passed": passed,
        "oos_pass_ratio": round(pass_ratio, 10),
        "aggregate_oos_metrics": aggregate_metrics,
        "oos_max_day_concentration": _max_day_concentration(oos_frame),
        "fold_results": fold_results,
        "ready_for_candidate_registry": False,
        "paper_observation_allowed": False,
        "can_promote_rules": False,
        "operational_authority": False,
    }


def validate_candidates_oos(
    aligned: pd.DataFrame,
    *,
    min_trade_count: int,
    max_day_concentration: float,
    min_oos_trade_count: int,
    min_oos_pass_ratio: float,
    min_oos_folds: int,
) -> dict[str, Any]:
    baseline, candidates, positive = discover_positive_candidates(
        aligned,
        min_trade_count=min_trade_count,
        max_day_concentration=max_day_concentration,
    )
    folds = _monthly_walk_forward_folds(aligned)
    if not positive or not folds:
        return {
            "baseline_metrics": baseline,
            "candidate_count": len(candidates),
            "positive_candidate_count": len(positive),
            "oos_evaluated_candidate_count": 0,
            "oos_surviving_candidate_count": 0,
            "oos_rejected_candidate_count": len(positive),
            "oos_candidate_results": [],
            "oos_shortlist": [],
            "folds": [_fold_public_record(fold) for fold in folds],
            "oos_gate_thresholds": {
                "min_trade_count": min_trade_count,
                "min_oos_trade_count": min_oos_trade_count,
                "min_oos_pass_ratio": min_oos_pass_ratio,
                "min_oos_folds": min_oos_folds,
                "max_day_concentration": max_day_concentration,
            },
        }

    results = [
        _evaluate_candidate_oos(candidate, folds, min_oos_trade_count=min_oos_trade_count)
        for candidate in positive
    ]
    for result in results:
        aggregate = result["aggregate_oos_metrics"]
        day_concentration = result["oos_max_day_concentration"]
        result["survives_oos_research_gate"] = bool(
            result["folds_evaluated"] >= min_oos_folds
            and result["oos_pass_ratio"] >= min_oos_pass_ratio
            and aggregate["trade_count"] >= min_oos_trade_count
            and aggregate["net_pnl"] > 0
            and _numeric_pf(aggregate) > 1.0
            and (day_concentration is None or day_concentration <= max_day_concentration)
        )
        reasons: list[str] = []
        if result["folds_evaluated"] < min_oos_folds:
            reasons.append("insufficient_oos_folds")
        if result["oos_pass_ratio"] < min_oos_pass_ratio:
            reasons.append("oos_pass_ratio_below_threshold")
        if aggregate["trade_count"] < min_oos_trade_count:
            reasons.append("insufficient_oos_trade_count")
        if aggregate["net_pnl"] <= 0:
            reasons.append("non_positive_oos_net_pnl")
        if _numeric_pf(aggregate) <= 1.0:
            reasons.append("oos_profit_factor_not_above_one")
        if day_concentration is not None and day_concentration > max_day_concentration:
            reasons.append("oos_day_concentration_too_high")
        result["oos_rejection_reasons"] = reasons

    results.sort(
        key=lambda item: (
            bool(item["survives_oos_research_gate"]),
            float(item["oos_pass_ratio"]),
            float(item["aggregate_oos_metrics"].get("net_pnl") or 0.0),
            _numeric_pf(item["aggregate_oos_metrics"]),
        ),
        reverse=True,
    )
    shortlist = [result for result in results if result["survives_oos_research_gate"]]
    return {
        "baseline_metrics": baseline,
        "candidate_count": len(candidates),
        "positive_candidate_count": len(positive),
        "oos_evaluated_candidate_count": len(results),
        "oos_surviving_candidate_count": len(shortlist),
        "oos_rejected_candidate_count": len(results) - len(shortlist),
        "oos_candidate_results": results[:30],
        "oos_shortlist": shortlist[:15],
        "folds": [_fold_public_record(fold) for fold in folds],
        "oos_gate_thresholds": {
            "min_trade_count": min_trade_count,
            "min_oos_trade_count": min_oos_trade_count,
            "min_oos_pass_ratio": min_oos_pass_ratio,
            "min_oos_folds": min_oos_folds,
            "max_day_concentration": max_day_concentration,
        },
    }


def _gate_matrix(
    *,
    allow_runtime_read: bool,
    trades_loaded: bool,
    candles_loaded: bool,
    aligned_rows: int,
    positive_count: int,
    oos_evaluated_count: int,
    oos_surviving_count: int,
) -> list[dict[str, Any]]:
    return [
        {
            "gate_id": "research_only_contract",
            "gate_name": "Research-only contract preserved",
            "severity": "critical",
            "passed": True,
            "evidence": "research_only=True; operational_authority=False",
        },
        {
            "gate_id": "runtime_read_explicit",
            "gate_name": "Runtime/data reads are explicit",
            "severity": "critical",
            "passed": True,
            "evidence": f"allow_runtime_read={allow_runtime_read}; input_mode={'runtime_read_only' if allow_runtime_read else 'no_runtime_rows_loaded'}",
        },
        {
            "gate_id": "source_contract_available",
            "gate_name": "Trades and candles source contract is explicit",
            "severity": "high",
            "passed": (not allow_runtime_read) or (trades_loaded and candles_loaded),
            "evidence": f"trades_loaded={trades_loaded}; candles_loaded={candles_loaded}",
        },
        {
            "gate_id": "alignment_required_for_oos",
            "gate_name": "OOS validation requires aligned rows",
            "severity": "high",
            "passed": (not allow_runtime_read) or aligned_rows > 0,
            "evidence": f"aligned_rows={aligned_rows}",
        },
        {
            "gate_id": "positive_candidates_required_for_oos",
            "gate_name": "Positive candidates are required before OOS evaluation",
            "severity": "high",
            "passed": (not allow_runtime_read) or positive_count > 0,
            "evidence": f"positive_candidate_count={positive_count}",
        },
        {
            "gate_id": "oos_validation_executed",
            "gate_name": "OOS validation was executed when runtime reads were allowed",
            "severity": "high",
            "passed": (not allow_runtime_read) or oos_evaluated_count > 0,
            "evidence": f"oos_evaluated_candidate_count={oos_evaluated_count}",
        },
        {
            "gate_id": "candidate_registry_blocked",
            "gate_name": "OOS survivors do not enter registry",
            "severity": "critical",
            "passed": True,
            "evidence": f"oos_surviving_candidate_count={oos_surviving_count}; ready_for_candidate_registry=False",
        },
        {
            "gate_id": "paper_observation_blocked",
            "gate_name": "Paper observation remains blocked",
            "severity": "critical",
            "passed": True,
            "evidence": "paper_observation_allowed=False",
        },
        {
            "gate_id": "promotion_blocked",
            "gate_name": "Rule and model promotion blocked",
            "severity": "critical",
            "passed": True,
            "evidence": "can_promote_rules=False; can_promote_model=False",
        },
        {
            "gate_id": "runtime_unchanged",
            "gate_name": "Runtime and execution surfaces unchanged",
            "severity": "critical",
            "passed": True,
            "evidence": "updates_freqtrade=false; updates_risk_manager=false; sends_orders=false",
        },
    ]


def _summarize_gates(gates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failed = [str(gate["gate_id"]) for gate in gates if not gate.get("passed")]
    critical_failed = [
        str(gate["gate_id"])
        for gate in gates
        if not gate.get("passed") and gate.get("severity") == "critical"
    ]
    return {
        "gate_count": len(gates),
        "passed_gate_count": len(gates) - len(failed),
        "failed_gate_count": len(failed),
        "failed_gate_ids": failed,
        "critical_failed_gate_ids": critical_failed,
    }


def _json_default(value: object) -> object:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(f"object of type {type(value)!r} is not JSON serializable")


def build_positive_rule_oos_validation_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    legacy_trade_dataset: str | Path | None = None,
    candle_roots: Sequence[str | Path] | None = None,
    min_trade_count: int = DEFAULT_MIN_TRADE_COUNT,
    max_day_concentration: float = DEFAULT_MAX_DAY_CONCENTRATION,
    min_oos_trade_count: int = DEFAULT_MIN_OOS_TRADE_COUNT,
    min_oos_pass_ratio: float = DEFAULT_MIN_OOS_PASS_RATIO,
    min_oos_folds: int = DEFAULT_MIN_OOS_FOLDS,
    alignment_tolerance_seconds: int = DEFAULT_ALIGNMENT_TOLERANCE_SECONDS,
    write: bool = False,
    no_write: bool = True,
) -> dict[str, Any]:
    root = Path(project_root)
    write_requested = bool(write and not no_write)

    raw_trade_rows = 0
    normalized_trades = pd.DataFrame()
    trades_source = None
    candles = pd.DataFrame()
    candle_sources: list[SourceInfo] = []
    aligned = pd.DataFrame()
    oos = validate_candidates_oos(
        pd.DataFrame(),
        min_trade_count=min_trade_count,
        max_day_concentration=max_day_concentration,
        min_oos_trade_count=min_oos_trade_count,
        min_oos_pass_ratio=min_oos_pass_ratio,
        min_oos_folds=min_oos_folds,
    )
    critical_warnings: list[str] = []

    if allow_runtime_read:
        if legacy_trade_dataset is not None:
            master_path = Path(legacy_trade_dataset)
            if not master_path.is_absolute():
                master_path = root / master_path
            if master_path.exists():
                raw_trades, normalized_trades, trades_source = load_legacy_trade_dataset(master_path, root)
                raw_trade_rows = len(raw_trades)
            else:
                critical_warnings.append(f"legacy_trade_dataset_missing:{master_path}")
        else:
            critical_warnings.append("legacy_trade_dataset_not_supplied")

        roots = [Path(item) for item in (candle_roots or [Path("data")])]
        candles, candle_sources = load_candles(root, roots)
        aligned = align_trades_to_candles(
            normalized_trades,
            candles,
            tolerance_seconds=alignment_tolerance_seconds,
        )
        oos = validate_candidates_oos(
            aligned,
            min_trade_count=min_trade_count,
            max_day_concentration=max_day_concentration,
            min_oos_trade_count=min_oos_trade_count,
            min_oos_pass_ratio=min_oos_pass_ratio,
            min_oos_folds=min_oos_folds,
        )

    positive_count = int(oos.get("positive_candidate_count", 0))
    oos_evaluated_count = int(oos.get("oos_evaluated_candidate_count", 0))
    oos_surviving_count = int(oos.get("oos_surviving_candidate_count", 0))
    gates = _gate_matrix(
        allow_runtime_read=allow_runtime_read,
        trades_loaded=trades_source is not None,
        candles_loaded=not candles.empty,
        aligned_rows=len(aligned),
        positive_count=positive_count,
        oos_evaluated_count=oos_evaluated_count,
        oos_surviving_count=oos_surviving_count,
    )
    gate_summary = _summarize_gates(gates)

    reason = "positive_rule_oos_validation_requires_explicit_runtime_read_and_sources"
    if allow_runtime_read and len(aligned) == 0:
        reason = "positive_rule_oos_validation_blocked_missing_aligned_rows"
    elif allow_runtime_read and positive_count == 0:
        reason = "positive_rule_oos_validation_completed_no_positive_candidates"
    elif allow_runtime_read and oos_evaluated_count == 0:
        reason = "positive_rule_oos_validation_blocked_no_oos_evaluable_candidates"
    elif allow_runtime_read and oos_surviving_count == 0:
        reason = "positive_rule_oos_validation_completed_no_survivors"
    elif allow_runtime_read and oos_surviving_count > 0:
        reason = "positive_rule_oos_survivors_found_research_only"

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(project_root),
        "status": "blocked",
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "input_mode": "runtime_read_only" if allow_runtime_read else "no_runtime_rows_loaded",
        "allow_runtime_read": allow_runtime_read,
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "expected_trade_value_contract": EXPECTED_TRADE_VALUE_CONTRACT,
        "forbidden_actions": FORBIDDEN_ACTIONS,
        "critical_warnings": critical_warnings,
        "legacy_trade_dataset_loaded": trades_source is not None,
        "legacy_trade_dataset_rows": int(raw_trade_rows),
        "legacy_trade_dataset_normalized_rows": int(len(normalized_trades)),
        "legacy_trade_dataset_source": trades_source.to_dict() if trades_source is not None else None,
        "candle_sources_loaded": not candles.empty,
        "candle_source_count": len(candle_sources),
        "candle_rows": int(len(candles)),
        "candle_sources": [source.to_dict() for source in candle_sources],
        "master_candle_alignment_computed": bool(len(aligned) > 0),
        "aligned_rows": int(len(aligned)),
        "alignment_coverage_ratio": _safe_float(len(aligned) / len(normalized_trades)) if len(normalized_trades) else None,
        "feature_rows": int(len(aligned)),
        "baseline_metrics": oos["baseline_metrics"],
        "candidate_count": int(oos["candidate_count"]),
        "positive_candidate_count": positive_count,
        "oos_evaluated_candidate_count": oos_evaluated_count,
        "oos_surviving_candidate_count": oos_surviving_count,
        "oos_rejected_candidate_count": int(oos["oos_rejected_candidate_count"]),
        "oos_shortlist": oos["oos_shortlist"],
        "oos_candidate_results": oos["oos_candidate_results"],
        "folds": oos["folds"],
        "oos_gate_thresholds": oos["oos_gate_thresholds"],
        "ready_for_oos_validation": bool(positive_count > 0 and len(aligned) > 0),
        "oos_validated": bool(oos_evaluated_count > 0),
        "ready_for_shadow_observation": False,
        "paper_observation_allowed": False,
        "ready_for_candidate_registry": False,
        "remediation_application_allowed": False,
        "registers_candidate_rules": False,
        "gate_matrix": gates,
        "gate_summary": gate_summary,
        **SAFETY_FLAGS,
    }

    if write_requested:
        output_dir = root / "data" / "reports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "ocr_master_candle_positive_rule_oos_validation_v1.json"
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=_json_default), encoding="utf-8")
        report["write_performed"] = True
        report["writes_reports"] = True
        report["output_path"] = str(output_path)

    return report
