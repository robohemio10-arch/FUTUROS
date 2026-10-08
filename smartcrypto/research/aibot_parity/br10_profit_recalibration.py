"""Research-only BR10 profit recalibration on the frozen walk-forward folds."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.research.aibot_parity.market_intelligence_pnl_ablation import (
    AblationError,
    ModelFactory,
    SAFETY_FLAGS,
    TARGET_COLUMN,
    _calibrate_threshold,
    _default_model_factory,
    _economic_metrics,
    _fit_predict,
    _load_bundle,
    _metrics,
    _threshold_candidates,
)

SCHEMA_VERSION = "br10_profit_recalibration_v1"
GROUP_FIELDS = ("symbol", "side")
MIN_GROUP_CALIBRATION_ROWS = 40
DIAGNOSTIC_FEATURES = (
    "feature_ret_close_30m",
    "feature_pre_entry_volatility_20",
    "feature_rsi_14",
)


class RecalibrationError(RuntimeError):
    """The frozen BR10 cohort or temporal calibration contract is invalid."""


def _group_key(frame: pd.DataFrame) -> pd.Series:
    if any(field not in frame.columns for field in GROUP_FIELDS):
        raise RecalibrationError("pretrade_group_field_missing")
    return frame["symbol"].astype(str) + "|" + frame["side"].astype(str)


def _calibrate_groups(
    validation: pd.DataFrame,
    scores: np.ndarray,
    global_threshold: float,
) -> dict[str, float]:
    """Optimize each sufficiently populated group on past validation labels only."""
    if len(validation) != len(scores):
        raise RecalibrationError("validation_score_shape_invalid")
    pnl = validation[TARGET_COLUMN].to_numpy(dtype=float)
    groups = _group_key(validation).to_numpy()
    thresholds: dict[str, float] = {}
    for group in sorted(set(groups)):
        mask = groups == group
        group_scores = scores[mask]
        if len(group_scores) < MIN_GROUP_CALIBRATION_ROWS:
            thresholds[group] = global_threshold
            continue
        group_pnl = pnl[mask]
        choices = set(_threshold_candidates(group_scores))
        choices.add(global_threshold)
        thresholds[group] = max(
            choices,
            key=lambda threshold: (
                float(group_pnl[group_scores >= threshold].sum()),
                int((group_scores >= threshold).sum()),
                -threshold,
            ),
        )
    return thresholds


def _select(
    frame: pd.DataFrame, scores: np.ndarray, thresholds: Mapping[str, float]
) -> np.ndarray:
    groups = _group_key(frame).to_numpy()
    if len(groups) != len(scores):
        raise RecalibrationError("test_score_shape_invalid")
    missing = sorted(set(groups) - set(thresholds))
    if missing:
        raise RecalibrationError("unseen_pretrade_group:" + ",".join(missing))
    return np.asarray(
        [score >= thresholds[str(group)] for score, group in zip(scores, groups, strict=True)],
        dtype=bool,
    )


def _bucket(values: np.ndarray, reference: np.ndarray) -> np.ndarray:
    if not np.isfinite(values).all() or not np.isfinite(reference).all():
        raise RecalibrationError("diagnostic_feature_non_finite")
    cutoffs = np.quantile(reference, [0.25, 0.50, 0.75])
    indices = np.atleast_1d(np.searchsorted(cutoffs, values, side="right"))
    return np.asarray([f"Q{index + 1}" for index in indices.tolist()])


def _removed_metrics(rows: pd.DataFrame) -> dict[str, Any]:
    pnl = rows[TARGET_COLUMN].to_numpy(dtype=float)
    profitable = pnl[pnl > 0.0]
    losing = pnl[pnl < 0.0]
    metrics = _metrics(pnl.tolist())
    return {
        "removed_trade_count": len(rows),
        "removed_net_pnl_usdt": float(pnl.sum()),
        "profitable_removed_count": int(len(profitable)),
        "losing_removed_count": int(len(losing)),
        "profitable_removed_pnl_usdt": float(profitable.sum()),
        "losing_removed_pnl_usdt": float(losing.sum()),
        "expectancy": metrics["expectancy"],
        "profit_factor": metrics["profit_factor"],
    }


def _arm_metrics(rows: pd.DataFrame) -> dict[str, Any]:
    metrics = _economic_metrics(rows)
    return {
        "net_pnl_usdt": metrics["net_pnl"],
        "trade_count": metrics["trade_count"],
        "max_drawdown": metrics["max_drawdown"],
        "profit_factor": metrics["profit_factor"],
        "expectancy": metrics["expectancy"],
        "capital_hours_used": metrics["capital_hours_total"],
        "net_pnl_per_capital_hour": metrics["net_pnl_per_capital_hour"],
    }


def _attribute_removed(rows: pd.DataFrame) -> dict[str, Any]:
    dimensions = ("symbol", "side", "symbol_side", "score_bucket", *DIAGNOSTIC_FEATURES)
    breakdown: dict[str, list[dict[str, Any]]] = {}
    for dimension in dimensions:
        breakdown[dimension] = [
            {
                "value": value,
                **_removed_metrics(rows.loc[rows[dimension].astype(str).eq(value)]),
            }
            for value in sorted(rows[dimension].astype(str).unique().tolist())
        ]
    return {
        **_removed_metrics(rows),
        "by_pretrade_attribute": breakdown,
        "regime": {"status": "UNAVAILABLE", "reason": "not_in_frozen_wq6_schema"},
        "confidence": {"status": "UNAVAILABLE", "reason": "not_in_frozen_wq6_schema"},
        "buckets_source": "past_validation_only",
    }


def evaluate_profit_recalibration_oos(
    *,
    dataset: pd.DataFrame,
    features: Sequence[str],
    splits: Sequence[Mapping[str, Any]],
    model_factory: ModelFactory,
) -> dict[str, Any]:
    """Choose all rules before held-out scoring; use OOS labels only ex post."""
    if not splits:
        raise RecalibrationError("walkforward_splits_empty")
    fold_reports: list[dict[str, Any]] = []
    test_frames: list[pd.DataFrame] = []
    seen: set[int] = set()
    for split in splits:
        train = dataset.iloc[list(split["_train_indices"])]
        validation = dataset.iloc[list(split["_validation_indices"])]
        test = dataset.iloc[list(split["_test_indices"])]
        if train.empty or validation.empty or test.empty:
            raise RecalibrationError("empty_fold_partition")
        if train["close_time_utc"].max() > validation["open_time_utc"].min():
            raise RecalibrationError("training_outcome_after_validation_start")
        if validation["close_time_utc"].max() > test["open_time_utc"].min():
            raise RecalibrationError("validation_outcome_after_test_start")
        if (test["feature_cutoff_utc"] > test["open_time_utc"]).any():
            raise RecalibrationError("feature_available_after_decision")
        ids = set(test["trade_sequence"].astype(int))
        if len(ids) != len(test) or seen.intersection(ids):
            raise RecalibrationError("duplicate_oos_trade_sequence")
        seen.update(ids)

        validation_scores, test_scores = _fit_predict(
            train=train,
            validation=validation,
            test=test,
            features=features,
            model_factory=model_factory,
        )
        global_threshold = float(
            _calibrate_threshold(
                validation_scores, validation[TARGET_COLUMN].to_numpy(dtype=float)
            )["threshold"]
        )
        group_thresholds = _calibrate_groups(validation, validation_scores, global_threshold)
        current_selected = test_scores >= global_threshold
        recalibrated_selected = _select(test, test_scores, group_thresholds)
        frame = test.copy()
        frame["_current"] = current_selected
        frame["_recalibrated"] = recalibrated_selected
        frame["symbol_side"] = _group_key(test).to_numpy()
        frame["score_bucket"] = _bucket(test_scores, validation_scores)
        for feature in DIAGNOSTIC_FEATURES:
            if feature not in test.columns or feature not in validation.columns:
                raise RecalibrationError("diagnostic_feature_missing:" + feature)
            frame[feature] = _bucket(
                test[feature].to_numpy(dtype=float),
                validation[feature].to_numpy(dtype=float),
            )
        test_frames.append(frame)
        fold_reports.append(
            {
                "split_id": split["split_id"],
                "train_count": len(train),
                "calibration_count": len(validation),
                "oos_count": len(test),
                "current_threshold": global_threshold,
                "recalibrated_thresholds_by_symbol_side": group_thresholds,
                "threshold_source": "past_validation_only",
                "control_net_pnl_usdt": float(test[TARGET_COLUMN].sum()),
                "current_net_pnl_usdt": float(test.loc[current_selected, TARGET_COLUMN].sum()),
                "recalibrated_net_pnl_usdt": float(
                    test.loc[recalibrated_selected, TARGET_COLUMN].sum()
                ),
            }
        )

    all_test = pd.concat(test_frames, ignore_index=True).sort_values(
        ["open_time_utc", "trade_sequence"], kind="stable"
    )
    current_removed = all_test.loc[~all_test["_current"]]
    recovered = all_test.loc[
        ~all_test["_current"] & all_test["_recalibrated"]
        & all_test[TARGET_COLUMN].gt(0.0)
    ]
    newly_filtered = all_test.loc[
        all_test["_current"] & ~all_test["_recalibrated"]
        & all_test[TARGET_COLUMN].lt(0.0)
    ]
    control = _arm_metrics(all_test)
    current = _arm_metrics(all_test.loc[all_test["_current"]])
    recalibrated = _arm_metrics(all_test.loc[all_test["_recalibrated"]])
    gate_pass = recalibrated["net_pnl_usdt"] > control["net_pnl_usdt"]
    return {
        "status": "ok",
        "decision": "BR10_RECALIBRATED" if gate_pass else "BR10_RECALIBRATE_FAILED",
        "economic_gate_pass": gate_pass,
        "recalibration_rule": "validation_net_pnl_optimal_threshold_by_symbol_side",
        "folds": fold_reports,
        "abstention_attribution": _attribute_removed(current_removed),
        "control": control,
        "br10_current": current,
        "br10_recalibrated": recalibrated,
        "control_net_pnl_usdt": control["net_pnl_usdt"],
        "br10_current_net_pnl_usdt": current["net_pnl_usdt"],
        "br10_recalibrated_net_pnl_usdt": recalibrated["net_pnl_usdt"],
        "recalibrated_minus_current_net_pnl": (
            recalibrated["net_pnl_usdt"] - current["net_pnl_usdt"]
        ),
        "recalibrated_minus_control_net_pnl": (
            recalibrated["net_pnl_usdt"] - control["net_pnl_usdt"]
        ),
        "recovered_profitable_abstention_count": len(recovered),
        "recovered_profitable_abstention_pnl_usdt": float(
            recovered[TARGET_COLUMN].sum()
        ),
        "newly_filtered_losing_trade_count": len(newly_filtered),
        "newly_filtered_losing_trade_pnl_usdt": float(
            newly_filtered[TARGET_COLUMN].sum()
        ),
        "anti_leakage_status": "PASS",
        "selection_inputs": ["past_train_features_and_labels", "past_validation_scores_and_labels", "oos_pretrade_features"],
        "outcome_used_for_selection": False,
    }


def build_br10_profit_recalibration_v1(
    *,
    project_root: str | Path,
    dataset_path: str | Path | None = None,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    split_manifest_path: str | Path | None = None,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    """Load and certify the frozen BR10 cohort, then run no-write OOS analysis."""
    try:
        dataset, features, splits, source = _load_bundle(
            project_root=project_root,
            dataset_path=dataset_path,
            feature_contract_path=feature_contract_path,
            dataset_manifest_path=dataset_manifest_path,
            split_manifest_path=split_manifest_path,
        )
        report = evaluate_profit_recalibration_oos(
            dataset=dataset,
            features=features,
            splits=splits,
            model_factory=model_factory or _default_model_factory,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "source": source,
            "model_family": (
                "injected_test_double" if model_factory is not None
                else "sklearn_hist_gradient_boosting_regressor"
            ),
            "write_performed": False,
            **SAFETY_FLAGS,
            **report,
        }
    except (AblationError, RecalibrationError, OSError, ValueError, TypeError, KeyError) as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": str(exc),
            "decision": "BR10_RECALIBRATE_BLOCKED",
            "anti_leakage_status": "BLOCKED",
            "write_performed": False,
            **SAFETY_FLAGS,
        }
