"""Purged training, fold baselines, financial ranking, and Monte Carlo."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd
from sklearn.base import ClassifierMixin, RegressorMixin
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import HuberRegressor, LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .contracts import (
    CLASSIFIER_MODEL_NAMES,
    MODEL_FEATURE_COLUMNS,
    MODEL_NAMES,
    REGRESSOR_MODEL_NAMES,
)


Estimator = ClassifierMixin | RegressorMixin


@dataclass(frozen=True)
class TrainingResult:
    predictions: pd.DataFrame
    model_summaries: tuple[dict[str, Any], ...]
    fold_baselines: tuple[dict[str, Any], ...]
    fitted_models: dict[str, Estimator]
    blockers: tuple[dict[str, Any], ...]


def build_model(name: str, *, seed: int) -> Estimator:
    if name == "logistic_regression":
        return Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        max_iter=2000,
                        class_weight="balanced",
                        random_state=seed,
                    ),
                ),
            ]
        )
    if name == "extra_trees_classifier":
        return ExtraTreesClassifier(
            n_estimators=160,
            min_samples_leaf=3,
            class_weight="balanced",
            random_state=seed,
            n_jobs=1,
        )
    if name == "random_forest_classifier":
        return RandomForestClassifier(
            n_estimators=160,
            min_samples_leaf=3,
            class_weight="balanced_subsample",
            random_state=seed,
            n_jobs=1,
        )
    if name == "hist_gradient_boosting_classifier":
        return HistGradientBoostingClassifier(
            max_iter=120,
            max_leaf_nodes=15,
            learning_rate=0.05,
            l2_regularization=0.1,
            random_state=seed,
        )
    if name == "huber_regressor":
        return Pipeline(
            [
                ("scale", StandardScaler()),
                ("model", HuberRegressor(max_iter=1000, epsilon=1.35)),
            ]
        )
    if name == "random_forest_regressor":
        return RandomForestRegressor(
            n_estimators=160,
            min_samples_leaf=3,
            random_state=seed,
            n_jobs=1,
        )
    if name == "extra_trees_regressor":
        return ExtraTreesRegressor(
            n_estimators=160,
            min_samples_leaf=3,
            random_state=seed,
            n_jobs=1,
        )
    if name == "hist_gradient_boosting_regressor":
        return HistGradientBoostingRegressor(
            max_iter=120,
            max_leaf_nodes=15,
            learning_rate=0.05,
            l2_regularization=0.1,
            random_state=seed,
        )
    raise ValueError(f"unsupported_model:{name}")


def model_kind(name: str) -> Literal["classifier", "regressor"]:
    if name in CLASSIFIER_MODEL_NAMES:
        return "classifier"
    if name in REGRESSOR_MODEL_NAMES:
        return "regressor"
    raise ValueError(f"unsupported_model:{name}")


def build_purged_walkforward_splits(
    frame: pd.DataFrame,
    *,
    embargo_seconds: int,
    max_folds: int = 3,
) -> list[dict[str, Any]]:
    """Create fit/validation/test windows with purging before each boundary."""

    ordered = frame.sort_values(["open_time_utc", "trade_id"], kind="mergesort")
    ordered_indices = ordered.index.to_numpy()
    row_count = len(ordered)
    if row_count < 60:
        return []
    test_size = max(10, row_count // 5)
    first_test = max(40, row_count - test_size * max_folds)
    splits: list[dict[str, Any]] = []
    for fold_id in range(max_folds):
        test_start_position = first_test + fold_id * test_size
        if test_start_position >= row_count:
            break
        test_end_position = min(row_count, test_start_position + test_size)
        test_indices = ordered_indices[test_start_position:test_end_position]
        test_start = pd.Timestamp(frame.loc[test_indices, "open_time_utc"].min())
        test_cutoff = test_start - pd.Timedelta(seconds=embargo_seconds)
        train_candidates = ordered_indices[:test_start_position]
        train_indices = train_candidates[
            frame.loc[train_candidates, "close_time_utc"].lt(test_cutoff).to_numpy()
        ]
        if len(train_indices) < 40:
            continue
        validation_size = max(10, len(train_indices) // 5)
        validation_indices = train_indices[-validation_size:]
        validation_start = pd.Timestamp(
            frame.loc[validation_indices, "open_time_utc"].min()
        )
        validation_cutoff = validation_start - pd.Timedelta(seconds=embargo_seconds)
        fit_candidates = train_indices[:-validation_size]
        fit_indices = fit_candidates[
            frame.loc[fit_candidates, "close_time_utc"].lt(validation_cutoff).to_numpy()
        ]
        if len(fit_indices) < 20:
            continue
        splits.append(
            {
                "fold_id": fold_id + 1,
                "fit_indices": fit_indices.tolist(),
                "validation_indices": validation_indices.tolist(),
                "train_indices": train_indices.tolist(),
                "test_indices": test_indices.tolist(),
                "validation_start_utc": validation_start.isoformat(),
                "test_start_utc": test_start.isoformat(),
                "embargo_cutoff_utc": test_cutoff.isoformat(),
                "fit_row_count": int(len(fit_indices)),
                "validation_row_count": int(len(validation_indices)),
                "train_row_count": int(len(train_indices)),
                "test_row_count": int(len(test_indices)),
                "purged_and_embargoed_row_count": int(
                    len(train_candidates) - len(train_indices)
                    + len(fit_candidates) - len(fit_indices)
                ),
                "embargo_seconds": int(embargo_seconds),
            }
        )
    return splits


def run_supervised_models(
    frame: pd.DataFrame,
    *,
    seed: int,
    embargo_seconds: int,
    run_walkforward: bool,
) -> TrainingResult:
    eligible = frame.loc[frame["row_status"].eq("ready")].copy()
    if eligible.empty:
        return _blocked_training("no_eligible_master_rows")
    if eligible["target_profitable"].nunique() < 2:
        return _blocked_training("single_target_class")
    splits = build_purged_walkforward_splits(
        eligible,
        embargo_seconds=embargo_seconds,
        max_folds=3 if run_walkforward else 1,
    )
    if not splits:
        return _blocked_training("no_valid_purged_split")

    prediction_frames: list[pd.DataFrame] = []
    fold_metrics: dict[str, list[dict[str, Any]]] = {name: [] for name in MODEL_NAMES}
    fold_baselines: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for split in splits:
        fit = eligible.loc[split["fit_indices"]]
        validation = eligible.loc[split["validation_indices"]]
        train = eligible.loc[split["train_indices"]]
        test = eligible.loc[split["test_indices"]]
        if fit["target_profitable"].nunique() < 2:
            blockers.append(
                {
                    "stage": "walkforward",
                    "fold_id": split["fold_id"],
                    "reason": "single_fit_class",
                }
            )
            continue
        baseline = {
            "fold_id": int(split["fold_id"]),
            "baseline_name": "always_allow",
            **financial_metrics(test["net_pnl"].to_numpy(dtype=float)),
            "test_trade_ids": test["trade_id"].astype(str).tolist(),
        }
        fold_baselines.append(baseline)
        for name in MODEL_NAMES:
            kind = model_kind(name)
            estimator = build_model(name, seed=seed + int(split["fold_id"]))
            threshold: float
            threshold_source: str
            if kind == "classifier":
                estimator.fit(
                    train.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float),
                    train["target_profitable"].astype(int).to_numpy(),
                )
                score = _positive_probability(
                    estimator,
                    test.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float),
                )
                decision = score >= 0.5
                threshold = 0.5
                threshold_source = "fixed_classifier_probability"
                predictive = classification_metrics(
                    test["target_profitable"].astype(int).to_numpy(), score
                )
            else:
                estimator.fit(
                    fit.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float),
                    fit["net_pnl"].to_numpy(dtype=float),
                )
                validation_score = np.asarray(
                    estimator.predict(
                        validation.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float)
                    ),
                    dtype=float,
                )
                threshold = select_expected_pnl_threshold(
                    validation_score,
                    validation["net_pnl"].to_numpy(dtype=float),
                )
                threshold_source = "master_fold_validation_only"
                score = np.asarray(
                    estimator.predict(
                        test.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float)
                    ),
                    dtype=float,
                )
                decision = score >= threshold
                predictive = regression_metrics(test["net_pnl"].to_numpy(dtype=float), score)
            observed = test["net_pnl"].to_numpy(dtype=float)
            selected_pnl = np.where(decision, observed, 0.0)
            financial = financial_metrics(selected_pnl)
            fold_metrics[name].append(
                {
                    "fold_id": int(split["fold_id"]),
                    "model_kind": kind,
                    "fit_rows": int(len(fit)),
                    "validation_rows": int(len(validation)),
                    "train_rows": int(len(train)),
                    "test_rows": int(len(test)),
                    "threshold": float(threshold),
                    "threshold_source": threshold_source,
                    "paper_used_for_threshold": False,
                    "purged_and_embargoed_rows": int(
                        split["purged_and_embargoed_row_count"]
                    ),
                    **predictive,
                    **financial,
                    "baseline_always_allow": {
                        key: baseline[key]
                        for key in (
                            "net_pnl",
                            "profit_factor",
                            "expectancy",
                            "max_drawdown",
                        )
                    },
                }
            )
            prediction_frames.append(
                pd.DataFrame(
                    {
                        "trade_id": test["trade_id"].astype(str).to_numpy(),
                        "open_time_utc": test["open_time_utc"].to_numpy(),
                        "model_name": name,
                        "model_kind": kind,
                        "fold_id": int(split["fold_id"]),
                        "model_score": score,
                        "decision_allow": decision,
                        "selected_threshold": float(threshold),
                        "threshold_source": threshold_source,
                        "observed_net_pnl": observed,
                        "strategy_net_pnl": selected_pnl,
                        "baseline_always_allow_net_pnl": observed,
                        "dataset_partition": "master_walkforward_oos",
                    }
                )
            )
    predictions = (
        pd.concat(prediction_frames, ignore_index=True)
        if prediction_frames
        else pd.DataFrame()
    )
    summaries = tuple(
        summarize_model(name, fold_metrics[name])
        for name in MODEL_NAMES
        if fold_metrics[name]
    )
    fitted: dict[str, Estimator] = {}
    for name in MODEL_NAMES:
        estimator = build_model(name, seed=seed)
        target = (
            eligible["target_profitable"].astype(int).to_numpy()
            if model_kind(name) == "classifier"
            else eligible["net_pnl"].to_numpy(dtype=float)
        )
        estimator.fit(eligible.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float), target)
        fitted[name] = estimator
    return TrainingResult(
        predictions=predictions,
        model_summaries=summaries,
        fold_baselines=tuple(fold_baselines),
        fitted_models=fitted,
        blockers=tuple(blockers),
    )


def select_expected_pnl_threshold(
    validation_predictions: np.ndarray,
    validation_net_pnl: np.ndarray,
) -> float:
    """Select threshold exclusively on a fold's Master validation interval."""

    predictions = np.asarray(validation_predictions, dtype=float)
    pnl = np.asarray(validation_net_pnl, dtype=float)
    if len(predictions) == 0:
        raise ValueError("empty_validation_predictions")
    candidates = np.unique(
        np.concatenate(
            (
                np.quantile(predictions, [0.0, 0.25, 0.5, 0.75, 0.9, 1.0]),
                np.array([0.0]),
            )
        )
    )
    scored = [
        (float(np.where(predictions >= threshold, pnl, 0.0).sum()), float(threshold))
        for threshold in candidates
    ]
    best_score = max(item[0] for item in scored)
    return max(threshold for score, threshold in scored if np.isclose(score, best_score))


