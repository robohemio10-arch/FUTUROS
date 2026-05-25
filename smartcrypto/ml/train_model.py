from __future__ import annotations

import json
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit


FEATURE_PREFIX = "entry_"


def train_model(
    trade_enriched_path: str | Path,
    model_path: str | Path,
    metrics_path: str | Path,
) -> dict:
    frame = pd.read_parquet(trade_enriched_path)
    frame = frame.sort_values("open_ts").copy()
    frame["target_win"] = (frame["pnl_pct"] > 0).astype(int)

    feature_columns = [column for column in frame.columns if column.startswith(FEATURE_PREFIX)]
    feature_columns = [column for column in feature_columns if pd.api.types.is_numeric_dtype(frame[column])]

    dataset = frame[feature_columns + ["target_win"]].dropna()
    if len(dataset) < 50:
        raise ValueError("not enough rows to train a model")

    x = dataset[feature_columns]
    y = dataset["target_win"]

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=6,
        min_samples_leaf=5,
        random_state=42,
        class_weight="balanced_subsample",
    )

    splitter = TimeSeriesSplit(n_splits=3)
    reports = []

    for train_index, test_index in splitter.split(x):
        model.fit(x.iloc[train_index], y.iloc[train_index])
        probability = model.predict_proba(x.iloc[test_index])[:, 1]
        predicted = (probability >= 0.5).astype(int)

        reports.append(
            {
                "roc_auc": float(roc_auc_score(y.iloc[test_index], probability)),
                "classification_report": classification_report(
                    y.iloc[test_index],
                    predicted,
                    output_dict=True,
                    zero_division=0,
                ),
            }
        )

    model.fit(x, y)

    model_payload = {
        "model": model,
        "feature_columns": feature_columns,
        "model_version": "baseline_random_forest_v1",
    }

    model_destination = Path(model_path)
    model_destination.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model_payload, model_destination)

    metrics = {
        "rows": int(len(dataset)),
        "features": feature_columns,
        "walk_forward": reports,
    }

    metrics_destination = Path(metrics_path)
    metrics_destination.parent.mkdir(parents=True, exist_ok=True)
    metrics_destination.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")

    return metrics
