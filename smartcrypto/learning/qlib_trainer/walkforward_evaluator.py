"""Walk-forward evaluator for research-only ranking challenger backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .dataset_adapter import RankingDatasetBundle
from .ranking_metrics import aggregate_metrics, baseline_comparison, compute_ranking_metrics


@dataclass
class TrainOnlyStandardizer:
    mean_: pd.Series | None = None
    std_: pd.Series | None = None
    fit_row_counts: list[int] | None = None

    def fit(self, frame: pd.DataFrame) -> "TrainOnlyStandardizer":
        self.fit_row_counts = [] if self.fit_row_counts is None else self.fit_row_counts
        self.fit_row_counts.append(int(len(frame)))
        self.mean_ = frame.mean(numeric_only=True)
        std = frame.std(numeric_only=True).replace(0, 1.0).fillna(1.0)
        self.std_ = std
        return self

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        if self.mean_ is None or self.std_ is None:
            raise ValueError("standardizer_not_fitted")
        return (frame - self.mean_) / self.std_


@dataclass
class LinearRankerModel:
    coefficients: np.ndarray
    intercept: float

    def predict(self, frame: pd.DataFrame) -> pd.Series:
        values = frame.to_numpy(dtype=float)
        prediction = values @ self.coefficients + self.intercept
        return pd.Series(prediction, index=frame.index)


class DeterministicLinearRankerBackend:
    backend_name = "research_fallback_linear_ranker"

    def fit(self, features: pd.DataFrame, target: pd.Series) -> LinearRankerModel:
        x = features.to_numpy(dtype=float)
        y = target.to_numpy(dtype=float)
        if x.size == 0:
            return LinearRankerModel(coefficients=np.zeros(features.shape[1]), intercept=0.0)
        augmented = np.column_stack([np.ones(len(x)), x])
        coefficients = np.linalg.pinv(augmented) @ y
        return LinearRankerModel(coefficients=coefficients[1:], intercept=float(coefficients[0]))


def evaluate_walkforward_challenger(
    bundle: RankingDatasetBundle,
    *,
    backend: DeterministicLinearRankerBackend | None = None,
) -> dict[str, Any]:
    """Train and evaluate one deterministic research ranker per split."""

    ranker = backend or DeterministicLinearRankerBackend()
    metrics: list[dict[str, Any]] = []
    scaler_fit_row_counts: list[int] = []
    models: list[dict[str, Any]] = []
    for split in bundle.reconstructed_splits:
        train_indices = split["_train_indices"]
        validation_indices = split["_validation_indices"]
        test_indices = split["_test_indices"]
        train = bundle.dataset.loc[train_indices]
        test = bundle.dataset.loc[test_indices]
        train_features = numeric_features(train, bundle.feature_columns)
        test_features = numeric_features(test, bundle.feature_columns)
        scaler = TrainOnlyStandardizer().fit(train_features)
        scaler_fit_row_counts.extend(scaler.fit_row_counts or [])
        model = ranker.fit(scaler.transform(train_features), pd.to_numeric(train[bundle.primary_target], errors="coerce").fillna(0.0))
        predictions = model.predict(scaler.transform(test_features))
        metrics.append(
            compute_ranking_metrics(
                split_id=split["split_id"],
                train_row_count=len(train_indices),
                validation_row_count=len(validation_indices),
                test_frame=test,
                predictions=predictions,
                baseline_summary=bundle.baseline_summary,
            )
        )
        models.append({"split_id": split["split_id"], "intercept": model.intercept, "coefficients": model.coefficients.tolist()})
    aggregate = aggregate_metrics(metrics)
    return {
        "backend_name": ranker.backend_name,
        "metrics_by_split": metrics,
        "aggregate_metrics": aggregate,
        "baseline_comparison": baseline_comparison(metrics, bundle.baseline_summary),
        "trained_split_count": len(metrics),
        "evaluated_split_count": len(metrics),
        "scaler_fit_row_counts": scaler_fit_row_counts,
        "models": models,
    }


def numeric_features(frame: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    data = pd.DataFrame(index=frame.index)
    for column in feature_columns:
        data[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return data
