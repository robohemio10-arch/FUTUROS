from __future__ import annotations

import argparse
import json
import math
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml
    except Exception:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def normalize_path(value: str | None, default: str) -> Path:
    return Path(value or default)


def resolve_dataset(config: dict[str, Any]) -> tuple[Path | None, list[dict[str, Any]]]:
    paths = config.get("paths", {})
    candidates = [
        Path(paths.get("preferred_dataset", "data/qlib/qlib_market_dataset.parquet")),
        Path(paths.get("fallback_dataset", "data/features/market_features_60d.parquet")),
        Path("data/features/market_features_60d.parquet"),
    ]
    checked = []
    for candidate in candidates:
        checked.append({"path": str(candidate), "exists": candidate.exists()})
        if candidate.exists():
            return candidate, checked
    return None, checked


def read_dataset(path: Path, max_rows: int) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    if frame.empty:
        return frame
    sort_columns = [column for column in ["ts", "datetime", "date", "timestamp", "ts_ms"] if column in frame.columns]
    if sort_columns:
        frame = frame.sort_values(sort_columns)
    if max_rows > 0 and len(frame) > max_rows:
        frame = frame.tail(max_rows)
    return frame.reset_index(drop=True)


def prepare_target(frame: pd.DataFrame, config: dict[str, Any]) -> tuple[pd.Series, str, pd.Series | None]:
    target_cfg = config.get("target", {})
    preferred = target_cfg.get("preferred_column", "target_direction_3")
    fallback_return = target_cfg.get("fallback_return_column", "future_ret_3")

    if preferred in frame.columns:
        target = pd.to_numeric(frame[preferred], errors="coerce")
        return (target > 0).astype("float"), preferred, pd.to_numeric(frame.get(fallback_return), errors="coerce") if fallback_return in frame.columns else None

    if fallback_return in frame.columns:
        returns = pd.to_numeric(frame[fallback_return], errors="coerce")
        return (returns > 0).astype("float"), f"{fallback_return}>0", returns

    future_candidates = [column for column in frame.columns if column.startswith("future_ret")]
    if future_candidates:
        returns = pd.to_numeric(frame[future_candidates[0]], errors="coerce")
        return (returns > 0).astype("float"), f"{future_candidates[0]}>0", returns

    raise ValueError("Nenhuma coluna de target encontrada. Esperado target_direction_3 ou future_ret_3.")


def select_feature_columns(frame: pd.DataFrame, target_name: str, max_features: int) -> list[str]:
    excluded = {
        "target_direction_3",
        "target",
        "label",
        "future_ret_1",
        "future_ret_3",
        "future_ret_5",
        "future_ret_10",
        "future_ret_15",
        "ts",
        "datetime",
        "date",
        "timestamp",
        "ts_ms",
        "symbol",
        "pair",
        "instrument",
        "tf",
    }
    excluded.add(target_name)
    numeric_columns = []
    for column in frame.columns:
        if column in excluded:
            continue
        if pd.api.types.is_numeric_dtype(frame[column]):
            finite_count = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan).notna().sum()
            if finite_count > 0:
                numeric_columns.append(column)
    priority = [
        "ret_1", "ret_3", "ret_5", "ret_10", "ret_15", "ret_30",
        "ema_20", "ema_50", "ema_200", "dist_ema20", "dist_ema50", "dist_ema200",
        "rsi_14", "macd_line", "macd_signal", "macd_hist",
        "atr_14", "atr_pct_14", "vol_30", "vol_120",
        "volume_rel_30", "volume_z_30", "trend_score",
        "hl_range", "body_range", "upper_wick", "lower_wick",
    ]
    ordered = [column for column in priority if column in numeric_columns]
    ordered.extend([column for column in numeric_columns if column not in ordered])
    return ordered[:max_features]


