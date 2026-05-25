from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer


@dataclass(frozen=True)
class BaselineTrainingResult:
    status: str
    model_path: str | None
    feature_count: int
    feature_columns: list[str]
    metrics: dict[str, float | None]
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


NON_FEATURE_COLUMNS = {
    "trade_id",
    "symbol",
    "target_win",
    "return_pct",
    "open_ts",
    "close_ts",
    "horario_abertura",
    "horario_fechamento",
    "horario_transacao",
    "moeda",
    "fechar_side",
    "order_id",
    "direcao_liquidez",
}


def select_feature_columns(frame: pd.DataFrame, target_column: str = "target_win") -> list[str]:
    forbidden = set(NON_FEATURE_COLUMNS)
    forbidden.add(target_column)

    numeric_columns = []
    for column in frame.columns:
        if column in forbidden:
            continue
        if column.endswith("_ts"):
            continue
        if pd.api.types.is_numeric_dtype(frame[column]):
            numeric_columns.append(column)

    return numeric_columns


def build_feature_matrix(frame: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    if not feature_columns:
        raise ValueError("Nenhuma feature numérica disponível.")

    features = frame[feature_columns].copy()

    for column in features.columns:
        features[column] = pd.to_numeric(features[column], errors="coerce")

    return features.replace([np.inf, -np.inf], np.nan)


def train_baseline_classifier(
    frame: pd.DataFrame,
    target_column: str,
    model_path: str | Path,
    random_state: int = 42,
    test_size_fraction: float = 0.25,
) -> BaselineTrainingResult:
    feature_columns = select_feature_columns(frame, target_column)
    features = build_feature_matrix(frame, feature_columns)
    target = pd.to_numeric(frame[target_column], errors="coerce").fillna(0).astype(int)

    stratify = target if target.nunique() > 1 and target.value_counts().min() >= 2 else None

    x_train, x_test, y_train, y_test = train_test_split(
        features,
        target,
        test_size=test_size_fraction,
        random_state=random_state,
        shuffle=True,
        stratify=stratify,
    )

    model = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler(with_mean=False)),
            (
                "classifier",
                RandomForestClassifier(
                    n_estimators=250,
                    max_depth=6,
                    min_samples_leaf=2,
                    random_state=random_state,
                    class_weight="balanced_subsample",
                ),
            ),
        ]
    )

    model.fit(x_train, y_train)
    predictions = model.predict(x_test)

    probabilities = None
    roc_auc = None

    if hasattr(model, "predict_proba") and y_test.nunique() > 1:
        try:
            probabilities = model.predict_proba(x_test)[:, 1]
            roc_auc = float(roc_auc_score(y_test, probabilities))
        except Exception:
            roc_auc = None

    metrics = {
        "accuracy": float(accuracy_score(y_test, predictions)),
        "precision": float(precision_score(y_test, predictions, zero_division=0)),
        "recall": float(recall_score(y_test, predictions, zero_division=0)),
        "f1": float(f1_score(y_test, predictions, zero_division=0)),
        "roc_auc": roc_auc,
        "train_rows": float(len(x_train)),
        "test_rows": float(len(x_test)),
    }

    output_path = Path(model_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "model": model,
        "feature_columns": feature_columns,
        "target_column": target_column,
        "metrics": metrics,
    }

    joblib.dump(payload, output_path)

    return BaselineTrainingResult(
        status="trained",
        model_path=str(output_path),
        feature_count=len(feature_columns),
        feature_columns=feature_columns,
        metrics=metrics,
        reason=None,
    )
