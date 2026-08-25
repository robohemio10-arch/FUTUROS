from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

from smartcrypto.qlib_engine.common import QlibEngineConfig, qlib_runtime_status, write_json
from smartcrypto.qlib_engine.sklearn_compatibility import load_sklearn_artifact


def _build_model(config: QlibEngineConfig):
    try:
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=250,
            learning_rate=0.035,
            max_depth=-1,
            num_leaves=31,
            subsample=0.85,
            colsample_bytree=0.85,
            random_state=config.random_state,
            objective="binary",
            n_jobs=-1,
            verbose=-1,
        ), "lightgbm_lgbmclassifier"
    except Exception:
        from sklearn.ensemble import RandomForestClassifier

        return RandomForestClassifier(
            n_estimators=250,
            min_samples_leaf=10,
            random_state=config.random_state,
            n_jobs=-1,
            class_weight="balanced_subsample",
        ), "sklearn_random_forest_fallback"


def _clean_level(value: object) -> str:
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        text = "__missing__"
    text = re.sub(r"[^0-9a-zA-Z_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_").lower()
    return text or "__missing__"


def _feature_kind(series: pd.Series) -> str:
    non_null = series.dropna()
    if non_null.empty:
        return "numeric"

    converted = pd.to_numeric(non_null, errors="coerce")
    numeric_ratio = float(converted.notna().mean())
    return "numeric" if numeric_ratio >= 0.95 else "categorical"


def _fit_feature_transformer(frame: pd.DataFrame, feature_columns: list[str]) -> dict[str, Any]:
    numeric_columns: list[str] = []
    categorical_columns: list[str] = []

    for column in feature_columns:
        if _feature_kind(frame[column]) == "numeric":
            numeric_columns.append(column)
        else:
            categorical_columns.append(column)

    numeric_medians: dict[str, float] = {}
    for column in numeric_columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        median = values.median()
        numeric_medians[column] = 0.0 if pd.isna(median) else float(median)

    categorical_levels: dict[str, list[str]] = {}
    for column in categorical_columns:
        levels = (
            frame[column]
            .map(_clean_level)
            .replace("", "__missing__")
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )
        categorical_levels[column] = sorted(levels)

    model_feature_columns = list(numeric_columns)
    for column in categorical_columns:
        model_feature_columns.extend([f"{column}__{level}" for level in categorical_levels[column]])

    return {
        "source_feature_columns": feature_columns,
        "numeric_columns": numeric_columns,
        "categorical_columns": categorical_columns,
        "numeric_medians": numeric_medians,
        "categorical_levels": categorical_levels,
        "model_feature_columns": model_feature_columns,
    }


def _transform_features(frame: pd.DataFrame, metadata: dict[str, Any]) -> pd.DataFrame:
    index = frame.index
    pieces: list[pd.DataFrame] = []

    numeric_columns = list(metadata.get("numeric_columns", []))
    numeric_medians = dict(metadata.get("numeric_medians", {}))
    if numeric_columns:
        numeric = pd.DataFrame(index=index)
        for column in numeric_columns:
            median = float(numeric_medians.get(column, 0.0))
            numeric[column] = pd.to_numeric(frame[column], errors="coerce").fillna(median).astype(float)
        pieces.append(numeric)

    categorical_columns = list(metadata.get("categorical_columns", []))
    categorical_levels = dict(metadata.get("categorical_levels", {}))
    for column in categorical_columns:
        values = frame[column].map(_clean_level).astype(str)
        encoded = pd.DataFrame(index=index)
        for level in categorical_levels.get(column, []):
            encoded[f"{column}__{level}"] = values.eq(level).astype(float)
        pieces.append(encoded)

    if pieces:
        matrix = pd.concat(pieces, axis=1)
    else:
        matrix = pd.DataFrame(index=index)

    expected_columns = list(metadata.get("model_feature_columns", []))
    for column in expected_columns:
        if column not in matrix.columns:
            matrix[column] = 0.0

    return matrix[expected_columns].astype(float)