def evaluate_paper_holdout(
    *,
    master: pd.DataFrame,
    paper: pd.DataFrame,
    seed: int,
    embargo_seconds: int,
) -> dict[str, Any]:
    paper_ready = paper.loc[paper["row_status"].eq("ready")].copy()
    if paper_ready.empty:
        return _blocked_holdout("no_eligible_paper_holdout_rows")
    first_paper_open = pd.Timestamp(paper_ready["open_time_utc"].min())
    cutoff = first_paper_open - pd.Timedelta(seconds=embargo_seconds)
    prior = master.loc[
        master["row_status"].eq("ready") & master["close_time_utc"].lt(cutoff)
    ].sort_values(["open_time_utc", "trade_id"], kind="mergesort")
    if len(prior) < 60 or prior["target_profitable"].nunique() < 2:
        return {
            **_blocked_holdout("insufficient_temporally_prior_master_rows"),
            "paper_rows": int(len(paper_ready)),
            "master_prior_rows": int(len(prior)),
        }
    validation_size = max(10, len(prior) // 5)
    fit = prior.iloc[:-validation_size]
    validation = prior.iloc[-validation_size:]
    x_paper = paper_ready.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float)
    results: list[dict[str, Any]] = []
    for name in MODEL_NAMES:
        estimator = build_model(name, seed=seed)
        kind = model_kind(name)
        if kind == "classifier":
            estimator.fit(
                prior.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float),
                prior["target_profitable"].astype(int).to_numpy(),
            )
            score = _positive_probability(estimator, x_paper)
            threshold = 0.5
            threshold_source = "fixed_classifier_probability"
        else:
            estimator.fit(
                fit.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float),
                fit["net_pnl"].to_numpy(dtype=float),
            )
            validation_score = np.asarray(
                estimator.predict(
                    validation.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float)
                ),
                dtype=float,
            )
            threshold = select_expected_pnl_threshold(
                validation_score,
                validation["net_pnl"].to_numpy(dtype=float),
            )
            threshold_source = "master_pre_paper_validation_only"
            score = np.asarray(estimator.predict(x_paper), dtype=float)
        decision = score >= threshold
        selected = np.where(
            decision, paper_ready["net_pnl"].to_numpy(dtype=float), 0.0
        )
        results.append(
            {
                "model_name": name,
                "model_kind": kind,
                "threshold": float(threshold),
                "threshold_source": threshold_source,
                "paper_used_for_threshold": False,
                **financial_metrics(selected),
                "allowed_trade_count": int(decision.sum()),
                "blocked_trade_count": int((~decision).sum()),
            }
        )
    return {
        "status": "ok",
        "reason": "paper_external_holdout_evaluated",
        "evaluation_set": "paper_evaluation_set_v1_consumed",
        "paper_rows": int(len(paper_ready)),
        "master_fit_rows": int(len(fit)),
        "master_validation_rows": int(len(validation)),
        "fit_cutoff_utc": cutoff.isoformat(),
        "paper_rows_used_for_fit": 0,
        "paper_rows_used_for_calibration": 0,
        "paper_rows_used_for_threshold": 0,
        "model_results": results,
    }


