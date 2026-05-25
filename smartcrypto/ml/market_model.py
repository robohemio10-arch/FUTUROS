from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

from smartcrypto.ml.market_dataset import (
    build_market_training_dataset,
    load_training_dataset,
    prepare_feature_matrix,
)


@dataclass(frozen=True)
class MarketTrainingReport:
    status: str
    reason: str | None
    rows: int
    train_rows: int
    test_rows: int
    feature_count: int
    target_column: str
    target_distribution: dict[str, int]
    metrics: dict[str, float]
    model_output_path: str | None
    production_enabled: bool
    created_at: str


def train_market_direction_model(
    config_path: str | Path = "config/market_model.yml",
    report_path: str | Path = "data/reports/phase6_market_training_report.json",
) -> MarketTrainingReport:
    build_market_training_dataset(config_path)
    frame, config = load_training_dataset(config_path)
    model_config = config["market_model"]
    target_column = str(model_config["target_column"])
    min_rows = int(model_config.get("min_rows_for_training", 1000))
    test_fraction = float(model_config.get("test_fraction", 0.2))
    random_state = int(model_config.get("random_state", 42))
    model_path = Path(model_config["model_output_path"])

    if target_column not in frame.columns:
        report = _blocked_report("missing_target_column", frame, target_column, model_path=None)
        _write_report(report_path, asdict(report))
        return report

    frame = frame.dropna(subset=[target_column]).copy()
    class_counts = frame[target_column].astype(int).value_counts().sort_index().to_dict()
    class_counts = {str(key): int(value) for key, value in class_counts.items()}

    if len(frame) < min_rows:
        report = _blocked_report("insufficient_market_rows_for_training", frame, target_column, model_path=None, class_counts=class_counts)
        _write_report(report_path, asdict(report))
        return report

    if len(class_counts) < 2:
        report = _blocked_report("single_target_class", frame, target_column, model_path=None, class_counts=class_counts)
        _write_report(report_path, asdict(report))
        return report

    frame = frame.sort_values(["ts", "symbol"]).reset_index(drop=True)
    split_index = max(1, int(len(frame) * (1.0 - test_fraction)))
    train = frame.iloc[:split_index].copy()
    test = frame.iloc[split_index:].copy()

    if train.empty or test.empty:
        report = _blocked_report("empty_train_or_test_split", frame, target_column, model_path=None, class_counts=class_counts)
        _write_report(report_path, asdict(report))
        return report

    train_classes = train[target_column].astype(int).nunique()
    test_classes = test[target_column].astype(int).nunique()
    if train_classes < 2 or test_classes < 1:
        report = _blocked_report("invalid_class_distribution_in_split", frame, target_column, model_path=None, class_counts=class_counts)
        _write_report(report_path, asdict(report))
        return report

    x_train, feature_columns = prepare_feature_matrix(train, config)
    x_test, _ = prepare_feature_matrix(test, config, feature_columns)
    y_train = train[target_column].astype(int)
    y_test = test[target_column].astype(int)

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=8,
        min_samples_leaf=20,
        class_weight="balanced_subsample",
        random_state=random_state,
        n_jobs=-1,
    )
    model.fit(x_train, y_train)

    predictions = model.predict(x_test)
    probabilities = model.predict_proba(x_test)[:, 1] if len(model.classes_) == 2 else np.zeros(len(test))
    metrics = {
        "accuracy": float(accuracy_score(y_test, predictions)),
        "precision": float(precision_score(y_test, predictions, zero_division=0)),
        "recall": float(recall_score(y_test, predictions, zero_division=0)),
        "f1": float(f1_score(y_test, predictions, zero_division=0)),
    }
    if y_test.nunique() == 2:
        metrics["roc_auc"] = float(roc_auc_score(y_test, probabilities))

    model_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "model": model,
        "feature_columns": feature_columns,
        "config": config,
        "target_column": target_column,
        "created_at": _utc_now(),
        "metrics": metrics,
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
    }
    joblib.dump(bundle, model_path)

    report = MarketTrainingReport(
        status="trained",
        reason=None,
        rows=int(len(frame)),
        train_rows=int(len(train)),
        test_rows=int(len(test)),
        feature_count=int(len(feature_columns)),
        target_column=target_column,
        target_distribution=class_counts,
        metrics=metrics,
        model_output_path=str(model_path),
        production_enabled=True,
        created_at=_utc_now(),
    )
    _write_report(report_path, asdict(report))
    return report


def _blocked_report(
    reason: str,
    frame: pd.DataFrame,
    target_column: str,
    model_path: Path | None,
    class_counts: dict[str, int] | None = None,
) -> MarketTrainingReport:
    if class_counts is None and target_column in frame.columns:
        class_counts = {str(k): int(v) for k, v in frame[target_column].astype(int).value_counts().sort_index().to_dict().items()}
    return MarketTrainingReport(
        status="blocked",
        reason=reason,
        rows=int(len(frame)),
        train_rows=0,
        test_rows=0,
        feature_count=0,
        target_column=target_column,
        target_distribution=class_counts or {},
        metrics={},
        model_output_path=str(model_path) if model_path else None,
        production_enabled=False,
        created_at=_utc_now(),
    )


def _write_report(path: str | Path, payload: dict[str, Any]) -> None:
    import json

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
