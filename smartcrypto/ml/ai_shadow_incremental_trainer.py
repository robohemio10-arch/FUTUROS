from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from smartcrypto.market.market_feature_schema import lookahead_columns


DEFAULT_INPUT_PATH = Path("data/features/incremental_training_microbatch.parquet")
DEFAULT_MODEL_DIR = Path("data/models/shadow")
DEFAULT_REPORT_PATH = Path("data/reports/ai_shadow_incremental_trainer_report.json")
TARGET_COLUMN = "target_profitable"
MINIMUM_RECOMMENDED_ROWS = 100
RANDOM_STATE = 42
NON_MODEL_FEATURE_COLUMNS = {
    "feature_timestamp_utc",
    "feature_age_seconds",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safety_payload() -> dict[str, Any]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def blocked_report(
    *,
    reason: str,
    input_path: Path,
    model_dir: Path,
    report_path: Path,
    rows: int = 0,
    feature_columns: list[str] | None = None,
    target_column: str = TARGET_COLUMN,
    lookahead: list[str] | None = None,
    blocking_errors: list[str] | None = None,
) -> dict[str, Any]:
    report = {
        "status": "blocked",
        "reason": reason,
        "input_path": str(input_path),
        "model_dir": str(model_dir),
        "report_path": str(report_path),
        "model_path": None,
        "metadata_path": None,
        "model_id": None,
        "model_version": None,
        "input_rows": int(rows),
        "feature_columns": feature_columns or [],
        "feature_count": len(feature_columns or []),
        "target_column": target_column,
        "class_balance": {},
        "metrics": {},
        "sample_warning": rows < MINIMUM_RECOMMENDED_ROWS,
        "minimum_recommended_rows": MINIMUM_RECOMMENDED_ROWS,
        "promotion_status": "blocked",
        "auto_promote": False,
        "lookahead_columns": sorted(lookahead or []),
        "lookahead_columns_count": len(lookahead or []),
        "blocking_errors": blocking_errors or [reason],
        "write_performed": False,
        "trained_at_utc": utc_now(),
        **safety_payload(),
    }
    write_json(report_path, report)
    return report


def select_feature_columns(frame: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for column in frame.columns:
        name = str(column)
        if not name.startswith("feature_"):
            continue
        if name in NON_MODEL_FEATURE_COLUMNS or name.endswith("_utc") or name.endswith("_ts"):
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
        if numeric.notna().any():
            columns.append(name)
    return columns


def prepare_training_frame(
    frame: pd.DataFrame,
    *,
    feature_columns: list[str],
    target_column: str,
) -> tuple[pd.DataFrame, pd.Series]:
    features = frame[feature_columns].copy()
    for column in feature_columns:
        features[column] = pd.to_numeric(features[column], errors="coerce")
    features = features.replace([np.inf, -np.inf], np.nan)
    target = pd.to_numeric(frame[target_column], errors="coerce")
    valid = target.notna()
    return features.loc[valid].reset_index(drop=True), target.loc[valid].astype(int).clip(0, 1).reset_index(drop=True)


def build_model() -> Pipeline:
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    max_iter=1000,
                    random_state=RANDOM_STATE,
                    solver="lbfgs",
                    class_weight="balanced",
                ),
            ),
        ]
    )


def compute_metrics(model: Pipeline, x_test: pd.DataFrame, y_test: pd.Series) -> dict[str, float | int | None]:
    predictions = model.predict(x_test)
    roc_auc: float | None = None
    if y_test.nunique() > 1 and hasattr(model, "predict_proba"):
        try:
            roc_auc = float(roc_auc_score(y_test, model.predict_proba(x_test)[:, 1]))
        except Exception:
            roc_auc = None
    return {
        "accuracy": float(accuracy_score(y_test, predictions)),
        "precision": float(precision_score(y_test, predictions, zero_division=0)),
        "recall": float(recall_score(y_test, predictions, zero_division=0)),
        "f1": float(f1_score(y_test, predictions, zero_division=0)),
        "roc_auc": roc_auc,
        "train_rows": 0,
        "test_rows": int(len(x_test)),
    }


def train_and_score(features: pd.DataFrame, target: pd.Series) -> tuple[Pipeline, dict[str, Any]]:
    class_counts = target.value_counts()
    can_split = len(features) >= 8 and len(class_counts) == 2 and int(class_counts.min()) >= 2
    model = build_model()

    if can_split:
        x_train, x_test, y_train, y_test = train_test_split(
            features,
            target,
            test_size=0.25,
            random_state=RANDOM_STATE,
            shuffle=True,
            stratify=target,
        )
        model.fit(x_train, y_train)
        metrics = compute_metrics(model, x_test, y_test)
        metrics["train_rows"] = int(len(x_train))
        metrics["validation_mode"] = "deterministic_stratified_holdout"
        return model, metrics

    model.fit(features, target)
    metrics = compute_metrics(model, features, target)
    metrics["train_rows"] = int(len(features))
    metrics["test_rows"] = 0
    metrics["validation_mode"] = "in_sample_small_sample_diagnostic"
    return model, metrics