def _drop_rows_without_required_data(
    frame: pd.DataFrame,
    *,
    feature_columns: list[str],
    target_column: str | None = None,
) -> pd.DataFrame:
    required = list(feature_columns)
    if target_column:
        required.append(target_column)

    clean = frame.copy()
    if target_column:
        clean = clean.dropna(subset=[target_column])

    for column in feature_columns:
        if _feature_kind(clean[column]) == "numeric":
            clean[column] = pd.to_numeric(clean[column], errors="coerce")

    return clean.dropna(subset=[column for column in feature_columns if column in clean.columns], how="all")


def train_qlib_market_model(
    *,
    dataset_path: str | Path,
    model_output_path: str | Path,
    report_path: str | Path,
    config: QlibEngineConfig,
) -> dict[str, Any]:
    path = Path(dataset_path)
    if not path.exists():
        report = {"status": "blocked", "reason": "qlib_dataset_missing", "dataset_path": str(path)}
        write_json(report_path, report)
        return report

    frame = pd.read_parquet(path)
    missing = sorted(set(config.feature_columns + [config.target_direction_column]).difference(frame.columns))
    if missing:
        report = {"status": "blocked", "reason": "missing_columns", "missing_columns": missing}
        write_json(report_path, report)
        return report

    frame = _drop_rows_without_required_data(
        frame,
        feature_columns=config.feature_columns,
        target_column=config.target_direction_column,
    ).copy()

    if len(frame) < config.min_rows_for_training:
        report = {
            "status": "blocked",
            "reason": "insufficient_rows_for_training",
            "rows": int(len(frame)),
            "min_rows_for_training": config.min_rows_for_training,
        }
        write_json(report_path, report)
        return report

    frame = frame.sort_values(["datetime", "symbol"]).reset_index(drop=True)
    split_index = int(len(frame) * (1 - config.test_size))
    split_index = max(1, min(split_index, len(frame) - 1))

    train = frame.iloc[:split_index].copy()
    test = frame.iloc[split_index:].copy()

    feature_metadata = _fit_feature_transformer(train, config.feature_columns)

    x_train = _transform_features(train, feature_metadata)
    y_train = train[config.target_direction_column].astype(int)

    x_test = _transform_features(test, feature_metadata)
    y_test = test[config.target_direction_column].astype(int)

    model, model_backend = _build_model(config)
    model.fit(x_train, y_train)

    probabilities = model.predict_proba(x_test)[:, 1]
    predicted = (probabilities >= 0.5).astype(int)

    metrics = {
        "accuracy": float(accuracy_score(y_test, predicted)),
        "precision": float(precision_score(y_test, predicted, zero_division=0)),
        "recall": float(recall_score(y_test, predicted, zero_division=0)),
        "f1": float(f1_score(y_test, predicted, zero_division=0)),
    }
    try:
        metrics["roc_auc"] = float(roc_auc_score(y_test, probabilities))
    except Exception:
        metrics["roc_auc"] = None

    target = Path(model_output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    joblib.dump(
        {
            "model": model,
            "feature_columns": config.feature_columns,
            "target_column": config.target_direction_column,
            "model_version": config.model_version,
            "model_backend": model_backend,
            "feature_metadata": feature_metadata,
            "trained_at": datetime.now(timezone.utc).isoformat(),
        },
        tmp,
    )
    tmp.replace(target)

    report = {
        "status": "trained",
        "reason": None,
        "rows": int(len(frame)),
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "source_feature_count": len(config.feature_columns),
        "model_feature_count": len(feature_metadata["model_feature_columns"]),
        "target_column": config.target_direction_column,
        "target_distribution": {str(k): int(v) for k, v in frame[config.target_direction_column].value_counts().sort_index().items()},
        "metrics": metrics,
        "model_output_path": str(target),
        "model_backend": model_backend,
        "feature_metadata": {
            "numeric_columns": feature_metadata["numeric_columns"],
            "categorical_columns": feature_metadata["categorical_columns"],
            "categorical_levels": feature_metadata["categorical_levels"],
        },
        "qlib_runtime": qlib_runtime_status(),
        "production_enabled": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(report_path, report)
    return report


def export_latest_qlib_predictions(
    *,
    market_features_path: str | Path,
    model_path: str | Path,
    output_path: str | Path,
    report_path: str | Path,
    config: QlibEngineConfig,
    sklearn_strict_compatibility: bool = False,
) -> dict[str, Any]:
    source = Path(market_features_path)
    model_file = Path(model_path)
    if not source.exists():
        report = {"status": "blocked", "reason": "market_features_missing", "market_features_path": str(source)}
        write_json(report_path, report)
        return report
    if not model_file.exists():
        report = {"status": "blocked", "reason": "model_missing", "model_path": str(model_file)}
        write_json(report_path, report)
        return report

    payload, sklearn_compatibility = load_sklearn_artifact(
        model_file,
        strict=sklearn_strict_compatibility,
    )
    sklearn_compatibility_payload = sklearn_compatibility.to_dict()
    if sklearn_compatibility.status == "incompatible" and sklearn_strict_compatibility:
        report = {
            "status": "blocked",
            "reason": "sklearn_artifact_incompatible",
            "model_path": str(model_file),
            "sklearn_compatibility": sklearn_compatibility_payload,
            "sklearn_runtime_version": sklearn_compatibility.sklearn_runtime_version,
            "sklearn_artifact_version": sklearn_compatibility.sklearn_artifact_version,
            "sklearn_compatibility_status": sklearn_compatibility.status,
            "sklearn_compatibility_reason": sklearn_compatibility.reason,
        }
        write_json(report_path, report)
        return report

    model = payload["model"]
    source_feature_columns = payload.get("feature_columns", config.feature_columns)
    feature_metadata = payload.get("feature_metadata")

    frame = pd.read_parquet(source)
    missing = sorted(set(source_feature_columns).difference(frame.columns))
    if missing:
        report = {"status": "blocked", "reason": "missing_prediction_columns", "missing_columns": missing}
        write_json(report_path, report)
        return report

    frame = frame.loc[frame["tf"].astype(str).eq(config.timeframe)].copy()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
    frame = _drop_rows_without_required_data(frame, feature_columns=list(source_feature_columns))
    frame = frame.dropna(subset=["ts"]).sort_values(["symbol", "ts"])
    latest = frame.groupby("symbol", as_index=False).tail(1).copy()

    if latest.empty:
        report = {"status": "blocked", "reason": "no_latest_rows"}
        write_json(report_path, report)
        return report

    if feature_metadata is None:
        feature_metadata = _fit_feature_transformer(frame, list(source_feature_columns))

    probabilities = model.predict_proba(_transform_features(latest, feature_metadata))[:, 1]
    generated_at = datetime.now(timezone.utc).isoformat()

    predictions = pd.DataFrame(
        {
            "date": latest["ts"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generated_at": generated_at,
            "symbol": latest["symbol"].astype(str).values,
            "pair": latest["pair"].astype(str).values,
            "tf": latest["tf"].astype(str).values,
            "prob_up": probabilities.astype(float),
            "score": (probabilities * 2 - 1).astype(float),
            "confidence": np.abs(probabilities - 0.5).astype(float),
            "predicted_direction": np.select(
                [
                    probabilities >= config.prediction_threshold,
                    probabilities <= (1.0 - config.prediction_threshold),
                ],
                [1, -1],
                default=0,
            ).astype(int),
            "model_version": config.model_version,
            "model_backend": payload.get("model_backend"),
        }
    )
    if "market_regime" in latest.columns:
        predictions["market_regime"] = latest["market_regime"].astype(str).values
        predictions["market_regime_status"] = "point_in_time"

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    predictions.to_parquet(tmp, index=False)
    tmp.replace(target)

    report = {
        "status": "ok",
        "reason": None,
        "rows": int(len(predictions)),
        "pairs": sorted(predictions["pair"].astype(str).unique().tolist()),
        "symbols": sorted(predictions["symbol"].astype(str).unique().tolist()),
        "output_path": str(target),
        "model_path": str(model_file),
        "generated_at": generated_at,
        "created_at": generated_at,
        "sklearn_compatibility": sklearn_compatibility_payload,
        "sklearn_runtime_version": sklearn_compatibility.sklearn_runtime_version,
        "sklearn_artifact_version": sklearn_compatibility.sklearn_artifact_version,
        "sklearn_compatibility_status": sklearn_compatibility.status,
        "sklearn_compatibility_reason": sklearn_compatibility.reason,
    }
    write_json(report_path, report)
    return report
