from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from smartcrypto.ml.anti_leakage_audit import BLOCKED as LEAKAGE_BLOCKED
from smartcrypto.ml.anti_leakage_audit import audit_feature_leakage
from smartcrypto.ml.baseline_evaluation import build_baseline_predictions, compute_metrics
from smartcrypto.ml.walkforward_split import create_walkforward_splits


OK = "OK"
WARNING = "WARNING"
BLOCKED = "BLOCKED"

FORBIDDEN_FEATURE_COLUMNS = {
    "target_win",
    "return_pct",
    "net_return_pct",
    "gross_return_pct",
    "leveraged_return_pct",
    "pnl",
    "pnl_resolved",
    "raw_return",
    "raw_return_resolved",
    "exit_price",
    "exit_price_repaired",
    "mfe_pct",
    "mae_pct",
    "path_candles",
}

BASELINE_NAMES = ("random_strategy", "always_predict_win", "majority_class", "no_trade/cash")


class ModelVsBaselineFinancialEvaluationError(ValueError):
    pass


def run_model_vs_baseline_financial_evaluation(
    features: pd.DataFrame,
    sidecar: pd.DataFrame,
    *,
    features_path: str | Path,
    sidecar_path: str | Path,
    sidecar_report: dict[str, Any] | str | Path | None,
    id_column: str = "trade_id",
    target_column: str = "target_win",
    return_column: str = "net_return_pct",
    time_column: str = "open_1m_ts",
    folds: int = 5,
    embargo_minutes: int = 60,
    seed: int = 42,
    min_train_rows: int = 200,
    min_test_rows: int = 100,
    probability_thresholds: list[float] | tuple[float, ...] | str = "0.50,0.55,0.60,0.65,0.70",
) -> dict[str, Any]:
    validate_inputs(features, sidecar, id_column=id_column, target_column=target_column, return_column=return_column)
    sidecar_gate = normalize_sidecar_report(sidecar_report)
    if sidecar_gate["status"] == BLOCKED:
        return blocked_report(
            reason="normalized_return_sidecar_blocked",
            features_path=features_path,
            sidecar_path=sidecar_path,
            id_column=id_column,
            target_column=target_column,
            return_column=return_column,
            sidecar_gate=sidecar_gate,
        )
    if sidecar_gate["status"] not in {OK, WARNING}:
        return blocked_report(
            reason=f"unsupported_sidecar_status:{sidecar_gate['status']}",
            features_path=features_path,
            sidecar_path=sidecar_path,
            id_column=id_column,
            target_column=target_column,
            return_column=return_column,
            sidecar_gate=sidecar_gate,
        )

    joined = join_finance_grade_features(
        features,
        sidecar,
        id_column=id_column,
        target_column=target_column,
        return_column=return_column,
    )
    finance_grade_excluded_rows = int(len(features) - len(joined))
    if joined.empty:
        raise ModelVsBaselineFinancialEvaluationError("finance_grade_join_empty")

    feature_columns, excluded_columns = select_feature_columns(joined, id_column=id_column, time_column=time_column)
    if not feature_columns:
        raise ModelVsBaselineFinancialEvaluationError("no_open_decision_feature_columns_available")

    leakage_audit = audit_feature_leakage(
        joined[[id_column, time_column, target_column, *feature_columns]].copy(),
        target_column=target_column,
        metadata_columns=[id_column, time_column],
        decision_mode="open",
    )
    if leakage_audit.status == LEAKAGE_BLOCKED:
        return blocked_report(
            reason="features_failed_anti_leakage_audit",
            features_path=features_path,
            sidecar_path=sidecar_path,
            id_column=id_column,
            target_column=target_column,
            return_column=return_column,
            sidecar_gate=sidecar_gate,
            leakage_status=leakage_audit.status,
        )

    thresholds = parse_thresholds(probability_thresholds)
    fold_metrics = evaluate_walkforward_models(
        joined,
        feature_columns=feature_columns,
        id_column=id_column,
        target_column=target_column,
        return_column=return_column,
        time_column=time_column,
        folds=folds,
        embargo_minutes=embargo_minutes,
        seed=seed,
        min_train_rows=min_train_rows,
        min_test_rows=min_test_rows,
        thresholds=thresholds,
    )
    if not fold_metrics:
        raise ModelVsBaselineFinancialEvaluationError("no_valid_model_evaluation_folds")

    aggregate_metrics = aggregate_model_metrics(fold_metrics)
    baseline_metrics = {name: aggregate_metrics[name] for name in BASELINE_NAMES if name in aggregate_metrics}
    model_ranking = rank_models(aggregate_metrics, baseline_names=set(BASELINE_NAMES))
    best = model_ranking[0] if model_ranking else {}
    best_baseline = best_baseline_metrics(baseline_metrics)
    model_beats_baseline = bool(best) and beats_baseline(best, best_baseline)
    limitations = []
    if finance_grade_excluded_rows:
        limitations.append("features_filtered_to_finance_grade_sidecar")
    if not model_beats_baseline:
        limitations.append("no_model_beat_best_baseline_with_minimal_robustness")

    status = OK if model_beats_baseline else WARNING
    if sidecar_gate["status"] == WARNING and status == OK:
        status = WARNING
        limitations.append("sidecar_status_warning")

    payload = {
        "status": status,
        "features_path": str(features_path),
        "sidecar_path": str(sidecar_path),
        "rows": int(len(features)),
        "joined_rows": int(len(joined)),
        "finance_grade_excluded_rows": finance_grade_excluded_rows,
        "sidecar_status": sidecar_gate["status"],
        "id_column": id_column,
        "target_column": target_column,
        "return_column": return_column,
        "time_column": time_column,
        "feature_columns_used": feature_columns,
        "feature_columns_excluded": excluded_columns,
        "fold_metrics": fold_metrics,
        "aggregate_metrics": aggregate_metrics,
        "baseline_metrics": baseline_metrics,
        "model_ranking": model_ranking,
        "best_model": best.get("model_name"),
        "best_threshold": best.get("threshold"),
        "best_baseline": best_baseline,
        "model_beats_baseline": model_beats_baseline,
        "leakage_status": leakage_audit.status,
        "limitations": limitations,
        "recommended_next_action": recommended_next_action(status, model_beats_baseline),
        "created_at": utc_now(),
    }
    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return payload