def sanitize_matrix(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    matrix = frame[columns].apply(pd.to_numeric, errors="coerce")
    matrix = matrix.replace([np.inf, -np.inf], np.nan)
    medians = matrix.median(numeric_only=True).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return matrix.fillna(medians)


def make_folds(n_rows: int, folds: int, min_train_rows: int, min_test_rows: int) -> list[tuple[int, int, int, int]]:
    folds = max(1, int(folds))
    min_train_rows = max(100, int(min_train_rows))
    min_test_rows = max(50, int(min_test_rows))

    if n_rows < min_train_rows + min_test_rows:
        train = max(1, int(n_rows * 0.7))
        return [(0, train, train, n_rows)] if n_rows - train >= 10 else []

    remaining = n_rows - min_train_rows
    test_size = max(min_test_rows, remaining // folds)
    result = []
    train_end = min_train_rows
    while train_end + min_test_rows <= n_rows and len(result) < folds:
        test_end = min(n_rows, train_end + test_size)
        if test_end - train_end >= min_test_rows:
            result.append((0, train_end, train_end, test_end))
        train_end = test_end
    return result


def safe_metric(name: str, y_true: np.ndarray, y_pred: np.ndarray, score: np.ndarray | None = None) -> float | None:
    try:
        if name == "accuracy":
            from sklearn.metrics import accuracy_score
            return float(accuracy_score(y_true, y_pred))
        if name == "precision":
            from sklearn.metrics import precision_score
            return float(precision_score(y_true, y_pred, zero_division=0))
        if name == "recall":
            from sklearn.metrics import recall_score
            return float(recall_score(y_true, y_pred, zero_division=0))
        if name == "f1":
            from sklearn.metrics import f1_score
            return float(f1_score(y_true, y_pred, zero_division=0))
        if name == "roc_auc":
            if score is None or len(np.unique(y_true)) < 2:
                return None
            from sklearn.metrics import roc_auc_score
            return float(roc_auc_score(y_true, score))
    except Exception:
        return None
    return None


def build_model(config: dict[str, Any], random_state: int) -> tuple[Any, str]:
    model_cfg = config.get("model", {})
    preferred = model_cfg.get("preferred_backend", "lightgbm")
    if preferred == "lightgbm":
        try:
            from lightgbm import LGBMClassifier
            return LGBMClassifier(
                n_estimators=120,
                learning_rate=0.05,
                max_depth=4,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=random_state,
                verbose=-1,
            ), "lightgbm_lgbmclassifier"
        except Exception:
            pass

    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(
            max_iter=120,
            learning_rate=0.05,
            max_leaf_nodes=16,
            random_state=random_state,
        ), "sklearn_hist_gradient_boosting"
    except Exception:
        from sklearn.ensemble import RandomForestClassifier
        return RandomForestClassifier(
            n_estimators=120,
            max_depth=6,
            min_samples_leaf=20,
            random_state=random_state,
            n_jobs=-1,
        ), "sklearn_random_forest"


def predict_scores(model: Any, x_test: pd.DataFrame) -> np.ndarray:
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(x_test)
        if proba.ndim == 2 and proba.shape[1] >= 2:
            return proba[:, 1]
    if hasattr(model, "decision_function"):
        raw = model.decision_function(x_test)
        return 1.0 / (1.0 + np.exp(-raw))
    return np.asarray(model.predict(x_test), dtype=float)


def compute_strategy_returns(pred: np.ndarray, realized: pd.Series | None, fee_bps: float) -> dict[str, Any]:
    if realized is None:
        return {
            "trades": 0,
            "total_return": None,
            "mean_return": None,
            "profit_factor": None,
            "max_drawdown": None,
        }

    returns = pd.to_numeric(realized, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    side = np.where(pred > 0, 1.0, -1.0)
    fee = float(fee_bps) / 10000.0
    strategy_returns = side * returns - fee
    equity = np.cumsum(strategy_returns)
    peak = np.maximum.accumulate(equity) if len(equity) else np.array([])
    drawdown = equity - peak if len(equity) else np.array([])

    positive = strategy_returns[strategy_returns > 0].sum()
    negative = abs(strategy_returns[strategy_returns < 0].sum())
    profit_factor = float(positive / negative) if negative > 0 else (float("inf") if positive > 0 else None)

    return {
        "trades": int(len(strategy_returns)),
        "total_return": float(np.sum(strategy_returns)),
        "mean_return": float(np.mean(strategy_returns)) if len(strategy_returns) else None,
        "profit_factor": profit_factor,
        "max_drawdown": float(np.min(drawdown)) if len(drawdown) else None,
    }


def aggregate_numeric(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None and not (isinstance(value, float) and math.isnan(value))]
    return float(np.mean(clean)) if clean else None


def feature_importance(model: Any, columns: list[str]) -> list[dict[str, Any]]:
    values = None
    if hasattr(model, "feature_importances_"):
        values = np.asarray(model.feature_importances_, dtype=float)
    elif hasattr(model, "coef_"):
        values = np.abs(np.asarray(model.coef_).reshape(-1))
    if values is None or len(values) != len(columns):
        return []
    order = np.argsort(values)[::-1][:20]
    return [{"feature": columns[int(i)], "importance": float(values[int(i)])} for i in order]


def performance_by_regime(frame: pd.DataFrame, predictions: np.ndarray, returns: pd.Series | None) -> list[dict[str, Any]]:
    if "market_regime" not in frame.columns or returns is None:
        return []
    temp = pd.DataFrame({
        "market_regime": frame["market_regime"].astype(str).values,
        "prediction": predictions,
        "return": pd.to_numeric(returns, errors="coerce").fillna(0.0).values,
    })
    temp["strategy_return"] = np.where(temp["prediction"] > 0, temp["return"], -temp["return"])
    grouped = temp.groupby("market_regime", dropna=False)
    return [
        {
            "market_regime": str(name),
            "rows": int(len(group)),
            "mean_strategy_return": float(group["strategy_return"].mean()),
            "total_strategy_return": float(group["strategy_return"].sum()),
        }
        for name, group in grouped
    ]


def create_charts(output_dir: Path, fold_returns: list[float], feature_rows: list[dict[str, Any]]) -> dict[str, str | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    charts: dict[str, str | None] = {"equity_curve": None, "feature_importance": None}
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if fold_returns:
            equity = np.cumsum(np.asarray(fold_returns, dtype=float))
            path = output_dir / "phase21_equity_curve.png"
            fig = plt.figure()
            plt.plot(equity)
            plt.title("Phase 21 Walk-forward Equity Curve")
            plt.xlabel("Fold/Test observation")
            plt.ylabel("Cumulative simulated return")
            plt.tight_layout()
            fig.savefig(path)
            plt.close(fig)
            charts["equity_curve"] = str(path)

        if feature_rows:
            top = feature_rows[:15]
            path = output_dir / "phase21_feature_importance.png"
            fig = plt.figure()
            plt.barh([row["feature"] for row in reversed(top)], [row["importance"] for row in reversed(top)])
            plt.title("Phase 21 Feature Importance")
            plt.tight_layout()
            fig.savefig(path)
            plt.close(fig)
            charts["feature_importance"] = str(path)
    except Exception:
        return charts
    return charts


def run(args: argparse.Namespace) -> dict[str, Any]:
    config_path = Path(args.config)
    config = load_yaml(config_path)
    paths = config.get("paths", {})
    output_report = Path(args.output or paths.get("output_report", "data/reports/phase21_qlib_walkforward_report.json"))
    output_dir = Path(paths.get("output_dir", "data/reports/phase21_walkforward"))

    runtime = config.get("runtime", {})
    max_rows = int(args.max_rows or runtime.get("max_rows", 50000))
    folds_requested = int(args.folds or runtime.get("folds", 5))
    min_train_rows = int(args.min_train_rows or runtime.get("min_train_rows", 2000))
    min_test_rows = int(args.min_test_rows or runtime.get("min_test_rows", 500))
    random_state = int(runtime.get("random_state", 42))
    max_features = int(runtime.get("max_features", 40))

    dataset_path, checked_datasets = resolve_dataset(config)
    if dataset_path is None:
        return {
            "status": "skipped",
            "reason": "dataset_not_found",
            "checked_datasets": checked_datasets,
            "created_at": utc_now(),
        }

    frame = read_dataset(dataset_path, max_rows=max_rows)
    if frame.empty:
        return {
            "status": "skipped",
            "reason": "dataset_empty",
            "dataset": str(dataset_path),
            "created_at": utc_now(),
        }

    target, target_name, realized_returns = prepare_target(frame, config)
    valid_mask = target.notna()
    frame = frame.loc[valid_mask].reset_index(drop=True)
    target = target.loc[valid_mask].astype(int).reset_index(drop=True)
    if realized_returns is not None:
        realized_returns = realized_returns.loc[valid_mask].reset_index(drop=True)

    feature_columns = select_feature_columns(frame, target_name, max_features)
    if not feature_columns:
        return {
            "status": "skipped",
            "reason": "no_numeric_features",
            "dataset": str(dataset_path),
            "rows": int(len(frame)),
            "created_at": utc_now(),
        }

    folds = make_folds(len(frame), folds_requested, min_train_rows, min_test_rows)
    if not folds:
        return {
            "status": "skipped",
            "reason": "insufficient_rows_for_walkforward",
            "dataset": str(dataset_path),
            "rows": int(len(frame)),
            "min_train_rows": min_train_rows,
            "min_test_rows": min_test_rows,
            "created_at": utc_now(),
        }

    x_all = sanitize_matrix(frame, feature_columns)
    y_all = target.to_numpy(dtype=int)
    fold_reports = []
    all_predictions = []
    all_realized = []
    last_model = None
    model_backend = None

    baseline_reports = []

    for idx, (train_start, train_end, test_start, test_end) in enumerate(folds, start=1):
        x_train = x_all.iloc[train_start:train_end]
        y_train = y_all[train_start:train_end]
        x_test = x_all.iloc[test_start:test_end]
        y_test = y_all[test_start:test_end]
        returns_test = realized_returns.iloc[test_start:test_end] if realized_returns is not None else None

        majority = int(pd.Series(y_train).mode().iloc[0])
        baseline_pred = np.full_like(y_test, majority)
        baseline_report = {
            "fold": idx,
            "train_rows": int(len(x_train)),
            "test_rows": int(len(x_test)),
            "accuracy": safe_metric("accuracy", y_test, baseline_pred),
            "precision": safe_metric("precision", y_test, baseline_pred),
            "recall": safe_metric("recall", y_test, baseline_pred),
            "f1": safe_metric("f1", y_test, baseline_pred),
        }
        baseline_report.update(compute_strategy_returns(baseline_pred, returns_test, config.get("simulation", {}).get("fee_bps_roundtrip", 8.0)))
        baseline_reports.append(baseline_report)

        model, model_backend = build_model(config, random_state + idx)
        model.fit(x_train, y_train)
        scores = predict_scores(model, x_test)
        pred = (scores >= 0.5).astype(int)

        simulated = compute_strategy_returns(pred, returns_test, config.get("simulation", {}).get("fee_bps_roundtrip", 8.0))
        fold_report = {
            "fold": idx,
            "train_rows": int(len(x_train)),
            "test_rows": int(len(x_test)),
            "test_start": int(test_start),
            "test_end": int(test_end),
            "accuracy": safe_metric("accuracy", y_test, pred),
            "precision": safe_metric("precision", y_test, pred),
            "recall": safe_metric("recall", y_test, pred),
            "f1": safe_metric("f1", y_test, pred),
            "roc_auc": safe_metric("roc_auc", y_test, pred, scores),
            "simulated": simulated,
        }
        fold_reports.append(fold_report)
        all_predictions.extend(pred.tolist())
        if returns_test is not None:
            all_realized.extend(pd.to_numeric(returns_test, errors="coerce").fillna(0.0).tolist())
        last_model = model

    metrics = {
        "accuracy": aggregate_numeric([fold["accuracy"] for fold in fold_reports]),
        "precision": aggregate_numeric([fold["precision"] for fold in fold_reports]),
        "recall": aggregate_numeric([fold["recall"] for fold in fold_reports]),
        "f1": aggregate_numeric([fold["f1"] for fold in fold_reports]),
        "roc_auc": aggregate_numeric([fold["roc_auc"] for fold in fold_reports]),
        "total_simulated_return": aggregate_numeric([fold["simulated"]["total_return"] for fold in fold_reports]),
        "mean_simulated_return": aggregate_numeric([fold["simulated"]["mean_return"] for fold in fold_reports]),
        "profit_factor": aggregate_numeric([fold["simulated"]["profit_factor"] for fold in fold_reports if fold["simulated"]["profit_factor"] != float("inf")]),
        "max_drawdown": aggregate_numeric([fold["simulated"]["max_drawdown"] for fold in fold_reports]),
    }

    baseline_metrics = {
        "accuracy": aggregate_numeric([fold["accuracy"] for fold in baseline_reports]),
        "precision": aggregate_numeric([fold["precision"] for fold in baseline_reports]),
        "recall": aggregate_numeric([fold["recall"] for fold in baseline_reports]),
        "f1": aggregate_numeric([fold["f1"] for fold in baseline_reports]),
        "total_simulated_return": aggregate_numeric([fold["total_return"] for fold in baseline_reports]),
    }

    importance = feature_importance(last_model, feature_columns) if last_model is not None else []
    charts = create_charts(output_dir, all_realized, importance)

    predictions_array = np.asarray(all_predictions, dtype=int)
    returns_series = pd.Series(all_realized) if all_realized else None
    regime_rows = performance_by_regime(frame.tail(len(predictions_array)).reset_index(drop=True), predictions_array, returns_series) if len(predictions_array) else []

    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(fold_reports).to_json(output_dir / "phase21_fold_reports.json", orient="records", indent=2, force_ascii=False)
    pd.DataFrame(baseline_reports).to_json(output_dir / "phase21_baseline_fold_reports.json", orient="records", indent=2, force_ascii=False)

    return {
        "status": "ok",
        "phase": "phase21_safe_walkforward",
        "created_at": utc_now(),
        "dataset": str(dataset_path),
        "dataset_rows_used": int(len(frame)),
        "target": target_name,
        "features_used": feature_columns,
        "model_backend": model_backend,
        "folds_requested": folds_requested,
        "folds_completed": int(len(fold_reports)),
        "metrics": metrics,
        "baseline_metrics": baseline_metrics,
        "folds": fold_reports,
        "baseline_folds": baseline_reports,
        "feature_importance": importance,
        "performance_by_regime": regime_rows,
        "charts": charts,
        "outputs": {
            "report": str(output_report),
            "output_dir": str(output_dir),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/phase21_walkforward.yml")
    parser.add_argument("--output", default=None)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--folds", type=int, default=None)
    parser.add_argument("--min-train-rows", type=int, default=None)
    parser.add_argument("--min-test-rows", type=int, default=None)
    args = parser.parse_args()

    config = load_yaml(Path(args.config))
    output_report = Path(args.output or config.get("paths", {}).get("output_report", "data/reports/phase21_qlib_walkforward_report.json"))

    try:
        report = run(args)
        report.setdefault("created_at", utc_now())
        write_json(output_report, report)
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 0 if report.get("status") in {"ok", "skipped"} else 1
    except Exception as exc:
        error_report = {
            "status": "error",
            "phase": "phase21_safe_walkforward",
            "created_at": utc_now(),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        write_json(output_report, error_report)
        print(json.dumps(error_report, indent=2, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
