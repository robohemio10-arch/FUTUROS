"""Pristine forward OOS observation for one frozen paper momentum filter."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

from smartcrypto.research.paper_profit_maximization.metrics import (
    finite_or_none,
    prepare_profit_dataset,
    profit_metrics,
    sort_trades,
)

from .contracts import (
    FORWARD_FREEZE_COMMIT,
    FORWARD_START_UTC,
    FORWARD_START_UTC_TEXT,
    FROZEN_FILTER_CONDITION,
    FROZEN_FILTER_ID,
    MIN_FEATURE_COVERAGE_RATIO,
    MIN_FORWARD_CANDIDATE_TRADES,
    RET1_THRESHOLD,
    RET12_THRESHOLD,
)


def observe_frozen_momentum_forward(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Observe only trades closed after the immutable freeze timestamp."""
    prepared, preparation = prepare_profit_dataset(frame)
    eligible = prepared.loc[prepared["profit_optimization_eligible"]].copy()
    eligible["close_time_utc"] = pd.to_datetime(
        eligible.get("close_time_utc"),
        utc=True,
        errors="coerce",
    )
    eligible = sort_trades(eligible).reset_index(drop=True)
    post_freeze = eligible.loc[
        eligible["close_time_utc"].gt(FORWARD_START_UTC)
    ].copy().reset_index(drop=True)

    annotated = _annotate_forward_partition(prepared, post_freeze)
    annotated = _annotate_candidate_eligibility(annotated)

    control_metrics = profit_metrics(post_freeze)
    feature_complete = _feature_complete_mask(post_freeze)
    candidate_mask = _candidate_mask(post_freeze)
    candidate = post_freeze.loc[candidate_mask].copy().reset_index(drop=True)
    candidate_metrics = profit_metrics(candidate)
    delta_pnl = float(candidate_metrics["net_pnl"]) - float(control_metrics["net_pnl"])
    retention = _positive_pnl_retention(post_freeze, candidate)

    feature_complete_count = int(feature_complete.sum())
    control_count = int(len(post_freeze))
    candidate_count = int(len(candidate))
    feature_coverage_ratio = (
        float(feature_complete_count / control_count) if control_count else 0.0
    )
    selection_ratio = float(candidate_count / control_count) if control_count else 0.0

    first_half, second_half = _temporal_halves(post_freeze)
    first_half_report = _segment_report("first_half", first_half)
    second_half_report = _segment_report("second_half", second_half)

    evidence_ready = bool(
        candidate_count >= MIN_FORWARD_CANDIDATE_TRADES
        and feature_coverage_ratio >= MIN_FEATURE_COVERAGE_RATIO
    )
    gate_passed = bool(
        evidence_ready
        and _positive_metrics(candidate_metrics)
        and delta_pnl > 0.0
        and float(candidate_metrics["maximum_drawdown"])
        < float(control_metrics["maximum_drawdown"])
        and first_half_report["segment_passed"]
        and second_half_report["segment_passed"]
    )

    max_close = _max_timestamp(post_freeze, "close_time_utc")
    min_close = _min_timestamp(post_freeze, "close_time_utc")
    reason = _result_reason(
        control_count=control_count,
        evidence_ready=evidence_ready,
        gate_passed=gate_passed,
    )
    return annotated, {
        "status": "ok",
        "reason": reason,
        "freeze_commit": FORWARD_FREEZE_COMMIT,
        "forward_start_utc": FORWARD_START_UTC_TEXT,
        "cutoff_operator": "close_time_utc_gt",
        "timeframe": "5m",
        "eligible_trade_count_total": int(len(eligible)),
        "forward_control_trade_count": control_count,
        "forward_candidate_trade_count": candidate_count,
        "forward_candidate_minimum_required": MIN_FORWARD_CANDIDATE_TRADES,
        "forward_candidate_selection_ratio": selection_ratio,
        "feature_complete_trade_count": feature_complete_count,
        "feature_missing_trade_count": control_count - feature_complete_count,
        "feature_coverage_ratio": feature_coverage_ratio,
        "minimum_feature_coverage_ratio": MIN_FEATURE_COVERAGE_RATIO,
        "control_metrics": control_metrics,
        "candidate_metrics": candidate_metrics,
        "delta_pnl": delta_pnl,
        "positive_pnl_retention_ratio": retention["ratio"],
        "retained_positive_pnl": retention["retained_positive_pnl"],
        "rejected_positive_pnl": retention["rejected_positive_pnl"],
        "first_half": first_half_report,
        "second_half": second_half_report,
        "forward_evidence_ready": evidence_ready,
        "forward_gate_passed": gate_passed,
        "eligible_for_future_paper_wiring_review": gate_passed,
        "ready_for_paper_wiring": False,
        "ready_for_paper_wiring_reason": "observer_has_no_operational_authority",
        "frozen_filter_id": FROZEN_FILTER_ID,
        "frozen_filter_condition": FROZEN_FILTER_CONDITION,
        "thresholds_frozen": True,
        "threshold_search_performed": False,
        "profit_protection_used": False,
        "observation_min_close_time_utc": _timestamp_text(min_close),
        "observation_max_close_time_utc": _timestamp_text(max_close),
        "observation_duration_seconds": _duration_from_freeze(max_close),
        "opened_before_or_at_freeze_closed_after_count": _cross_cutoff_trade_count(
            post_freeze
        ),
        "diagnostics_only": _diagnostic_breakdowns(post_freeze),
        "preparation": preparation,
        "validation_contract": _validation_contract(),
    }