def build_baselines(frame: pd.DataFrame, *, seed: int) -> list[dict[str, Any]]:
    eligible = frame.loc[frame["row_status"].eq("ready")].copy()
    if eligible.empty:
        return []
    pnl = eligible["net_pnl"].to_numpy(dtype=float)
    side = eligible["side"].astype(str).to_numpy()
    rng = np.random.default_rng(seed)
    policies = {
        "always_allow": np.ones(len(eligible), dtype=bool),
        "always_block": np.zeros(len(eligible), dtype=bool),
        "always_long": side == "long",
        "always_short": side == "short",
        "random_deterministic": rng.random(len(eligible)) >= 0.5,
    }
    return [
        {
            "baseline_name": name,
            **financial_metrics(np.where(mask, pnl, 0.0)),
            "allowed_trade_count": int(mask.sum()),
        }
        for name, mask in policies.items()
    ]


def block_monte_carlo(
    predictions: pd.DataFrame,
    *,
    iterations: int,
    block_size: int,
    seed: int,
) -> list[dict[str, Any]]:
    if predictions.empty:
        return []
    results: list[dict[str, Any]] = []
    ordered = predictions.sort_values(["open_time_utc", "trade_id"])
    for offset, (name, group) in enumerate(ordered.groupby("model_name", sort=True)):
        pnl = group["strategy_net_pnl"].to_numpy(dtype=float)
        rng = np.random.default_rng(seed + offset)
        totals = np.empty(iterations, dtype=float)
        drawdowns = np.empty(iterations, dtype=float)
        for iteration in range(iterations):
            sampled = _sample_contiguous_blocks(pnl, rng=rng, block_size=block_size)
            totals[iteration] = float(sampled.sum())
            drawdowns[iteration] = max_drawdown(sampled)
        results.append(
            {
                "model_name": str(name),
                "method": "contiguous_block_bootstrap",
                "iterations": int(iterations),
                "block_size": int(block_size),
                "net_pnl_p05": float(np.quantile(totals, 0.05)),
                "net_pnl_median": float(np.median(totals)),
                "net_pnl_p95": float(np.quantile(totals, 0.95)),
                "probability_negative_net_pnl": float(np.mean(totals < 0)),
                "max_drawdown_p95": float(np.quantile(drawdowns, 0.95)),
            }
        )
    return results