def stable_model_version(input_path: Path, rows: int, feature_columns: list[str], trained_at: str) -> str:
    material = json.dumps(
        {
            "input_path": str(input_path),
            "rows": rows,
            "feature_columns": feature_columns,
            "trained_at": trained_at,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    timestamp = pd.Timestamp(trained_at).strftime("%Y%m%dT%H%M%SZ")
    return f"shadow_incremental_{timestamp}_{digest}"


def class_balance(target: pd.Series) -> dict[str, int]:
    counts = target.value_counts().sort_index()
    return {str(int(label)): int(count) for label, count in counts.items()}


def train_ai_shadow_incremental_model(
    *,
    input_path: str | Path = DEFAULT_INPUT_PATH,
    model_dir: str | Path = DEFAULT_MODEL_DIR,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    strict: bool = False,
) -> dict[str, Any]:
    input_file = Path(input_path)
    output_dir = Path(model_dir)
    report_file = Path(report_path)

    if not input_file.exists():
        return blocked_report(
            reason="missing_input",
            input_path=input_file,
            model_dir=output_dir,
            report_path=report_file,
            blocking_errors=[f"missing_input:{input_file}"],
        )

    frame = pd.read_parquet(input_file)
    lookahead = lookahead_columns(frame)
    if lookahead:
        return blocked_report(
            reason="lookahead_columns_detected",
            input_path=input_file,
            model_dir=output_dir,
            report_path=report_file,
            rows=len(frame),
            lookahead=lookahead,
            blocking_errors=[f"lookahead_columns:{lookahead}"],
        )
    if TARGET_COLUMN not in frame.columns:
        return blocked_report(
            reason="missing_target_column",
            input_path=input_file,
            model_dir=output_dir,
            report_path=report_file,
            rows=len(frame),
            blocking_errors=[f"missing_target_column:{TARGET_COLUMN}"],
        )

    feature_columns = select_feature_columns(frame)
    if not feature_columns:
        return blocked_report(
            reason="missing_numeric_feature_columns",
            input_path=input_file,
            model_dir=output_dir,
            report_path=report_file,
            rows=len(frame),
            target_column=TARGET_COLUMN,
            blocking_errors=["missing_numeric_feature_columns:feature_*"],
        )

    features, target = prepare_training_frame(frame, feature_columns=feature_columns, target_column=TARGET_COLUMN)
    if strict and len(target) != len(frame):
        return blocked_report(
            reason="invalid_target_rows",
            input_path=input_file,
            model_dir=output_dir,
            report_path=report_file,
            rows=len(frame),
            feature_columns=feature_columns,
            target_column=TARGET_COLUMN,
            blocking_errors=[f"invalid_target_rows:{len(frame) - len(target)}"],
        )
    if target.empty:
        return blocked_report(
            reason="empty_target_after_validation",
            input_path=input_file,
            model_dir=output_dir,
            report_path=report_file,
            rows=len(frame),
            feature_columns=feature_columns,
            target_column=TARGET_COLUMN,
        )
    if target.nunique() < 2:
        return blocked_report(
            reason="single_target_class",
            input_path=input_file,
            model_dir=output_dir,
            report_path=report_file,
            rows=len(frame),
            feature_columns=feature_columns,
            target_column=TARGET_COLUMN,
            blocking_errors=["target_profitable_requires_two_classes"],
        )

    trained_at = utc_now()
    model, metrics = train_and_score(features, target)
    model_id = "ai_shadow_incremental_logistic_regression"
    model_version = stable_model_version(input_file, len(frame), feature_columns, trained_at)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / f"{model_version}.joblib"
    metadata_path = output_dir / f"{model_version}.metadata.json"
    sample_warning = len(frame) < MINIMUM_RECOMMENDED_ROWS

    metadata = {
        "model_id": model_id,
        "model_version": model_version,
        "trained_at_utc": trained_at,
        "input_path": str(input_file),
        "input_rows": int(len(frame)),
        "feature_columns": feature_columns,
        "target_column": TARGET_COLUMN,
        "class_balance": class_balance(target),
        "metrics": metrics,
        "promotion_status": "pending",
        "auto_promote": False,
        "sample_warning": sample_warning,
        "minimum_recommended_rows": MINIMUM_RECOMMENDED_ROWS,
        **safety_payload(),
    }
    bundle = {
        "model": model,
        "metadata": metadata,
        "feature_columns": feature_columns,
        "target_column": TARGET_COLUMN,
    }
    joblib.dump(bundle, model_path)
    write_json(metadata_path, metadata)

    report = {
        "status": "ok",
        "reason": "ok",
        "input_path": str(input_file),
        "model_dir": str(output_dir),
        "report_path": str(report_file),
        "model_path": str(model_path),
        "metadata_path": str(metadata_path),
        "model_id": model_id,
        "model_version": model_version,
        "trained_at_utc": trained_at,
        "input_rows": int(len(frame)),
        "feature_columns": feature_columns,
        "feature_count": len(feature_columns),
        "target_column": TARGET_COLUMN,
        "class_balance": metadata["class_balance"],
        "metrics": metrics,
        "sample_warning": sample_warning,
        "minimum_recommended_rows": MINIMUM_RECOMMENDED_ROWS,
        "promotion_status": "pending",
        "auto_promote": False,
        "lookahead_columns": [],
        "lookahead_columns_count": 0,
        "blocking_errors": [],
        "write_performed": True,
        **safety_payload(),
    }
    write_json(report_file, report)
    return report