def build_frozen_candidate_mask(frame: pd.DataFrame) -> pd.Series:
    """Return the exact frozen ret12+ret1 candidate mask; missing features fail closed."""
    return _candidate_mask(frame)


def _candidate_mask(frame: pd.DataFrame) -> pd.Series:
    ret12 = _finite_numeric_series(frame, "entry_return_12")
    ret1 = _finite_numeric_series(frame, "entry_return_1")
    return (
        ret12.ge(RET12_THRESHOLD) & ret1.ge(RET1_THRESHOLD)
    ).fillna(False).astype(bool)


def _feature_complete_mask(frame: pd.DataFrame) -> pd.Series:
    ret12 = _finite_numeric_series(frame, "entry_return_12")
    ret1 = _finite_numeric_series(frame, "entry_return_1")
    return (ret12.notna() & ret1.notna()).astype(bool)


def _temporal_halves(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = sort_trades(frame).reset_index(drop=True)
    midpoint = len(ordered) // 2
    return (
        ordered.iloc[:midpoint].copy().reset_index(drop=True),
        ordered.iloc[midpoint:].copy().reset_index(drop=True),
    )


def _segment_report(segment_id: str, control: pd.DataFrame) -> dict[str, Any]:
    mask = _candidate_mask(control)
    candidate = control.loc[mask].copy().reset_index(drop=True)
    control_metrics = profit_metrics(control)
    candidate_metrics = profit_metrics(candidate)
    delta = float(candidate_metrics["net_pnl"]) - float(control_metrics["net_pnl"])
    return {
        "segment_id": segment_id,
        "control_trade_count": int(len(control)),
        "candidate_trade_count": int(len(candidate)),
        "control_metrics": control_metrics,
        "candidate_metrics": candidate_metrics,
        "delta_pnl": delta,
        "segment_passed": bool(len(candidate) > 0 and delta > 0.0),
    }


def _diagnostic_breakdowns(frame: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    diagnostics: dict[str, list[dict[str, Any]]] = {}
    for column in ("symbol", "side", "regime"):
        if column not in frame.columns:
            continue
        entries: list[dict[str, Any]] = []
        values = frame[column].astype("string").fillna("<missing>")
        for value in sorted(values.unique().tolist()):
            subset = frame.loc[values.eq(value)].copy().reset_index(drop=True)
            candidate = subset.loc[_candidate_mask(subset)].copy().reset_index(drop=True)
            control_metrics = profit_metrics(subset)
            candidate_metrics = profit_metrics(candidate)
            entries.append(
                {
                    "value": str(value),
                    "control_trade_count": int(len(subset)),
                    "candidate_trade_count": int(len(candidate)),
                    "control_net_pnl": control_metrics["net_pnl"],
                    "candidate_net_pnl": candidate_metrics["net_pnl"],
                    "delta_pnl": float(candidate_metrics["net_pnl"])
                    - float(control_metrics["net_pnl"]),
                }
            )
        diagnostics[column] = entries
    return diagnostics


def _positive_metrics(metrics: Mapping[str, Any]) -> bool:
    profit_factor = finite_or_none(metrics.get("profit_factor"))
    return bool(
        float(metrics.get("net_pnl", 0.0)) > 0.0
        and float(metrics.get("expectancy", 0.0)) > 0.0
        and (profit_factor is None or profit_factor > 1.0)
    )


def _positive_pnl_retention(
    baseline: pd.DataFrame,
    selected: pd.DataFrame,
) -> dict[str, float | None]:
    baseline_pnl = _finite_numeric_series(baseline, "net_pnl").fillna(0.0)
    selected_pnl = _finite_numeric_series(selected, "net_pnl").fillna(0.0)
    baseline_positive = float(baseline_pnl.clip(lower=0.0).sum())
    selected_positive = float(selected_pnl.clip(lower=0.0).sum())
    ratio = selected_positive / baseline_positive if baseline_positive > 0.0 else None
    return {
        "ratio": ratio,
        "retained_positive_pnl": selected_positive,
        "rejected_positive_pnl": max(baseline_positive - selected_positive, 0.0),
    }


def _annotate_forward_partition(
    prepared: pd.DataFrame,
    post_freeze: pd.DataFrame,
) -> pd.DataFrame:
    output = prepared.copy()
    output["momentum_forward_oos_partition"] = "excluded_or_pre_freeze"
    if "stable_trade_id" not in output.columns or post_freeze.empty:
        return output
    ids = set(post_freeze["stable_trade_id"].astype("string"))
    stable = output["stable_trade_id"].astype("string")
    output.loc[stable.isin(ids), "momentum_forward_oos_partition"] = "forward_oos"
    return output


def _annotate_candidate_eligibility(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["momentum_forward_feature_complete"] = _feature_complete_mask(output)
    output["momentum_forward_candidate_eligible"] = _candidate_mask(output)
    return output


def _cross_cutoff_trade_count(frame: pd.DataFrame) -> int:
    if frame.empty or "open_time_utc" not in frame.columns:
        return 0
    opened = pd.to_datetime(frame["open_time_utc"], utc=True, errors="coerce")
    return int(opened.le(FORWARD_START_UTC).fillna(False).sum())


def _finite_numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    values = pd.to_numeric(frame[column], errors="coerce")
    return values.where(values.map(lambda value: bool(pd.notna(value) and math.isfinite(float(value)))))


def _max_timestamp(frame: pd.DataFrame, column: str) -> pd.Timestamp | None:
    if frame.empty or column not in frame.columns:
        return None
    values = pd.to_datetime(frame[column], utc=True, errors="coerce").dropna()
    return None if values.empty else pd.Timestamp(values.max())


def _min_timestamp(frame: pd.DataFrame, column: str) -> pd.Timestamp | None:
    if frame.empty or column not in frame.columns:
        return None
    values = pd.to_datetime(frame[column], utc=True, errors="coerce").dropna()
    return None if values.empty else pd.Timestamp(values.min())


def _duration_from_freeze(max_close: pd.Timestamp | None) -> float | None:
    if max_close is None:
        return None
    return max(float((max_close - FORWARD_START_UTC).total_seconds()), 0.0)


def _timestamp_text(value: pd.Timestamp | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def _result_reason(
    *,
    control_count: int,
    evidence_ready: bool,
    gate_passed: bool,
) -> str:
    if control_count == 0:
        return "forward_oos_collecting_no_post_freeze_trades"
    if not evidence_ready:
        return "forward_oos_collecting_insufficient_candidate_evidence"
    return "forward_oos_gate_passed" if gate_passed else "forward_oos_gate_failed"


def _validation_contract() -> dict[str, Any]:
    return {
        "freeze_commit": FORWARD_FREEZE_COMMIT,
        "forward_start_utc": FORWARD_START_UTC_TEXT,
        "cutoff_operator": "close_time_utc_gt",
        "frozen_filter_id": FROZEN_FILTER_ID,
        "ret12_threshold": RET12_THRESHOLD,
        "ret1_threshold": RET1_THRESHOLD,
        "minimum_candidate_trades": MIN_FORWARD_CANDIDATE_TRADES,
        "minimum_feature_coverage_ratio": MIN_FEATURE_COVERAGE_RATIO,
        "searches_new_thresholds": False,
        "uses_profit_protection": False,
        "blocks_entries": False,
        "requires_positive_net_pnl": True,
        "requires_positive_expectancy": True,
        "requires_profit_factor_gt_one": True,
        "requires_positive_delta_vs_control": True,
        "requires_lower_max_drawdown_than_control": True,
        "requires_positive_first_half_delta": True,
        "requires_positive_second_half_delta": True,
        "diagnostic_subgroups_can_change_filter": False,
        "ready_for_paper_wiring_is_always_false_in_this_observer": True,
    }