def build_candidate_rankings(
    summaries: tuple[dict[str, Any], ...],
    monte_carlo: list[dict[str, Any]],
    *,
    maximum_negative_pnl_probability: float,
    leakage_detected: bool,
    paper_rows_used_for_fit: int,
    paper_rows_used_for_threshold: int,
) -> dict[str, Any]:
    diagnostic = diagnostic_rank_models(summaries)
    monte_carlo_by_model = {item["model_name"]: item for item in monte_carlo}
    eligibility: list[dict[str, Any]] = []
    for summary in summaries:
        reasons: list[str] = []
        baseline = summary["baseline_always_allow"]
        _require_gt(summary, baseline, "net_pnl", reasons)
        _require_gt(summary, baseline, "profit_factor", reasons)
        _require_gt(summary, baseline, "expectancy", reasons)
        if summary["positive_fold_count"] <= summary["fold_count"] / 2:
            reasons.append("positive_fold_majority_not_met")
        simulation = monte_carlo_by_model.get(summary["model_name"])
        if simulation is None:
            reasons.append("monte_carlo_missing")
        else:
            if simulation["net_pnl_median"] <= 0:
                reasons.append("monte_carlo_median_not_positive")
            if (
                simulation["probability_negative_net_pnl"]
                >= maximum_negative_pnl_probability
            ):
                reasons.append("negative_pnl_probability_limit_not_met")
        if leakage_detected:
            reasons.append("leakage_detected")
        if paper_rows_used_for_fit != 0:
            reasons.append("paper_used_for_fit")
        if paper_rows_used_for_threshold != 0:
            reasons.append("paper_used_for_threshold")
        eligibility.append(
            {
                "model_name": summary["model_name"],
                "eligible": not reasons,
                "ineligibility_reasons": reasons,
            }
        )
    diagnostic_by_name = {item["model_name"]: item for item in diagnostic}
    eligible = [
        {
            **diagnostic_by_name[item["model_name"]],
            "eligible": True,
            "ineligibility_reasons": [],
        }
        for item in eligibility
        if item["eligible"]
    ]
    eligible.sort(key=lambda item: (item["rank"], item["model_name"]))
    for rank, item in enumerate(eligible, start=1):
        item["eligible_rank"] = rank
    selected = eligible[0] if eligible else None
    return {
        "diagnostic_ranking": list(diagnostic),
        "eligible_candidate_ranking": eligible,
        "candidate_eligibility": eligibility,
        "selected_candidate": selected,
        "decision": (
            "ELIGIBLE_RESEARCH_CANDIDATE_IDENTIFIED"
            if selected is not None
            else "NO_ELIGIBLE_MODEL_CANDIDATE"
        ),
    }


