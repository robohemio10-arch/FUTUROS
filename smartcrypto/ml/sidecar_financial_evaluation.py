from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.ml.anti_leakage_audit import BLOCKED, audit_feature_leakage
from smartcrypto.ml.baseline_evaluation import build_baseline_predictions, compute_metrics
from smartcrypto.ml.walkforward_split import create_walkforward_splits


OK = "OK"
WARNING = "WARNING"
BLOCKED = "BLOCKED"

FORBIDDEN_FEATURE_COLUMNS = {"return_pct", "mfe_pct", "mae_pct", "pnl"}


class SidecarFinancialEvaluationError(ValueError):
    pass


@dataclass(frozen=True)
class SidecarFinancialEvaluationReport:
    status: str
    features_path: str
    sidecar_path: str
    rows: int
    joined_rows: int
    id_column: str
    target_column: str
    return_column: str
    leakage_status: str
    fold_metrics: list[dict[str, Any]]
    aggregate_metrics: dict[str, dict[str, Any]]
    limitations: list[str]
    created_at: str = field(default_factory=lambda: utc_now())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_sidecar_financial_evaluation(
    features: pd.DataFrame,
    sidecar: pd.DataFrame,
    *,
    features_path: str | Path,
    sidecar_path: str | Path,
    id_column: str = "trade_id",
    target_column: str = "target_win",
    return_column: str = "return_pct",
    folds: int = 5,
    embargo_minutes: int = 60,
    seed: int = 42,
    time_column: str = "open_1m_ts",
) -> SidecarFinancialEvaluationReport:
    validate_features_frame(
        features,
        id_column=id_column,
        target_column=target_column,
        time_column=time_column,
    )
    validate_sidecar_frame(
        sidecar,
        id_column=id_column,
        target_column=target_column,
        return_column=return_column,
    )

    feature_audit = audit_feature_leakage(
        features,
        target_column=target_column,
        metadata_columns=[id_column, time_column, "symbol", "open_5m_ts"],
        decision_mode="open",
    )
    if feature_audit.status == BLOCKED:
        raise SidecarFinancialEvaluationError("features_failed_anti_leakage_audit")

    joined = features.merge(
        sidecar[[id_column, return_column]],
        on=id_column,
        how="left",
        validate="one_to_one",
    )
    missing_returns = int(joined[return_column].isna().sum())
    if missing_returns:
        raise SidecarFinancialEvaluationError(f"sidecar_join_lost_rows:{missing_returns}")
    if len(joined) != len(features):
        raise SidecarFinancialEvaluationError(
            f"sidecar_join_row_count_mismatch:{len(features)}:{len(joined)}"
        )

    y_true = pd.to_numeric(joined[target_column], errors="coerce")
    if y_true.isna().any():
        raise SidecarFinancialEvaluationError("target_column_contains_null_or_non_numeric")
    returns = pd.to_numeric(joined[return_column], errors="coerce")
    if returns.isna().any():
        raise SidecarFinancialEvaluationError("return_column_contains_null_or_non_numeric")

    fold_metrics = evaluate_folds(
        joined.reset_index(drop=True),
        id_column=id_column,
        target_column=target_column,
        return_column=return_column,
        time_column=time_column,
        folds=folds,
        embargo_minutes=embargo_minutes,
        seed=seed,
    )
    aggregate = aggregate_fold_metrics(fold_metrics)
    report = SidecarFinancialEvaluationReport(
        status=OK,
        features_path=str(features_path),
        sidecar_path=str(sidecar_path),
        rows=int(len(features)),
        joined_rows=int(len(joined)),
        id_column=id_column,
        target_column=target_column,
        return_column=return_column,
        leakage_status=feature_audit.status,
        fold_metrics=fold_metrics,
        aggregate_metrics=aggregate,
        limitations=[],
    )
    json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    return report


