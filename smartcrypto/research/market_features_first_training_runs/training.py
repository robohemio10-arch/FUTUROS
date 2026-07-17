"""Deterministic research training, backtest, and block Monte Carlo helpers."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import ClassifierMixin
from sklearn.ensemble import (
    ExtraTreesClassifier,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .contracts import MODEL_FEATURE_COLUMNS, MODEL_NAMES


@dataclass(frozen=True)
class TrainingResult:
    predictions: pd.DataFrame
    model_summaries: tuple[dict[str, Any], ...]
    ranking: tuple[dict[str, Any], ...]
    fitted_models: dict[str, ClassifierMixin]
    blockers: tuple[dict[str, Any], ...]


def build_model(name: str, *, seed: int) -> ClassifierMixin:
    """Build one of the four approved deterministic challenger families."""

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
    if name == "extra_trees":
        return ExtraTreesClassifier(
            n_estimators=160,
            min_samples_leaf=3,
            class_weight="balanced",
            random_state=seed,
            n_jobs=1,
        )
    if name == "random_forest":
        return RandomForestClassifier(
            n_estimators=160,
            min_samples_leaf=3,
            class_weight="balanced_subsample",
            random_state=seed,
            n_jobs=1,
        )
    if name == "hist_gradient_boosting":
        return HistGradientBoostingClassifier(
            max_iter=120,
            max_leaf_nodes=15,
            learning_rate=0.05,
            l2_regularization=0.1,
            random_state=seed,
        )
    raise ValueError(f"unsupported_model:{name}")


def build_purged_walkforward_splits(
    frame: pd.DataFrame,
    *,
    embargo_seconds: int,
    max_folds: int = 3,
) -> list[dict[str, Any]]:
    """Create expanding temporal folds with interval purging and embargo."""

    ordered = frame.sort_values(["open_time_utc", "trade_id"], kind="mergesort")
    ordered_indices = ordered.index.to_numpy()
    row_count = len(ordered)
    if row_count < 40:
        return []
    test_size = max(10, row_count // 5)
    first_test = max(20, row_count - test_size * max_folds)
    splits: list[dict[str, Any]] = []
    for fold_id in range(max_folds):
        test_start_position = first_test + fold_id * test_size
        if test_start_position >= row_count:
            break
        test_end_position = min(row_count, test_start_position + test_size)
        test_indices = ordered_indices[test_start_position:test_end_position]
        if len(test_indices) == 0:
            continue
        test_start = pd.Timestamp(frame.loc[test_indices, "open_time_utc"].min())
        cutoff = test_start - pd.Timedelta(seconds=embargo_seconds)
        candidates = ordered_indices[:test_start_position]
        train_close = frame.loc[candidates, "close_time_utc"]
        keep = train_close.lt(cutoff).to_numpy()
        train_indices = candidates[keep]
        if len(train_indices) < 20:
            continue
        splits.append(
            {
                "fold_id": fold_id + 1,
                "train_indices": train_indices.tolist(),
                "test_indices": test_indices.tolist(),
                "test_start_utc": test_start.isoformat(),
                "embargo_cutoff_utc": cutoff.isoformat(),
                "train_candidate_count": int(len(candidates)),
                "train_row_count": int(len(train_indices)),
                "test_row_count": int(len(test_indices)),
                "purged_and_embargoed_row_count": int(len(candidates) - len(train_indices)),
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
    """Fit approved models only on Master rows and evaluate out of sample."""

    eligible = frame.loc[frame["row_status"].eq("ready")].copy()
    blockers: list[dict[str, Any]] = []
    if eligible.empty:
        return TrainingResult(
            predictions=pd.DataFrame(),
            model_summaries=(),
            ranking=(),
            fitted_models={},
            blockers=({"stage": "training", "reason": "no_eligible_master_rows"},),
        )
    classes = sorted(eligible["target_profitable"].dropna().astype(int).unique())
    if len(classes) < 2:
        return TrainingResult(
            predictions=pd.DataFrame(),
            model_summaries=(),
            ranking=(),
            fitted_models={},
            blockers=({"stage": "training", "reason": "single_target_class"},),
        )
    if run_walkforward:
        splits = build_purged_walkforward_splits(
            eligible,
            embargo_seconds=embargo_seconds,
        )
    else:
        splits = build_purged_walkforward_splits(
            eligible,
            embargo_seconds=embargo_seconds,
            max_folds=1,
        )
    if not splits:
        return TrainingResult(
            predictions=pd.DataFrame(),
            model_summaries=(),
            ranking=(),
            fitted_models={},
            blockers=({"stage": "walkforward", "reason": "no_valid_purged_split"},),
        )

    prediction_frames: list[pd.DataFrame] = []
    fold_metrics: dict[str, list[dict[str, Any]]] = {name: [] for name in MODEL_NAMES}
    for split in splits:
        train = eligible.loc[split["train_indices"]]
        test = eligible.loc[split["test_indices"]]
        if train["target_profitable"].nunique() < 2:
            blockers.append(
                {
                    "stage": "walkforward",
                    "fold_id": split["fold_id"],
                    "reason": "single_train_class",
                }
            )
            continue
        x_train = train.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float)
        y_train = train["target_profitable"].astype(int).to_numpy()
        x_test = test.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float)
        y_test = test["target_profitable"].astype(int).to_numpy()
        for model_name in MODEL_NAMES:
            model = build_model(model_name, seed=seed + int(split["fold_id"]))
            model.fit(x_train, y_train)
            probability = _positive_probability(model, x_test)
            decision = probability >= 0.5
            selected_pnl = np.where(decision, test["net_pnl"].to_numpy(dtype=float), 0.0)
            metrics = {
                **classification_metrics(y_test, probability),
                **financial_metrics(selected_pnl),
                "fold_id": int(split["fold_id"]),
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "purged_and_embargoed_rows": int(
                    split["purged_and_embargoed_row_count"]
                ),
                "test_start_utc": split["test_start_utc"],
            }
            fold_metrics[model_name].append(metrics)
            prediction_frames.append(
                pd.DataFrame(
                    {
                        "trade_id": test["trade_id"].astype(str).to_numpy(),
                        "open_time_utc": test["open_time_utc"].to_numpy(),
                        "model_name": model_name,
                        "fold_id": int(split["fold_id"]),
                        "probability": probability,
                        "decision_allow": decision,
                        "observed_net_pnl": test["net_pnl"].to_numpy(dtype=float),
                        "strategy_net_pnl": selected_pnl,
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
    ranking = rank_models(summaries)
    fitted_models: dict[str, ClassifierMixin] = {}
    x_all = eligible.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float)
    y_all = eligible["target_profitable"].astype(int).to_numpy()
    for name in MODEL_NAMES:
        model = build_model(name, seed=seed)
        model.fit(x_all, y_all)
        fitted_models[name] = model
    return TrainingResult(
        predictions=predictions,
        model_summaries=summaries,
        ranking=ranking,
        fitted_models=fitted_models,
        blockers=tuple(blockers),
    )


def evaluate_paper_holdout(
    *,
    master: pd.DataFrame,
    paper: pd.DataFrame,
    seed: int,
    embargo_seconds: int,
) -> dict[str, Any]:
    """Evaluate paper externally with a temporally prior Master fit only."""

    paper_ready = paper.loc[paper["row_status"].eq("ready")].copy()
    if paper_ready.empty:
        return {
            "status": "blocked",
            "reason": "no_eligible_paper_holdout_rows",
            "paper_rows_used_for_fit": 0,
            "paper_rows_used_for_calibration": 0,
            "model_results": [],
        }
    first_paper_open = pd.Timestamp(paper_ready["open_time_utc"].min())
    cutoff = first_paper_open - pd.Timedelta(seconds=embargo_seconds)
    train = master.loc[
        master["row_status"].eq("ready") & master["close_time_utc"].lt(cutoff)
    ].copy()
    if len(train) < 20 or train["target_profitable"].nunique() < 2:
        return {
            "status": "blocked",
            "reason": "insufficient_temporally_prior_master_rows",
            "paper_rows": int(len(paper_ready)),
            "master_fit_rows": int(len(train)),
            "fit_cutoff_utc": cutoff.isoformat(),
            "paper_rows_used_for_fit": 0,
            "paper_rows_used_for_calibration": 0,
            "model_results": [],
        }
    x_train = train.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float)
    y_train = train["target_profitable"].astype(int).to_numpy()
    x_paper = paper_ready.loc[:, MODEL_FEATURE_COLUMNS].to_numpy(dtype=float)
    y_paper = paper_ready["target_profitable"].astype(int).to_numpy()
    results: list[dict[str, Any]] = []
    for name in MODEL_NAMES:
        model = build_model(name, seed=seed)
        model.fit(x_train, y_train)
        probability = _positive_probability(model, x_paper)
        decision = probability >= 0.5
        selected_pnl = np.where(
            decision,
            paper_ready["net_pnl"].to_numpy(dtype=float),
            0.0,
        )
        results.append(
            {
                "model_name": name,
                **classification_metrics(y_paper, probability),
                **financial_metrics(selected_pnl),
                "allowed_trade_count": int(decision.sum()),
                "blocked_trade_count": int((~decision).sum()),
            }
        )
    return {
        "status": "ok",
        "reason": "paper_external_holdout_evaluated",
        "paper_rows": int(len(paper_ready)),
        "master_fit_rows": int(len(train)),
        "fit_cutoff_utc": cutoff.isoformat(),
        "paper_rows_used_for_fit": 0,
        "paper_rows_used_for_calibration": 0,
        "calibration_performed": False,
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
    """Bootstrap contiguous blocks to preserve local temporal dependence."""

    if predictions.empty:
        return []
    results: list[dict[str, Any]] = []
    for model_offset, (name, group) in enumerate(
        predictions.sort_values(["open_time_utc", "trade_id"]).groupby(
            "model_name", sort=True
        )
    ):
        pnl = group["strategy_net_pnl"].to_numpy(dtype=float)
        if len(pnl) == 0:
            continue
        rng = np.random.default_rng(seed + model_offset)
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


def qlib_gate(*, requested: bool) -> dict[str, Any]:
    if not requested:
        return {"status": "not_requested", "reason": "qlib_training_not_requested"}
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
    net_pnl = np.asarray([item["net_pnl"] for item in folds], dtype=float)
    return {
        "model_name": name,
        "fold_count": int(len(folds)),
        "net_pnl": float(net_pnl.sum()),
        "profit_factor": _weighted_metric(folds, "gross_profit", "gross_loss"),
        "expectancy": float(np.mean([item["expectancy"] for item in folds])),
        "max_drawdown": float(max(item["max_drawdown"] for item in folds)),
        "stability_std_net_pnl": float(np.std(net_pnl)),
        "accuracy": float(np.mean([item["accuracy"] for item in folds])),
        "precision": float(np.mean([item["precision"] for item in folds])),
        "recall": float(np.mean([item["recall"] for item in folds])),
        "f1": float(np.mean([item["f1"] for item in folds])),
        "roc_auc": _mean_optional(folds, "roc_auc"),
        "fold_metrics": folds,
    }


def rank_models(summaries: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
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
            "ranking_score": float(row["ranking_score"]),
            "promotion_eligible": False,
        }
        for index, row in frame.iterrows()
    )


def _positive_probability(model: ClassifierMixin, features: np.ndarray) -> np.ndarray:
    probability = model.predict_proba(features)
    return np.asarray(probability[:, 1], dtype=float)


def _weighted_metric(folds: list[dict[str, Any]], gain: str, loss: str) -> float | None:
    numerator = float(sum(item[gain] for item in folds))
    denominator = float(sum(item[loss] for item in folds))
    return numerator / denominator if denominator > 0 else None


def _mean_optional(folds: list[dict[str, Any]], field: str) -> float | None:
    values = [float(item[field]) for item in folds if item.get(field) is not None]
    return float(np.mean(values)) if values else None


def _sample_contiguous_blocks(
    values: np.ndarray,
    *,
    rng: np.random.Generator,
    block_size: int,
) -> np.ndarray:
    size = len(values)
    effective = max(1, min(int(block_size), size))
    output: list[np.ndarray] = []
    while sum(len(block) for block in output) < size:
        start = int(rng.integers(0, size - effective + 1))
        output.append(values[start : start + effective])
    return np.concatenate(output)[:size]