def diagnostic_rank_models(
    summaries: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    if not summaries:
        return ()
    frame = pd.DataFrame(summaries)
    pf = pd.to_numeric(frame["profit_factor"], errors="coerce").fillna(0.0)
    frame["ranking_score"] = (
        frame["net_pnl"].rank(pct=True, method="average")
        + pf.rank(pct=True, method="average")
        + frame["expectancy"].rank(pct=True, method="average")
        - frame["max_drawdown"].rank(pct=True, method="average")
        - frame["stability_std_net_pnl"].rank(pct=True, method="average")
    )
    frame = frame.sort_values(
        ["ranking_score", "net_pnl", "model_name"],
        ascending=[False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    return tuple(
        {
            "rank": int(index + 1),
            "model_name": str(row["model_name"]),
            "model_kind": str(row["model_kind"]),
            "ranking_score": float(row["ranking_score"]),
            "promotion_eligible": False,
        }
        for index, row in frame.iterrows()
    )


def rank_models(summaries: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    """Backward-compatible diagnostic alias without eligibility authority."""

    return diagnostic_rank_models(summaries)


def qlib_gate(*, requested: bool, environment_allowed: bool = True) -> dict[str, Any]:
    if not requested:
        return {"status": "not_requested", "reason": "qlib_training_not_requested"}
    if not environment_allowed:
        return {
            "status": "blocked",
            "reason": "canonical_training_environment_mismatch",
            "training_performed": False,
        }
    spec = importlib.util.find_spec("qlib")
    if spec is None:
        return {
            "status": "blocked",
            "reason": "qlib_backend_unavailable",
            "qlib_importable": False,
            "training_performed": False,
        }
    return {
        "status": "blocked",
        "reason": "qlib_provider_not_configured_runtime_access_forbidden",
        "qlib_importable": True,
        "qlib_package_path": spec.origin,
        "training_performed": False,
    }


def classification_metrics(y_true: np.ndarray, probability: np.ndarray) -> dict[str, Any]:
    decision = probability >= 0.5
    result: dict[str, Any] = {
        "accuracy": float(accuracy_score(y_true, decision)),
        "precision": float(precision_score(y_true, decision, zero_division=0)),
        "recall": float(recall_score(y_true, decision, zero_division=0)),
        "f1": float(f1_score(y_true, decision, zero_division=0)),
        "roc_auc": None,
    }
    if len(np.unique(y_true)) == 2:
        result["roc_auc"] = float(roc_auc_score(y_true, probability))
    return result


def regression_metrics(y_true: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    return {
        "mae": float(mean_absolute_error(y_true, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, predicted))),
    }


def financial_metrics(pnl_values: np.ndarray | pd.Series) -> dict[str, Any]:
    pnl = np.asarray(pnl_values, dtype=float)
    gross_profit = float(pnl[pnl > 0].sum()) if len(pnl) else 0.0
    gross_loss = float(-pnl[pnl < 0].sum()) if len(pnl) else 0.0
    nonzero = pnl[pnl != 0]
    return {
        "trade_count": int(len(pnl)),
        "active_trade_count": int(len(nonzero)),
        "net_pnl": float(pnl.sum()) if len(pnl) else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "expectancy": float(pnl.mean()) if len(pnl) else 0.0,
        "win_rate": float(np.mean(nonzero > 0)) if len(nonzero) else 0.0,
        "max_drawdown": max_drawdown(pnl),
    }


def max_drawdown(pnl_values: np.ndarray | pd.Series) -> float:
    pnl = np.asarray(pnl_values, dtype=float)
    if len(pnl) == 0:
        return 0.0
    equity = np.cumsum(pnl)
    peaks = np.maximum.accumulate(np.concatenate(([0.0], equity)))[:-1]
    return float(np.max(peaks - equity))


def summarize_model(name: str, folds: list[dict[str, Any]]) -> dict[str, Any]:
    model_financial = _aggregate_financial(folds, prefix="")
    baseline_financial = _aggregate_financial(
        [item["baseline_always_allow"] for item in folds], prefix=""
    )
    net_by_fold = np.asarray([item["net_pnl"] for item in folds], dtype=float)
    predictive_fields = ("accuracy", "precision", "recall", "f1", "roc_auc", "mae", "rmse")
    predictive = {
        field: _mean_optional(folds, field)
        for field in predictive_fields
        if any(field in item for item in folds)
    }
    return {
        "model_name": name,
        "model_kind": model_kind(name),
        "fold_count": int(len(folds)),
        "positive_fold_count": int(np.sum(net_by_fold > 0)),
        "majority_folds_positive": bool(np.sum(net_by_fold > 0) > len(folds) / 2),
        "stability_std_net_pnl": float(np.std(net_by_fold)),
        **model_financial,
        **predictive,
        "baseline_always_allow": baseline_financial,
        "fold_metrics": folds,
    }


def _aggregate_financial(
    records: list[dict[str, Any]], *, prefix: str
) -> dict[str, Any]:
    gross_profit = float(sum(float(item.get("gross_profit", 0.0)) for item in records))
    gross_loss = float(sum(float(item.get("gross_loss", 0.0)) for item in records))
    net_pnl = float(sum(float(item.get("net_pnl", 0.0)) for item in records))
    trade_count = int(sum(int(item.get("trade_count", 0)) for item in records))
    return {
        f"{prefix}net_pnl": net_pnl,
        f"{prefix}gross_profit": gross_profit,
        f"{prefix}gross_loss": gross_loss,
        f"{prefix}profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        f"{prefix}expectancy": net_pnl / trade_count if trade_count else 0.0,
        f"{prefix}max_drawdown": float(
            max((float(item.get("max_drawdown", 0.0)) for item in records), default=0.0)
        ),
        f"{prefix}trade_count": trade_count,
    }


def _require_gt(
    model: dict[str, Any],
    baseline: dict[str, Any],
    field: str,
    reasons: list[str],
) -> None:
    left = model.get(field)
    right = baseline.get(field)
    if left is None or right is None or float(left) <= float(right):
        reasons.append(f"{field}_not_above_always_allow")


def _positive_probability(model: Estimator, features: np.ndarray) -> np.ndarray:
    if not hasattr(model, "predict_proba"):
        raise TypeError("classifier_missing_predict_proba")
    return np.asarray(model.predict_proba(features)[:, 1], dtype=float)


def _mean_optional(records: list[dict[str, Any]], field: str) -> float | None:
    values = [float(item[field]) for item in records if item.get(field) is not None]
    return float(np.mean(values)) if values else None


def _sample_contiguous_blocks(
    values: np.ndarray,
    *,
    rng: np.random.Generator,
    block_size: int,
) -> np.ndarray:
    size = len(values)
    effective = max(1, min(int(block_size), size))
    blocks: list[np.ndarray] = []
    while sum(len(block) for block in blocks) < size:
        start = int(rng.integers(0, size - effective + 1))
        blocks.append(values[start : start + effective])
    return np.concatenate(blocks)[:size]


def _blocked_training(reason: str) -> TrainingResult:
    return TrainingResult(
        predictions=pd.DataFrame(),
        model_summaries=(),
        fold_baselines=(),
        fitted_models={},
        blockers=({"stage": "training", "reason": reason},),
    )


def _blocked_holdout(reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "paper_rows_used_for_fit": 0,
        "paper_rows_used_for_calibration": 0,
        "paper_rows_used_for_threshold": 0,
        "model_results": [],
    }
