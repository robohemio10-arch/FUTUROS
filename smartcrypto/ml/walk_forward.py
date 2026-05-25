from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer

from smartcrypto.ml.baseline_model import build_feature_matrix, select_feature_columns


@dataclass(frozen=True)
class WalkForwardResult:
    status: str
    reason: str | None
    folds: list[dict[str, Any]]
    aggregate_metrics: dict[str, float | None]
    feature_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sort_for_walk_forward(frame: pd.DataFrame) -> pd.DataFrame:
    candidates = ["open_1m_ts", "open_5m_ts", "trade_id"]

    for column in candidates:
        if column in frame.columns:
            sorted_frame = frame.copy()

            if column.endswith("_ts"):
                sorted_frame[column] = pd.to_datetime(sorted_frame[column], utc=True, errors="coerce")

            return sorted_frame.sort_values(column, kind="stable").reset_index(drop=True)

    return frame.reset_index(drop=True)


def run_walk_forward_validation(
    frame: pd.DataFrame,
    target_column: str,
    min_rows: int,
    random_state: int = 42,
    n_splits: int = 5,
) -> WalkForwardResult:
    if len(frame) < min_rows:
        return WalkForwardResult(
            status="blocked",
            reason="insufficient_trades_for_walk_forward",
            folds=[],
            aggregate_metrics={},
            feature_count=0,
        )

    frame = _sort_for_walk_forward(frame)
    feature_columns = select_feature_columns(frame, target_column)

    if not feature_columns:
        return WalkForwardResult(
            status="blocked",
            reason="no_numeric_features",
            folds=[],
            aggregate_metrics={},
            feature_count=0,
        )

    target = pd.to_numeric(frame[target_column], errors="coerce").fillna(0).astype(int)

    if target.nunique() < 2:
        return WalkForwardResult(
            status="blocked",
            reason="insufficient_target_classes",
            folds=[],
            aggregate_metrics={},
            feature_count=len(feature_columns),
        )

    features = build_feature_matrix(frame, feature_columns)
    total_rows = len(frame)
    effective_splits = min(n_splits, max(2, total_rows // 20))
    fold_size = max(1, total_rows // (effective_splits + 1))
    folds = []

    for fold_index in range(1, effective_splits + 1):
        train_end = fold_size * fold_index
        test_end = min(total_rows, train_end + fold_size)

        if test_end <= train_end:
            continue

        x_train = features.iloc[:train_end]
        y_train = target.iloc[:train_end]
        x_test = features.iloc[train_end:test_end]
        y_test = target.iloc[train_end:test_end]

        if y_train.nunique() < 2 or y_test.empty:
            folds.append(
                {
                    "fold": fold_index,
                    "status": "skipped",
                    "reason": "insufficient_classes_in_fold",
                    "train_rows": int(len(x_train)),
                    "test_rows": int(len(x_test)),
                }
            )
            continue

        model = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler(with_mean=False)),
                (
                    "classifier",
                    RandomForestClassifier(
                        n_estimators=200,
                        max_depth=5,
                        min_samples_leaf=2,
                        random_state=random_state + fold_index,
                        class_weight="balanced_subsample",
                    ),
                ),
            ]
        )

        model.fit(x_train, y_train)
        predictions = model.predict(x_test)

        roc_auc = None
        if y_test.nunique() > 1:
            try:
                probabilities = model.predict_proba(x_test)[:, 1]
                roc_auc = float(roc_auc_score(y_test, probabilities))
            except Exception:
                roc_auc = None

        folds.append(
            {
                "fold": fold_index,
                "status": "ok",
                "train_rows": int(len(x_train)),
                "test_rows": int(len(x_test)),
                "accuracy": float(accuracy_score(y_test, predictions)),
                "precision": float(precision_score(y_test, predictions, zero_division=0)),
                "recall": float(recall_score(y_test, predictions, zero_division=0)),
                "f1": float(f1_score(y_test, predictions, zero_division=0)),
                "roc_auc": roc_auc,
            }
        )

    valid_folds = [fold for fold in folds if fold.get("status") == "ok"]

    if not valid_folds:
        return WalkForwardResult(
            status="blocked",
            reason="no_valid_walk_forward_folds",
            folds=folds,
            aggregate_metrics={},
            feature_count=len(feature_columns),
        )

    metric_names = ["accuracy", "precision", "recall", "f1", "roc_auc"]
    aggregate = {}

    for metric in metric_names:
        values = [
            fold[metric]
            for fold in valid_folds
            if fold.get(metric) is not None and not pd.isna(fold.get(metric))
        ]
        aggregate[metric] = float(np.mean(values)) if values else None

    aggregate["valid_folds"] = float(len(valid_folds))

    return WalkForwardResult(
        status="validated",
        reason=None,
        folds=folds,
        aggregate_metrics=aggregate,
        feature_count=len(feature_columns),
    )
