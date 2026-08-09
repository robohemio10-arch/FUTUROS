"""Fixed-threshold momentum validation with chronological replay holdout."""

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
    ARM_CONTROL,
    ARM_RET12,
    ARM_RET12_RET1,
    FIXED_CANDIDATE_ARMS,
    HOLDOUT_INDEPENDENCE,
    HOLDOUT_RATIO,
    INITIAL_DEVELOPMENT_TRAIN_RATIO,
    MIN_ELIGIBLE_TRADES,
    MIN_HOLDOUT_TRADES,
    MIN_POSITIVE_WALKFORWARD_FOLDS,
    MIN_SELECTED_TRADES_HOLDOUT,
    MIN_SELECTED_TRADES_PER_FOLD,
    RET1_THRESHOLD,
    RET12_THRESHOLD,
    WALKFORWARD_FOLD_COUNT,
)


def validate_fixed_threshold_momentum(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Validate only the two pre-frozen momentum filters against control."""
    prepared, preparation = prepare_profit_dataset(frame)
    eligible = sort_trades(
        prepared.loc[prepared["profit_optimization_eligible"]].copy()
    ).reset_index(drop=True)
    if len(eligible) < MIN_ELIGIBLE_TRADES:
        return prepared, _blocked_report(
            "insufficient_profit_eligible_trades",
            preparation=preparation,
            eligible_count=len(eligible),
        )

    holdout_count = max(
        MIN_HOLDOUT_TRADES,
        int(math.ceil(len(eligible) * HOLDOUT_RATIO)),
    )
    if holdout_count >= len(eligible):
        return prepared, _blocked_report(
            "insufficient_trades_for_chronological_holdout",
            preparation=preparation,
            eligible_count=len(eligible),
        )

    development = eligible.iloc[:-holdout_count].copy().reset_index(drop=True)
    holdout = eligible.iloc[-holdout_count:].copy().reset_index(drop=True)
    folds = build_walkforward_folds(len(development))
    if len(folds) != WALKFORWARD_FOLD_COUNT:
        return prepared, _blocked_report(
            "walkforward_fold_construction_failed",
            preparation=preparation,
            eligible_count=len(eligible),
        )

    arm_masks = build_fixed_arm_masks(development)
    development_reports = [
        _evaluate_development_arm(
            development,
            mask=arm_masks[arm_id],
            arm_id=arm_id,
            folds=folds,
        )
        for arm_id in FIXED_CANDIDATE_ARMS
    ]
    ranked = rank_development_candidates(development_reports)
    champion = next(
        (
            item
            for item in ranked
            if item.get("development_decision") == "FREEZE_FOR_REPLAY_HOLDOUT"
        ),
        None,
    )

    annotated = _annotate_partitions(prepared, development, holdout)
    annotated = _annotate_fixed_arm_eligibility(annotated)
    control_report = _development_control_report(development, folds)

    if champion is None:
        return annotated, {
            "status": "ok",
            "reason": "no_fixed_momentum_candidate_passed_walkforward_freeze_gate",
            "eligible_trade_count": int(len(eligible)),
            "development_trade_count": int(len(development)),
            "holdout_trade_count": int(len(holdout)),
            "walkforward_folds": folds,
            "control_walkforward": control_report,
            "development_candidates": ranked,
            "frozen_champion": None,
            "replay_holdout_evaluation": None,
            "replay_holdout_passed": False,
            "ready_for_forward_paper_ab": False,
            "ready_for_paper_wiring": False,
            "holdout_independence": HOLDOUT_INDEPENDENCE,
            "preparation": preparation,
            "validation_contract": _validation_contract(),
        }

    holdout_result = _evaluate_replay_holdout(
        holdout,
        arm_id=str(champion["arm_id"]),
    )
    replay_passed = bool(holdout_result["holdout_passed"])
    return annotated, {
        "status": "ok",
        "reason": "fixed_threshold_walkforward_replay_holdout_completed",
        "eligible_trade_count": int(len(eligible)),
        "development_trade_count": int(len(development)),
        "holdout_trade_count": int(len(holdout)),
        "walkforward_folds": folds,
        "control_walkforward": control_report,
        "development_candidates": ranked,
        "frozen_champion": champion,
        "replay_holdout_evaluation": holdout_result,
        "replay_holdout_passed": replay_passed,
        "ready_for_forward_paper_ab": replay_passed,
        "ready_for_paper_wiring": False,
        "ready_for_paper_wiring_reason": (
            "historical_threshold_discovery_exposed_replay_holdout"
            if replay_passed
            else "replay_holdout_gate_failed"
        ),
        "holdout_independence": HOLDOUT_INDEPENDENCE,
        "preparation": preparation,
        "validation_contract": _validation_contract(),
    }


def build_fixed_arm_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Build control and exactly two frozen momentum masks."""
    ret12 = _numeric_series(frame, "entry_return_12")
    ret1 = _numeric_series(frame, "entry_return_1")
    control = pd.Series(True, index=frame.index, dtype=bool)
    ret12_mask = ret12.ge(RET12_THRESHOLD).fillna(False).astype(bool)
    combo_mask = (
        ret12.ge(RET12_THRESHOLD) & ret1.ge(RET1_THRESHOLD)
    ).fillna(False).astype(bool)
    return {
        ARM_CONTROL: control,
        ARM_RET12: ret12_mask,
        ARM_RET12_RET1: combo_mask,
    }


def build_walkforward_folds(development_count: int) -> list[dict[str, int]]:
    """Build three expanding-history folds fully before replay holdout."""
    if development_count < WALKFORWARD_FOLD_COUNT + 2:
        return []
    initial_train = max(
        1,
        int(math.floor(development_count * INITIAL_DEVELOPMENT_TRAIN_RATIO)),
    )
    remaining = development_count - initial_train
    if remaining < WALKFORWARD_FOLD_COUNT:
        return []

    folds: list[dict[str, int]] = []
    for fold_index in range(WALKFORWARD_FOLD_COUNT):
        validation_start = initial_train + int(
            math.floor(remaining * fold_index / WALKFORWARD_FOLD_COUNT)
        )
        validation_end = initial_train + int(
            math.floor(remaining * (fold_index + 1) / WALKFORWARD_FOLD_COUNT)
        )
        if validation_end <= validation_start:
            return []
        folds.append(
            {
                "fold_index": fold_index + 1,
                "train_start": 0,
                "train_end_exclusive": validation_start,
                "validation_start": validation_start,
                "validation_end_exclusive": validation_end,
            }
        )
    return folds


def rank_development_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank candidates using development walk-forward fields only."""
    return sorted(
        [dict(item) for item in candidates],
        key=lambda item: (
            item.get("development_decision") != "FREEZE_FOR_REPLAY_HOLDOUT",
            -int(item.get("positive_walkforward_fold_count", 0)),
            -_sort_float(item.get("walkforward_total_delta_pnl")),
            -_sort_float(item.get("walkforward_candidate_net_pnl")),
            -_sort_float(item.get("walkforward_expectancy")),
            -_sort_float(item.get("walkforward_profit_factor")),
            _sort_float(item.get("walkforward_maximum_drawdown")),
            -_sort_float(item.get("positive_pnl_retention_ratio")),
            str(item.get("arm_id")),
        ),
    )


def _evaluate_development_arm(
    development: pd.DataFrame,
    *,
    mask: pd.Series,
    arm_id: str,
    folds: list[dict[str, int]],
) -> dict[str, Any]:
    fold_reports: list[dict[str, Any]] = []
    baseline_frames: list[pd.DataFrame] = []
    selected_frames: list[pd.DataFrame] = []

    for fold in folds:
        start = int(fold["validation_start"])
        end = int(fold["validation_end_exclusive"])
        baseline = development.iloc[start:end].copy().reset_index(drop=True)
        fold_mask = mask.iloc[start:end].reset_index(drop=True).fillna(False).astype(bool)
        selected = baseline.loc[fold_mask].copy().reset_index(drop=True)
        baseline_metrics = profit_metrics(baseline)
        candidate_metrics = profit_metrics(selected)
        delta = float(candidate_metrics["net_pnl"]) - float(baseline_metrics["net_pnl"])
        retention = _positive_pnl_retention(baseline, selected)
        fold_passed = _fold_gate(
            candidate_metrics,
            delta_pnl=delta,
            selected_count=len(selected),
        )
        fold_reports.append(
            {
                **fold,
                "baseline_metrics": baseline_metrics,
                "candidate_metrics": candidate_metrics,
                "selected_trade_count": int(len(selected)),
                "selection_ratio": float(len(selected) / len(baseline)) if len(baseline) else 0.0,
                "delta_pnl": delta,
                "positive_pnl_retention_ratio": retention["ratio"],
                "retained_positive_pnl": retention["retained_positive_pnl"],
                "rejected_positive_pnl": retention["rejected_positive_pnl"],
                "fold_passed": fold_passed,
            }
        )
        baseline_frames.append(baseline)
        selected_frames.append(selected)

    baseline_validation = pd.concat(baseline_frames, ignore_index=True)
    selected_validation = pd.concat(selected_frames, ignore_index=True)
    baseline_metrics = profit_metrics(baseline_validation)
    candidate_metrics = profit_metrics(selected_validation)
    total_delta = float(candidate_metrics["net_pnl"]) - float(baseline_metrics["net_pnl"])
    positive_folds = sum(bool(item["fold_passed"]) for item in fold_reports)
    retention = _positive_pnl_retention(baseline_validation, selected_validation)
    freeze = _development_freeze_gate(
        candidate_metrics,
        total_delta=total_delta,
        positive_fold_count=positive_folds,
        selected_count=len(selected_validation),
    )
    return {
        "arm_id": arm_id,
        "condition": _arm_condition(arm_id),
        "development_decision": (
            "FREEZE_FOR_REPLAY_HOLDOUT" if freeze else "REJECT_PRE_HOLDOUT"
        ),
        "walkforward_folds": fold_reports,
        "positive_walkforward_fold_count": int(positive_folds),
        "walkforward_fold_count": len(fold_reports),
        "walkforward_baseline_trade_count": int(len(baseline_validation)),
        "walkforward_selected_trade_count": int(len(selected_validation)),
        "walkforward_selection_ratio": (
            float(len(selected_validation) / len(baseline_validation))
            if len(baseline_validation)
            else 0.0
        ),
        "walkforward_baseline_net_pnl": baseline_metrics["net_pnl"],
        "walkforward_candidate_net_pnl": candidate_metrics["net_pnl"],
        "walkforward_total_delta_pnl": total_delta,
        "walkforward_expectancy": candidate_metrics["expectancy"],
        "walkforward_profit_factor": candidate_metrics["profit_factor"],
        "walkforward_average_win": candidate_metrics["average_win"],
        "walkforward_average_loss": candidate_metrics["average_loss"],
        "walkforward_maximum_drawdown": candidate_metrics["maximum_drawdown"],
        "positive_pnl_retention_ratio": retention["ratio"],
        "retained_positive_pnl": retention["retained_positive_pnl"],
        "rejected_positive_pnl": retention["rejected_positive_pnl"],
        "holdout_metrics_used_for_selection": False,
    }


def _development_control_report(
    development: pd.DataFrame,
    folds: list[dict[str, int]],
) -> dict[str, Any]:
    frames = [
        development.iloc[
            int(fold["validation_start"]): int(fold["validation_end_exclusive"])
        ].copy()
        for fold in folds
    ]
    validation = pd.concat(frames, ignore_index=True)
    return {
        "arm_id": ARM_CONTROL,
        "trade_count": int(len(validation)),
        "metrics": profit_metrics(validation),
    }


def _evaluate_replay_holdout(
    holdout: pd.DataFrame,
    *,
    arm_id: str,
) -> dict[str, Any]:
    masks = build_fixed_arm_masks(holdout)
    selected = holdout.loc[masks[arm_id]].copy().reset_index(drop=True)
    baseline_metrics = profit_metrics(holdout)
    candidate_metrics = profit_metrics(selected)
    delta = float(candidate_metrics["net_pnl"]) - float(baseline_metrics["net_pnl"])
    retention = _positive_pnl_retention(holdout, selected)
    passed = _holdout_gate(
        candidate_metrics,
        delta_pnl=delta,
        selected_count=len(selected),
    )
    return {
        "arm_id": arm_id,
        "condition": _arm_condition(arm_id),
        "baseline_trade_count": int(len(holdout)),
        "selected_trade_count": int(len(selected)),
        "selection_ratio": float(len(selected) / len(holdout)) if len(holdout) else 0.0,
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "delta_pnl": delta,
        "positive_pnl_retention_ratio": retention["ratio"],
        "retained_positive_pnl": retention["retained_positive_pnl"],
        "rejected_positive_pnl": retention["rejected_positive_pnl"],
        "holdout_passed": passed,
        "holdout_used_for_selection": False,
        "historically_pristine_holdout": False,
    }


def _fold_gate(
    metrics: Mapping[str, Any],
    *,
    delta_pnl: float,
    selected_count: int,
) -> bool:
    return bool(
        selected_count >= MIN_SELECTED_TRADES_PER_FOLD
        and _positive_metrics(metrics)
        and delta_pnl > 0
    )


def _development_freeze_gate(
    metrics: Mapping[str, Any],
    *,
    total_delta: float,
    positive_fold_count: int,
    selected_count: int,
) -> bool:
    return bool(
        positive_fold_count >= MIN_POSITIVE_WALKFORWARD_FOLDS
        and selected_count >= MIN_SELECTED_TRADES_PER_FOLD * WALKFORWARD_FOLD_COUNT
        and _positive_metrics(metrics)
        and total_delta > 0
    )


def _holdout_gate(
    metrics: Mapping[str, Any],
    *,
    delta_pnl: float,
    selected_count: int,
) -> bool:
    return bool(
        selected_count >= MIN_SELECTED_TRADES_HOLDOUT
        and _positive_metrics(metrics)
        and delta_pnl > 0
    )


def _positive_metrics(metrics: Mapping[str, Any]) -> bool:
    profit_factor = finite_or_none(metrics.get("profit_factor"))
    return bool(
        float(metrics.get("net_pnl", 0.0)) > 0
        and float(metrics.get("expectancy", 0.0)) > 0
        and (profit_factor is None or profit_factor > 1.0)
    )


def _positive_pnl_retention(
    baseline: pd.DataFrame,
    selected: pd.DataFrame,
) -> dict[str, float | None]:
    baseline_pnl = _numeric_series(baseline, "net_pnl")
    selected_pnl = _numeric_series(selected, "net_pnl")
    baseline_positive = float(baseline_pnl.clip(lower=0.0).sum())
    selected_positive = float(selected_pnl.clip(lower=0.0).sum())
    ratio = selected_positive / baseline_positive if baseline_positive > 0 else None
    return {
        "ratio": ratio,
        "retained_positive_pnl": selected_positive,
        "rejected_positive_pnl": max(baseline_positive - selected_positive, 0.0),
    }


def _annotate_partitions(
    prepared: pd.DataFrame,
    development: pd.DataFrame,
    holdout: pd.DataFrame,
) -> pd.DataFrame:
    output = prepared.copy()
    output["momentum_validation_partition"] = "excluded"
    stable = output["stable_trade_id"].astype("string")
    development_ids = set(development["stable_trade_id"].astype("string"))
    holdout_ids = set(holdout["stable_trade_id"].astype("string"))
    output.loc[stable.isin(development_ids), "momentum_validation_partition"] = "development"
    output.loc[stable.isin(holdout_ids), "momentum_validation_partition"] = "replay_holdout"
    return output


def _annotate_fixed_arm_eligibility(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    masks = build_fixed_arm_masks(output)
    output["momentum_ret12_fixed_eligible"] = masks[ARM_RET12]
    output["momentum_ret12_ret1_fixed_eligible"] = masks[ARM_RET12_RET1]
    return output


def _arm_condition(arm_id: str) -> dict[str, Any]:
    if arm_id == ARM_RET12:
        return {
            "field": "entry_return_12",
            "operator": "gte",
            "value": RET12_THRESHOLD,
        }
    if arm_id == ARM_RET12_RET1:
        return {
            "operator": "and",
            "conditions": [
                {
                    "field": "entry_return_12",
                    "operator": "gte",
                    "value": RET12_THRESHOLD,
                },
                {
                    "field": "entry_return_1",
                    "operator": "gte",
                    "value": RET1_THRESHOLD,
                },
            ],
        }
    return {"operator": "all_eligible"}


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _sort_float(value: Any) -> float:
    parsed = finite_or_none(value)
    return parsed if parsed is not None and math.isfinite(parsed) else -math.inf


def _validation_contract() -> dict[str, Any]:
    return {
        "timeframe": "5m",
        "fixed_threshold_count": 2,
        "searches_new_thresholds": False,
        "uses_profit_protection": False,
        "holdout_ratio": HOLDOUT_RATIO,
        "walkforward_fold_count": WALKFORWARD_FOLD_COUNT,
        "required_positive_walkforward_folds": MIN_POSITIVE_WALKFORWARD_FOLDS,
        "holdout_used_for_selection": False,
        "historically_pristine_holdout": False,
    }


def _blocked_report(
    reason: str,
    *,
    preparation: Mapping[str, Any],
    eligible_count: int,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "eligible_trade_count": int(eligible_count),
        "development_candidates": [],
        "frozen_champion": None,
        "replay_holdout_evaluation": None,
        "replay_holdout_passed": False,
        "ready_for_forward_paper_ab": False,
        "ready_for_paper_wiring": False,
        "holdout_independence": HOLDOUT_INDEPENDENCE,
        "preparation": dict(preparation),
        "validation_contract": _validation_contract(),
    }