def evaluate_folds(
    frame: pd.DataFrame,
    *,
    id_column: str,
    target_column: str,
    return_column: str,
    time_column: str,
    folds: int,
    embargo_minutes: int,
    seed: int,
) -> list[dict[str, Any]]:
    if time_column in frame.columns:
        split_result = create_walkforward_splits(
            frame,
            time_column=time_column,
            folds=folds,
            embargo_seconds=int(embargo_minutes) * 60,
        )
        fold_specs = [
            {
                "fold_id": fold.fold_id,
                "train_indices": fold.train_indices,
                "test_indices": fold.test_indices,
                "train_start": fold.train_start,
                "train_end": fold.train_end,
                "test_start": fold.test_start,
                "test_end": fold.test_end,
            }
            for fold in split_result.folds
        ]
    else:
        fold_specs = [
            {
                "fold_id": 1,
                "train_indices": list(range(len(frame))),
                "test_indices": list(range(len(frame))),
            }
        ]

    reports: list[dict[str, Any]] = []
    for spec in fold_specs:
        train = frame.iloc[spec["train_indices"]]
        test = frame.iloc[spec["test_indices"]]
        y_train = pd.to_numeric(train[target_column], errors="coerce").astype(int).to_numpy()
        y_test = pd.to_numeric(test[target_column], errors="coerce").astype(int).to_numpy()
        returns = pd.to_numeric(test[return_column], errors="coerce").to_numpy(float)
        predictions = build_fold_predictions(y_train, y_test, seed=seed + int(spec["fold_id"]))
        baseline_metrics: dict[str, Any] = {}
        for name, y_pred in predictions.items():
            trade_mask = np.zeros_like(y_pred, dtype=bool) if name == "no_trade/cash" else y_pred == 1
            baseline_metrics[name] = compute_metrics(
                y_test,
                y_pred,
                returns=returns,
                costs=np.zeros(len(test), dtype=float),
                trade_mask=trade_mask,
            )
        reports.append(
            {
                "fold_id": int(spec["fold_id"]),
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "baseline_metrics": baseline_metrics,
                **{key: value for key, value in spec.items() if key.endswith("_start") or key.endswith("_end")},
            }
        )
    return reports


def build_fold_predictions(
    y_train: np.ndarray,
    y_test: np.ndarray,
    *,
    seed: int,
) -> dict[str, np.ndarray]:
    predictions = build_baseline_predictions(y_test, seed=seed)
    majority = int(np.mean(y_train) >= 0.5) if y_train.size else 0
    predictions["majority_class"] = np.full_like(y_test, majority, dtype=int)
    return predictions


def aggregate_fold_metrics(fold_metrics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    aggregate: dict[str, dict[str, Any]] = {}
    if not fold_metrics:
        return aggregate
    baseline_names = fold_metrics[0]["baseline_metrics"].keys()
    metric_names = (
        "accuracy",
        "precision",
        "recall",
        "win_rate",
        "average_return_pct",
        "total_return_pct",
        "profit_factor",
        "max_drawdown",
        "trades",
    )
    for baseline in baseline_names:
        aggregate[baseline] = {}
        for metric in metric_names:
            values = [
                fold["baseline_metrics"][baseline].get(metric)
                for fold in fold_metrics
                if fold["baseline_metrics"][baseline].get(metric) is not None
            ]
            if not values:
                aggregate[baseline][metric] = None
            elif metric in {"total_return_pct", "trades"}:
                aggregate[baseline][metric] = float(np.sum(values))
            else:
                aggregate[baseline][metric] = float(np.mean(values))
    return aggregate


def validate_features_frame(
    frame: pd.DataFrame,
    *,
    id_column: str,
    target_column: str,
    time_column: str,
) -> None:
    if id_column not in frame.columns:
        raise SidecarFinancialEvaluationError(f"features_id_column_missing:{id_column}")
    if target_column not in frame.columns:
        raise SidecarFinancialEvaluationError(f"features_target_column_missing:{target_column}")
    forbidden = [
        column
        for column in frame.columns
        if column in FORBIDDEN_FEATURE_COLUMNS
        or column.startswith("close_1m_")
        or column.startswith("close_5m_")
    ]
    if forbidden:
        raise SidecarFinancialEvaluationError(f"features_contain_outcome_or_close_columns:{forbidden}")
    if frame[id_column].duplicated(keep=False).any():
        raise SidecarFinancialEvaluationError("features_id_column_contains_duplicates")
    if time_column in frame.columns and pd.to_datetime(frame[time_column], errors="coerce").isna().any():
        raise SidecarFinancialEvaluationError("features_time_column_contains_null_or_unparseable")


def validate_sidecar_frame(
    frame: pd.DataFrame,
    *,
    id_column: str,
    target_column: str,
    return_column: str,
) -> None:
    for column in (id_column, target_column, return_column):
        if column not in frame.columns:
            raise SidecarFinancialEvaluationError(f"sidecar_column_missing:{column}")
    if frame[id_column].duplicated(keep=False).any():
        raise SidecarFinancialEvaluationError("sidecar_id_column_contains_duplicates")
    forbidden_feature_like = [
        column
        for column in frame.columns
        if (
            column.startswith("open_1m_")
            or column.startswith("open_5m_")
            or column.startswith("close_1m_")
            or column.startswith("close_5m_")
        )
        and column != "open_1m_ts"
    ]
    if forbidden_feature_like:
        raise SidecarFinancialEvaluationError(
            f"sidecar_contains_feature_columns:{forbidden_feature_like}"
        )


def read_parquet(path: str | Path) -> pd.DataFrame:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"dataset_missing:{input_path}")
    return pd.read_parquet(input_path)


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