def validate_inputs(
    features: pd.DataFrame,
    sidecar: pd.DataFrame,
    *,
    id_column: str,
    target_column: str,
    return_column: str,
) -> None:
    if not isinstance(features, pd.DataFrame) or not isinstance(sidecar, pd.DataFrame):
        raise ModelVsBaselineFinancialEvaluationError("inputs_must_be_dataframes")
    for frame_name, frame in (("features", features), ("sidecar", sidecar)):
        if id_column not in frame.columns:
            raise ModelVsBaselineFinancialEvaluationError(f"{frame_name}_id_column_missing:{id_column}")
        if frame[id_column].isna().any():
            raise ModelVsBaselineFinancialEvaluationError(f"{frame_name}_id_column_contains_nulls")
        if frame[id_column].duplicated(keep=False).any():
            raise ModelVsBaselineFinancialEvaluationError(f"{frame_name}_id_column_contains_duplicates")
    if target_column not in features.columns:
        raise ModelVsBaselineFinancialEvaluationError(f"features_target_column_missing:{target_column}")
    if return_column not in sidecar.columns:
        raise ModelVsBaselineFinancialEvaluationError(f"sidecar_return_column_missing:{return_column}")
    if pd.to_numeric(sidecar[return_column], errors="coerce").isna().any():
        raise ModelVsBaselineFinancialEvaluationError("sidecar_return_column_contains_null_or_non_numeric")


def normalize_sidecar_report(sidecar_report: dict[str, Any] | str | Path | None) -> dict[str, Any]:
    if sidecar_report is None:
        raise ModelVsBaselineFinancialEvaluationError("sidecar_report_required")
    if isinstance(sidecar_report, (str, Path)):
        path = Path(sidecar_report)
        if not path.exists():
            raise ModelVsBaselineFinancialEvaluationError(f"sidecar_report_missing:{path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
    elif isinstance(sidecar_report, dict):
        payload = dict(sidecar_report)
    else:
        raise ModelVsBaselineFinancialEvaluationError("sidecar_report_must_be_mapping_or_path")
    if not isinstance(payload, dict):
        raise ModelVsBaselineFinancialEvaluationError("sidecar_report_root_must_be_mapping")
    return {
        "status": str(payload.get("status") or "UNKNOWN").upper(),
        "rows": payload.get("rows"),
        "quality_flag_counts": payload.get("quality_flag_counts") if isinstance(payload.get("quality_flag_counts"), dict) else {},
        "outlier_summary": payload.get("outlier_summary") if isinstance(payload.get("outlier_summary"), dict) else {},
        "recommended_next_action": payload.get("recommended_next_action"),
    }


def join_finance_grade_features(
    features: pd.DataFrame,
    sidecar: pd.DataFrame,
    *,
    id_column: str,
    target_column: str,
    return_column: str,
) -> pd.DataFrame:
    sidecar_return_column = "__finance_grade_return_pct"
    joined = features.merge(
        sidecar[[id_column, return_column]].rename(columns={return_column: sidecar_return_column}),
        on=id_column,
        how="inner",
        validate="one_to_one",
    )
    if target_column not in joined.columns:
        raise ModelVsBaselineFinancialEvaluationError(f"target_column_missing_after_join:{target_column}")
    if return_column in joined.columns:
        joined = joined.drop(columns=[return_column])
    joined = joined.rename(columns={sidecar_return_column: return_column})
    return joined.reset_index(drop=True)


def select_feature_columns(frame: pd.DataFrame, *, id_column: str, time_column: str) -> tuple[list[str], list[str]]:
    excluded: list[str] = []
    selected: list[str] = []
    for column in frame.columns:
        if column in {id_column, time_column}:
            excluded.append(column)
            continue
        if is_forbidden_feature(column):
            excluded.append(column)
            continue
        if not (column.startswith("open_1m_") or column.startswith("open_5m_")):
            excluded.append(column)
            continue
        if column.endswith("_ts"):
            excluded.append(column)
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.notna().sum() == 0:
            excluded.append(column)
            continue
        selected.append(column)
    return selected, excluded


def is_forbidden_feature(column: str) -> bool:
    return (
        column in FORBIDDEN_FEATURE_COLUMNS
        or column.startswith("close_")
        or column.startswith("future_ret_")
        or column.startswith("target_")
    )


def evaluate_walkforward_models(
    frame: pd.DataFrame,
    *,
    feature_columns: list[str],
    id_column: str,
    target_column: str,
    return_column: str,
    time_column: str,
    folds: int,
    embargo_minutes: int,
    seed: int,
    min_train_rows: int,
    min_test_rows: int,
    thresholds: list[float],
) -> list[dict[str, Any]]:
    working = frame.sort_values(time_column, kind="stable").reset_index(drop=True)
    split_result = create_walkforward_splits(
        working,
        time_column=time_column,
        folds=folds,
        embargo_seconds=int(embargo_minutes) * 60,
    )
    fold_reports: list[dict[str, Any]] = []
    for fold in split_result.folds:
        train = working.iloc[fold.train_indices].copy()
        test = working.iloc[fold.test_indices].copy()
        if len(train) < min_train_rows or len(test) < min_test_rows:
            continue
        x_train, x_test = prepare_feature_matrices(train, test, feature_columns)
        y_train = pd.to_numeric(train[target_column], errors="coerce").astype(int).clip(0, 1).to_numpy()
        y_test = pd.to_numeric(test[target_column], errors="coerce").astype(int).clip(0, 1).to_numpy()
        returns = pd.to_numeric(test[return_column], errors="coerce").to_numpy(float)
        model_metrics = evaluate_models_for_fold(
            x_train,
            y_train,
            x_test,
            y_test,
            returns,
            thresholds=thresholds,
            seed=seed + int(fold.fold_id),
        )
        baseline_metrics = evaluate_baselines_for_fold(
            y_train,
            y_test,
            returns,
            seed=seed + int(fold.fold_id),
        )
        fold_reports.append(
            {
                "fold_id": int(fold.fold_id),
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "train_start": fold.train_start,
                "train_end": fold.train_end,
                "test_start": fold.test_start,
                "test_end": fold.test_end,
                "embargo_seconds": int(fold.embargo_seconds),
                "model_metrics": model_metrics,
                "baseline_metrics": baseline_metrics,
            }
        )
    return fold_reports


def prepare_feature_matrices(
    train: pd.DataFrame,
    test: pd.DataFrame,
    feature_columns: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    x_train = train[feature_columns].apply(pd.to_numeric, errors="coerce")
    x_test = test[feature_columns].apply(pd.to_numeric, errors="coerce")
    medians = x_train.median(numeric_only=True).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return x_train.fillna(medians).replace([np.inf, -np.inf], 0.0), x_test.fillna(medians).replace([np.inf, -np.inf], 0.0)


def evaluate_models_for_fold(
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    x_test: pd.DataFrame,
    y_test: np.ndarray,
    returns: np.ndarray,
    *,
    thresholds: list[float],
    seed: int,
) -> dict[str, dict[str, Any]]:
    models = build_models(seed=seed)
    results: dict[str, dict[str, Any]] = {}
    for name, model in models.items():
        if len(np.unique(y_train)) < 2:
            continue
        model.fit(x_train, y_train)
        probabilities = model.predict_proba(x_test)[:, 1]
        threshold_metrics: dict[str, Any] = {}
        for threshold in thresholds:
            y_pred = (probabilities >= threshold).astype(int)
            trade_mask = y_pred == 1
            metrics = compute_metrics(
                y_test,
                y_pred,
                returns=returns,
                costs=np.zeros(len(y_test), dtype=float),
                trade_mask=trade_mask,
            )
            threshold_metrics[format_threshold(threshold)] = rename_metrics(metrics)
        results[name] = threshold_metrics
    return results


def build_models(*, seed: int) -> dict[str, Any]:
    return {
        "logistic_regression": Pipeline(
            [
                ("scale", StandardScaler()),
                ("model", LogisticRegression(max_iter=1000, random_state=seed, solver="lbfgs")),
            ]
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=80,
            max_depth=5,
            min_samples_leaf=10,
            random_state=seed,
            n_jobs=1,
        ),
        "gradient_boosting": GradientBoostingClassifier(random_state=seed, max_depth=2, n_estimators=80),
    }


def evaluate_baselines_for_fold(
    y_train: np.ndarray,
    y_test: np.ndarray,
    returns: np.ndarray,
    *,
    seed: int,
) -> dict[str, Any]:
    predictions = build_baseline_predictions(y_test, seed=seed)
    majority = int(np.mean(y_train) >= 0.5) if y_train.size else 0
    predictions["majority_class"] = np.full_like(y_test, majority, dtype=int)
    baseline_metrics: dict[str, Any] = {}
    for name in BASELINE_NAMES:
        y_pred = predictions[name]
        trade_mask = np.zeros_like(y_pred, dtype=bool) if name == "no_trade/cash" else y_pred == 1
        baseline_metrics[name] = rename_metrics(
            compute_metrics(
                y_test,
                y_pred,
                returns=returns,
                costs=np.zeros(len(y_test), dtype=float),
                trade_mask=trade_mask,
            )
        )
    return baseline_metrics


def aggregate_model_metrics(fold_metrics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    flattened: dict[str, list[dict[str, Any]]] = {}
    for fold in fold_metrics:
        for model_name, threshold_map in fold["model_metrics"].items():
            for threshold, metrics in threshold_map.items():
                flattened.setdefault(f"{model_name}@{threshold}", []).append(metrics)
        for baseline_name, metrics in fold["baseline_metrics"].items():
            flattened.setdefault(baseline_name, []).append(metrics)
    return {name: aggregate_metrics(metrics) for name, metrics in sorted(flattened.items())}


def aggregate_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    keys = (
        "accuracy",
        "precision",
        "recall",
        "win_rate",
        "average_net_return_pct",
        "total_net_return_pct",
        "profit_factor",
        "max_drawdown",
        "trades",
    )
    for key in keys:
        values = [item.get(key) for item in items if item.get(key) is not None]
        if not values:
            result[key] = None
        elif key in {"total_net_return_pct", "trades"}:
            result[key] = float(np.sum(values))
        else:
            result[key] = float(np.mean(values))
    return result


def rank_models(aggregate: dict[str, dict[str, Any]], *, baseline_names: set[str]) -> list[dict[str, Any]]:
    ranking: list[dict[str, Any]] = []
    for name, metrics in aggregate.items():
        if name in baseline_names:
            continue
        model_name, threshold = split_model_threshold(name)
        ranking.append(
            {
                "model_name": model_name,
                "threshold": threshold,
                "total_net_return_pct": metrics.get("total_net_return_pct"),
                "profit_factor": metrics.get("profit_factor"),
                "max_drawdown": metrics.get("max_drawdown"),
                "trades": metrics.get("trades"),
            }
        )
    ranking.sort(
        key=lambda item: (
            safe_sort_value(item.get("total_net_return_pct")),
            safe_sort_value(item.get("profit_factor")),
            safe_sort_value(item.get("max_drawdown")),
            safe_sort_value(item.get("trades")),
        ),
        reverse=True,
    )
    return ranking


def best_baseline_metrics(baseline_metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not baseline_metrics:
        return {}
    name, metrics = max(
        baseline_metrics.items(),
        key=lambda item: safe_sort_value(item[1].get("total_net_return_pct")),
    )
    return {"baseline_name": name, **metrics}


def beats_baseline(model: dict[str, Any], baseline: dict[str, Any]) -> bool:
    if not model or not baseline:
        return False
    model_total = model.get("total_net_return_pct")
    baseline_total = baseline.get("total_net_return_pct")
    if model_total is None or baseline_total is None:
        return False
    return float(model_total) > float(baseline_total) and float(model.get("trades") or 0) > 0


def blocked_report(
    *,
    reason: str,
    features_path: str | Path,
    sidecar_path: str | Path,
    id_column: str,
    target_column: str,
    return_column: str,
    sidecar_gate: dict[str, Any],
    leakage_status: str | None = None,
) -> dict[str, Any]:
    return {
        "status": BLOCKED,
        "reason": reason,
        "features_path": str(features_path),
        "sidecar_path": str(sidecar_path),
        "id_column": id_column,
        "target_column": target_column,
        "return_column": return_column,
        "sidecar_status": sidecar_gate["status"],
        "sidecar_quality_flag_counts": sidecar_gate["quality_flag_counts"],
        "sidecar_outlier_summary": sidecar_gate["outlier_summary"],
        "fold_metrics": [],
        "aggregate_metrics": {},
        "baseline_metrics": {},
        "model_ranking": [],
        "best_model": None,
        "best_threshold": None,
        "leakage_status": leakage_status,
        "limitations": [reason],
        "recommended_next_action": sidecar_gate.get("recommended_next_action")
        or "block_model_financial_evaluation_until_inputs_are_repaired",
        "created_at": utc_now(),
    }


def rename_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    result = dict(metrics)
    if "average_return_pct" in result:
        result["average_net_return_pct"] = result.pop("average_return_pct")
    if "total_return_pct" in result:
        result["total_net_return_pct"] = result.pop("total_return_pct")
    return result


def parse_thresholds(value: list[float] | tuple[float, ...] | str) -> list[float]:
    if isinstance(value, str):
        thresholds = [float(item.strip()) for item in value.split(",") if item.strip()]
    else:
        thresholds = [float(item) for item in value]
    if not thresholds:
        raise ModelVsBaselineFinancialEvaluationError("probability_thresholds_empty")
    if any(threshold < 0 or threshold > 1 for threshold in thresholds):
        raise ModelVsBaselineFinancialEvaluationError("probability_threshold_out_of_range")
    return thresholds


def format_threshold(value: float) -> str:
    return f"{float(value):.2f}"


def split_model_threshold(name: str) -> tuple[str, float | None]:
    if "@" not in name:
        return name, None
    model, threshold = name.rsplit("@", 1)
    return model, float(threshold)


def safe_sort_value(value: Any) -> float:
    if value is None:
        return float("-inf")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float("-inf")
    if np.isnan(numeric):
        return float("-inf")
    return numeric


def recommended_next_action(status: str, model_beats_baseline: bool) -> str:
    if status == BLOCKED:
        return "block_model_financial_evaluation_until_inputs_are_repaired"
    if not model_beats_baseline:
        return "keep_model_in_research_shadow_until_it_beats_finance_grade_baselines"
    return "model_outperformed_baseline_in_offline_research_only_do_not_enable_live"


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
