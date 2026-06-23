from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, brier_score_loss, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

try:
    from lightgbm import LGBMClassifier
except Exception:  # pragma: no cover - fallback covered by model behavior tests.
    LGBMClassifier = None  # type: ignore[assignment]


SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "runs_ocr": False,
    "imports_ocr": False,
    "promotes_quality_gated": False,
    "runs_ai_shadow_incremental": False,
    "cleans_sqlite": False,
    "updates_freqtrade": False,
    "updates_qlib_runtime": False,
    "updates_risk_manager": False,
    "registers_model": False,
    "auto_promote": False,
    "production_enabled": False,
}


METADATA_COLUMNS = {
    "trade_id",
    "order_id",
    "symbol",
    "pair",
    "side",
    "open_time",
    "close_time",
    "timestamp",
    "datetime",
    "date",
    "source_file",
    "strategy_id",
    "simulation_status",
    "first_hit",
    "status",
    "reason",
}

ALLOWED_FEATURE_PREFIXES = (
    "entry_",
    "feature_",
    "market_",
    "pretrade_",
    "pre_trade_",
)

ALLOWED_FEATURE_EXACT = {
    "entry_candle_found",
}

LEAKAGE_HINTS = (
    "future_ret",
    "target",
    "label",
    "pnl",
    "profit",
    "outcome",
    "simulated",
    "original",
    "close",
    "exit",
    "tp_",
    "sl_",
    "mfe",
    "mae",
    "is_win",
    "win",
    "loss",
    "duration",
    "fee",
    "fees",
    "volume_closed",
    "candles_between",
    "missing_candle",
    "max_favorable",
    "max_adverse",
    "time_to_",
)


@dataclass(frozen=True)
class SupervisedTrainingPaths:
    project_root: Path
    research_dataset_path: Path
    trade_outcomes_path: Path
    walkforward_report_path: Path
    prediction_output_path: Path
    model_output_path: Path
    report_path: Path
    executive_report_path: Path
    summary_path: Path


@dataclass(frozen=True)
class SupervisedTrainingConfig:
    min_rows: int = 600
    folds: int = 5
    embargo_seconds: int = 3600
    selector_quantile: float = 0.70
    min_selected_rows: int = 25
    seed: int = 42
    workers: int = 10
    max_ram_gb: float = 16.0
    model_family: Literal["lightgbm", "random_forest"] = "lightgbm"


@dataclass(frozen=True)
class SupervisedTrainingResult:
    predictions: pd.DataFrame
    report: dict[str, Any]


def configured_workers() -> int:
    raw = os.getenv("SMARTCRYPTO_TRAINING_WORKERS", "10")
    try:
        return max(1, int(raw))
    except ValueError:
        return 10


def configured_max_ram_gb() -> float:
    raw = os.getenv("SMARTCRYPTO_TRAINING_MAX_RAM_GB", "16")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 16.0


def resolve_paths(
    project_root: str | Path,
    *,
    research_dataset_path: str | Path | None = None,
    trade_outcomes_path: str | Path | None = None,
    walkforward_report_path: str | Path | None = None,
    prediction_output_path: str | Path | None = None,
    model_output_path: str | Path | None = None,
    report_path: str | Path | None = None,
    executive_report_path: str | Path | None = None,
    summary_path: str | Path | None = None,
) -> SupervisedTrainingPaths:
    root = Path(project_root).resolve()

    def resolved(value: str | Path | None, default: Path) -> Path:
        path = Path(value) if value is not None else default
        if not path.is_absolute():
            path = root / path
        return path.resolve()

    return SupervisedTrainingPaths(
        project_root=root,
        research_dataset_path=resolved(
            research_dataset_path,
            Path("data/research/ocr_v11_trade_research_dataset.parquet"),
        ),
        trade_outcomes_path=resolved(
            trade_outcomes_path,
            Path("data/research/ocr_v11_trade_outcome_simulation.parquet"),
        ),
        walkforward_report_path=resolved(
            walkforward_report_path,
            Path("data/reports/ocr_v11_walkforward_montecarlo_summary.json"),
        ),
        prediction_output_path=resolved(
            prediction_output_path,
            Path("data/research/qlib_ocr_v11_supervised_training_predictions.parquet"),
        ),
        model_output_path=resolved(
            model_output_path,
            Path("data/models/qlib_ocr_v11/research/qlib_ocr_v11_supervised_candidate.joblib"),
        ),
        report_path=resolved(
            report_path,
            Path("data/reports/qlib_ocr_v11_supervised_training_summary.json"),
        ),
        executive_report_path=resolved(
            executive_report_path,
            Path("data/reports/training_reports/qlib_ocr_v11_supervised_training_executive.md"),
        ),
        summary_path=resolved(
            summary_path,
            Path("data/reports/training_reports/qlib_ocr_v11_supervised_training_summary.json"),
        ),
    )


def validate_config(config: SupervisedTrainingConfig) -> list[str]:
    errors: list[str] = []
    if config.min_rows <= 0:
        errors.append("invalid_min_rows")
    if config.folds <= 1:
        errors.append("invalid_folds")
    if config.embargo_seconds < 0:
        errors.append("invalid_embargo_seconds")
    if not 0.0 < config.selector_quantile < 1.0:
        errors.append("invalid_selector_quantile")
    if config.min_selected_rows <= 0:
        errors.append("invalid_min_selected_rows")
    if config.workers <= 0:
        errors.append("invalid_workers")
    if config.max_ram_gb <= 0:
        errors.append("invalid_max_ram_gb")
    if config.model_family not in {"lightgbm", "random_forest"}:
        errors.append("invalid_model_family")
    return errors


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() in {".csv", ".txt"}:
        return pd.read_csv(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"unsupported_table_format:{path.suffix}")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    if isinstance(payload, dict):
        return payload
    return {"payload": payload}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(json_safe(payload), handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_name = handle.name
    Path(temp_name).replace(path)


def atomic_write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".parquet", dir=path.parent, delete=False) as handle:
        temp_path = Path(handle.name)
    frame.to_parquet(temp_path, index=False)
    temp_path.replace(path)


def atomic_write_joblib(path: Path, model: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".joblib", dir=path.parent, delete=False) as handle:
        temp_path = Path(handle.name)
    joblib.dump(model, temp_path)
    temp_path.replace(path)


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        item = float(value)
        return None if math.isnan(item) or math.isinf(item) else item
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def first_existing(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def normalize_training_frame(research: pd.DataFrame, outcomes: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    columns = set(research.columns)

    trade_id_column = first_existing(columns, ("trade_id", "order_id", "_dedup_key"))
    time_column = first_existing(columns, ("open_time", "horario_abertura", "open_ts", "open_1m_ts", "timestamp"))
    close_time_column = first_existing(columns, ("close_time", "horario_fechamento", "close_ts", "event_end_ts"))

    if trade_id_column is None:
        return pd.DataFrame(), ["missing_trade_id_column"]
    if time_column is None:
        return pd.DataFrame(), ["missing_time_column"]

    working = research.copy()
    if trade_id_column != "trade_id":
        working["trade_id"] = working[trade_id_column].astype(str)
    else:
        working["trade_id"] = working["trade_id"].astype(str)

    working["open_time"] = pd.to_datetime(working[time_column], utc=True, errors="coerce")
    if close_time_column and close_time_column in working.columns:
        working["close_time"] = pd.to_datetime(working[close_time_column], utc=True, errors="coerce")
    else:
        working["close_time"] = working["open_time"]

    outcomes_working = outcomes.copy()
    if "trade_id" in outcomes_working.columns:
        outcomes_working["trade_id"] = outcomes_working["trade_id"].astype(str)
    else:
        outcomes_working = pd.DataFrame(columns=["trade_id"])

    outcome_columns = [
        column
        for column in [
            "trade_id",
            "original_net_pnl",
            "original_is_win",
            "simulated_net_pnl",
            "simulation_status",
            "strategy_id",
        ]
        if column in outcomes_working.columns
    ]
    if "trade_id" in outcome_columns:
        merged = working.merge(
            outcomes_working[outcome_columns],
            on="trade_id",
            how="left",
            suffixes=("", "_outcome"),
        )
    else:
        merged = working.copy()
        warnings.append("outcomes_without_trade_id")

    if "original_net_pnl" not in merged.columns:
        pnl_column = first_existing(set(merged.columns), ("net_pnl_usdt", "pnl_usdt", "pnl_fechado", "pnl"))
        if pnl_column is None:
            return pd.DataFrame(), ["missing_original_net_pnl"]
        merged["original_net_pnl"] = pd.to_numeric(merged[pnl_column], errors="coerce")
    else:
        merged["original_net_pnl"] = pd.to_numeric(merged["original_net_pnl"], errors="coerce")

    if "original_is_win" not in merged.columns:
        merged["target_original_win"] = (merged["original_net_pnl"] > 0).astype(int)
    else:
        numeric_target = pd.to_numeric(merged["original_is_win"], errors="coerce")
        merged["target_original_win"] = numeric_target.fillna(merged["original_net_pnl"] > 0).astype(int)

    merged = merged.dropna(subset=["open_time", "original_net_pnl", "target_original_win"]).copy()
    merged = merged.sort_values(["open_time", "trade_id"], kind="mergesort").reset_index(drop=True)
    return merged, warnings


def is_leakage_column(column: str) -> bool:
    normalized = column.lower().strip()

    if normalized in METADATA_COLUMNS:
        return True

    if normalized in ALLOWED_FEATURE_EXACT:
        return False

    if any(normalized.startswith(prefix) for prefix in ALLOWED_FEATURE_PREFIXES):
        return any(hint in normalized for hint in LEAKAGE_HINTS)

    return True


def select_feature_columns(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    numeric_columns = [
        str(column)
        for column in frame.columns
        if pd.api.types.is_numeric_dtype(frame[column])
    ]

    excluded = sorted(column for column in numeric_columns if is_leakage_column(column))
    features = [
        column
        for column in numeric_columns
        if column not in excluded
        and column != "target_original_win"
        and not column.startswith("_")
    ]

    return features, excluded


def create_temporal_folds(
    frame: pd.DataFrame,
    *,
    folds: int,
    embargo_seconds: int,
) -> list[dict[str, Any]]:
    total_rows = len(frame)
    if total_rows < folds + 1:
        return []

    test_size = max(1, total_rows // (folds + 1))
    embargo_delta = pd.Timedelta(seconds=int(embargo_seconds))
    result: list[dict[str, Any]] = []

    for fold_id in range(1, folds + 1):
        test_start_pos = test_size * fold_id
        test_end_pos = min(total_rows, test_start_pos + test_size)
        if test_start_pos >= total_rows or test_end_pos <= test_start_pos:
            continue

        train_candidate = frame.iloc[:test_start_pos].copy()
        test = frame.iloc[test_start_pos:test_end_pos].copy()
        test_start = test["open_time"].min()
        embargo_cutoff = test_start - embargo_delta
        train = train_candidate[train_candidate["close_time"] < embargo_cutoff].copy()
        purged_rows = int(len(train_candidate) - len(train))

        if train.empty or test.empty:
            continue
        if train["open_time"].max() >= test_start:
            continue

        result.append(
            {
                "fold_id": fold_id,
                "train_indices": train.index.to_list(),
                "test_indices": test.index.to_list(),
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "purged_rows": purged_rows,
                "train_start": train["open_time"].min(),
                "train_end": train["open_time"].max(),
                "test_start": test_start,
                "test_end": test["open_time"].max(),
            }
        )
    return result


def build_model(config: SupervisedTrainingConfig) -> Any:
    if config.model_family == "lightgbm" and LGBMClassifier is not None:
        return LGBMClassifier(
            n_estimators=240,
            learning_rate=0.035,
            num_leaves=31,
            max_depth=-1,
            min_child_samples=20,
            subsample=0.85,
            colsample_bytree=0.85,
            objective="binary",
            class_weight="balanced",
            random_state=config.seed,
            n_jobs=config.workers,
            verbosity=-1,
        )

    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler(with_mean=False)),
            (
                "classifier",
                RandomForestClassifier(
                    n_estimators=240,
                    max_depth=7,
                    min_samples_leaf=3,
                    random_state=config.seed,
                    n_jobs=config.workers,
                    class_weight="balanced_subsample",
                ),
            ),
        ]
    )


def predict_probability(model: Any, features: pd.DataFrame) -> np.ndarray:
    if hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(features)
        if probabilities.ndim == 2 and probabilities.shape[1] >= 2:
            return np.asarray(probabilities[:, 1], dtype=float)
    predictions = model.predict(features)
    return np.asarray(predictions, dtype=float)


def safe_auc(y_true: pd.Series, probability: np.ndarray) -> float | None:
    if y_true.nunique(dropna=True) < 2:
        return None
    try:
        return float(roc_auc_score(y_true, probability))
    except ValueError:
        return None


def financial_metrics(pnl: pd.Series | np.ndarray) -> dict[str, Any]:
    values = pd.to_numeric(pd.Series(pnl), errors="coerce").dropna().astype(float)
    if values.empty:
        return {
            "rows": 0,
            "net_pnl": 0.0,
            "win_rate": 0.0,
            "profit_factor": None,
            "expectancy": 0.0,
        }

    wins = values[values > 0]
    losses = values[values < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))

    return {
        "rows": int(len(values)),
        "net_pnl": float(values.sum()),
        "win_rate": float((values > 0).mean()),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "expectancy": float(values.mean()),
    }


def evaluate_fold(
    *,
    frame: pd.DataFrame,
    fold: dict[str, Any],
    feature_columns: list[str],
    config: SupervisedTrainingConfig,
) -> tuple[dict[str, Any], pd.DataFrame]:
    train = frame.loc[fold["train_indices"]].copy()
    test = frame.loc[fold["test_indices"]].copy()

    x_train = train[feature_columns]
    y_train = train["target_original_win"].astype(int)
    x_test = test[feature_columns]
    y_test = test["target_original_win"].astype(int)

    if y_train.nunique(dropna=True) < 2 or y_test.nunique(dropna=True) < 2:
        skipped = {
            "fold_id": fold["fold_id"],
            "status": "skipped",
            "reason": "insufficient_target_classes_in_fold",
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
            "purged_rows": int(fold["purged_rows"]),
        }
        return skipped, pd.DataFrame()

    model = build_model(config)
    model.fit(x_train, y_train)
    probability = predict_probability(model, x_test)
    prediction = (probability >= 0.5).astype(int)

    threshold = float(np.quantile(probability, config.selector_quantile))
    selected_mask = probability >= threshold
    selected = test.loc[selected_mask].copy()

    prediction_frame = pd.DataFrame(
        {
            "fold_id": int(fold["fold_id"]),
            "trade_id": test["trade_id"].astype(str).to_numpy(),
            "open_time": test["open_time"].to_numpy(),
            "target_original_win": y_test.to_numpy(dtype=int),
            "original_net_pnl": test["original_net_pnl"].to_numpy(dtype=float),
            "predicted_quality_probability": probability,
            "predicted_accept": selected_mask.astype(int),
        }
    )

    all_test = financial_metrics(test["original_net_pnl"])
    selected_metrics = financial_metrics(selected["original_net_pnl"])

    report = {
        "fold_id": int(fold["fold_id"]),
        "status": "ok",
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "selected_rows": int(len(selected)),
        "purged_rows": int(fold["purged_rows"]),
        "train_start": fold["train_start"],
        "train_end": fold["train_end"],
        "test_start": fold["test_start"],
        "test_end": fold["test_end"],
        "accuracy": float(accuracy_score(y_test, prediction)),
        "precision": float(precision_score(y_test, prediction, zero_division=0)),
        "recall": float(recall_score(y_test, prediction, zero_division=0)),
        "f1": float(f1_score(y_test, prediction, zero_division=0)),
        "roc_auc": safe_auc(y_test, probability),
        "brier": float(brier_score_loss(y_test, probability)),
        "all_test_net_pnl": all_test["net_pnl"],
        "all_test_profit_factor": all_test["profit_factor"],
        "all_test_expectancy": all_test["expectancy"],
        "selected_net_pnl": selected_metrics["net_pnl"],
        "selected_profit_factor": selected_metrics["profit_factor"],
        "selected_expectancy": selected_metrics["expectancy"],
        "selected_win_rate": selected_metrics["win_rate"],
        "selection_threshold": threshold,
    }
    return report, prediction_frame


def aggregate_fold_reports(folds: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [fold for fold in folds if fold.get("status") == "ok"]
    if not valid:
        return {
            "valid_folds": 0,
            "mean_accuracy": None,
            "mean_precision": None,
            "mean_recall": None,
            "mean_f1": None,
            "mean_roc_auc": None,
            "mean_brier": None,
            "all_test_net_pnl": 0.0,
            "selected_net_pnl": 0.0,
            "selected_rows": 0,
            "all_test_rows": 0,
        }

    def mean_metric(name: str) -> float | None:
        values = [fold.get(name) for fold in valid if fold.get(name) is not None]
        return float(np.mean(values)) if values else None

    return {
        "valid_folds": int(len(valid)),
        "mean_accuracy": mean_metric("accuracy"),
        "mean_precision": mean_metric("precision"),
        "mean_recall": mean_metric("recall"),
        "mean_f1": mean_metric("f1"),
        "mean_roc_auc": mean_metric("roc_auc"),
        "mean_brier": mean_metric("brier"),
        "all_test_net_pnl": float(sum(float(fold["all_test_net_pnl"]) for fold in valid)),
        "selected_net_pnl": float(sum(float(fold["selected_net_pnl"]) for fold in valid)),
        "selected_rows": int(sum(int(fold["selected_rows"]) for fold in valid)),
        "all_test_rows": int(sum(int(fold["test_rows"]) for fold in valid)),
    }


def build_final_model(frame: pd.DataFrame, feature_columns: list[str], config: SupervisedTrainingConfig) -> Any:
    model = build_model(config)
    model.fit(frame[feature_columns], frame["target_original_win"].astype(int))
    return model


def base_report(
    paths: SupervisedTrainingPaths,
    config: SupervisedTrainingConfig,
    *,
    analysis_date_utc: str,
    write: bool,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "not_run",
        "analysis_date_utc": analysis_date_utc,
        "research_dataset_path": str(paths.research_dataset_path),
        "trade_outcomes_path": str(paths.trade_outcomes_path),
        "walkforward_report_path": str(paths.walkforward_report_path),
        "prediction_output_path": str(paths.prediction_output_path),
        "model_output_path": str(paths.model_output_path),
        "report_path": str(paths.report_path),
        "executive_report_path": str(paths.executive_report_path),
        "summary_path": str(paths.summary_path),
        "write_requested": bool(write),
        "write_performed": False,
        "model_exported": False,
        "min_rows": int(config.min_rows),
        "folds": int(config.folds),
        "embargo_seconds": int(config.embargo_seconds),
        "selector_quantile": float(config.selector_quantile),
        "min_selected_rows": int(config.min_selected_rows),
        "seed": int(config.seed),
        "configured_workers": int(config.workers),
        "configured_max_ram_gb": float(config.max_ram_gb),
        "model_family_requested": config.model_family,
        "model_family_effective": "lightgbm" if config.model_family == "lightgbm" and LGBMClassifier is not None else "random_forest",
        "validation_errors": [],
        "warnings": [],
        **SAFETY_FLAGS,
    }


def build_executive_summary(report: dict[str, Any]) -> dict[str, Any]:
    aggregate = report.get("aggregate_metrics", {})
    return {
        "title": "Relatório Executivo — Qlib OCR V1.1 Supervised Training Lab",
        "analysis_date_utc": report["analysis_date_utc"],
        "status": report["status"],
        "reason": report["reason"],
        "decision": report["decision"],
        "training_rows": report.get("training_rows", 0),
        "feature_count": report.get("feature_count", 0),
        "valid_folds": aggregate.get("valid_folds", 0),
        "mean_roc_auc": aggregate.get("mean_roc_auc"),
        "mean_f1": aggregate.get("mean_f1"),
        "all_test_net_pnl": aggregate.get("all_test_net_pnl", 0.0),
        "selected_net_pnl": aggregate.get("selected_net_pnl", 0.0),
        "selected_rows": aggregate.get("selected_rows", 0),
        "recommendation": report["recommendation"],
    }


def render_executive_markdown(summary: dict[str, Any]) -> str:
    return f"""# {summary["title"]}

Data UTC: `{summary["analysis_date_utc"]}`

## 1. Veredito

**Status:** `{summary["status"]}`  
**Motivo:** `{summary["reason"]}`  
**Decisão:** `{summary["decision"]}`

## 2. Base treinada

| Item | Valor |
|---|---:|
| Linhas de treino/eval | {summary["training_rows"]} |
| Features usadas | {summary["feature_count"]} |
| Folds válidos | {summary["valid_folds"]} |

## 3. Métricas supervisionadas

| Métrica | Valor |
|---|---:|
| ROC AUC médio | {summary["mean_roc_auc"]} |
| F1 médio | {summary["mean_f1"]} |

## 4. Métrica financeira do seletor

| Métrica | Valor |
|---|---:|
| PnL all-test | {summary["all_test_net_pnl"]} |
| PnL selecionado pelo modelo | {summary["selected_net_pnl"]} |
| Trades selecionados | {summary["selected_rows"]} |

## 5. Conclusão executiva

{summary["recommendation"]}

Observação: esta branch é `research-only`. Ela não registra modelo em registry produtivo, não promove candidato, não altera Qlib runtime, Freqtrade, RiskManager, IA Shadow runtime, SQLite, live/canary ou ordens reais.
"""


def run_supervised_training_lab(
    paths: SupervisedTrainingPaths,
    config: SupervisedTrainingConfig,
    *,
    write: bool = False,
    analysis_date_utc: str | None = None,
) -> SupervisedTrainingResult:
    analysis_date = analysis_date_utc or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    report = base_report(paths, config, analysis_date_utc=analysis_date, write=write)

    config_errors = validate_config(config)
    if config_errors:
        report["reason"] = "invalid_config"
        report["validation_errors"] = config_errors
        return SupervisedTrainingResult(pd.DataFrame(), report)

    if not paths.research_dataset_path.exists():
        report["reason"] = "missing_research_dataset"
        report["validation_errors"] = ["missing_research_dataset"]
        return SupervisedTrainingResult(pd.DataFrame(), report)

    if not paths.trade_outcomes_path.exists():
        report["reason"] = "missing_trade_outcomes"
        report["validation_errors"] = ["missing_trade_outcomes"]
        return SupervisedTrainingResult(pd.DataFrame(), report)

    research = read_table(paths.research_dataset_path)
    outcomes = read_table(paths.trade_outcomes_path)
    walkforward_gate = read_json(paths.walkforward_report_path)

    frame, normalize_warnings = normalize_training_frame(research, outcomes)
    report["warnings"] = normalize_warnings
    report["research_dataset_rows"] = int(len(research))
    report["trade_outcomes_rows"] = int(len(outcomes))
    report["training_rows"] = int(len(frame))
    report["branch03_gate_status"] = walkforward_gate.get("status")
    report["branch03_gate_decision"] = walkforward_gate.get("decision")
    report["branch03_risk_of_ruin"] = (walkforward_gate.get("monte_carlo") or {}).get("risk_of_ruin")

    if frame.empty:
        report["reason"] = "invalid_training_frame"
        report["validation_errors"] = normalize_warnings or ["empty_training_frame"]
        return SupervisedTrainingResult(pd.DataFrame(), report)

    if len(frame) < config.min_rows:
        report["reason"] = "insufficient_rows"
        report["validation_errors"] = [f"rows_below_min:{len(frame)}:{config.min_rows}"]
        return SupervisedTrainingResult(pd.DataFrame(), report)

    if frame["target_original_win"].nunique(dropna=True) < 2:
        report["reason"] = "insufficient_target_classes"
        report["validation_errors"] = ["insufficient_target_classes"]
        return SupervisedTrainingResult(pd.DataFrame(), report)

    feature_columns, excluded_leakage_columns = select_feature_columns(frame)
    report["feature_count"] = int(len(feature_columns))
    report["excluded_leakage_columns"] = excluded_leakage_columns

    if not feature_columns:
        report["reason"] = "no_numeric_features"
        report["validation_errors"] = ["no_numeric_features"]
        return SupervisedTrainingResult(pd.DataFrame(), report)

    folds = create_temporal_folds(
        frame,
        folds=config.folds,
        embargo_seconds=config.embargo_seconds,
    )
    if not folds:
        report["reason"] = "no_temporal_folds"
        report["validation_errors"] = ["no_temporal_folds"]
        return SupervisedTrainingResult(pd.DataFrame(), report)

    fold_reports: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []
    for fold in folds:
        fold_report, prediction_frame = evaluate_fold(
            frame=frame,
            fold=fold,
            feature_columns=feature_columns,
            config=config,
        )
        fold_reports.append(fold_report)
        if not prediction_frame.empty:
            prediction_frames.append(prediction_frame)

    predictions = pd.concat(prediction_frames, ignore_index=True) if prediction_frames else pd.DataFrame()
    aggregate = aggregate_fold_reports(fold_reports)

    selected_net = float(aggregate["selected_net_pnl"])
    all_test_net = float(aggregate["all_test_net_pnl"])
    selected_rows = int(aggregate["selected_rows"])

    suspicious_perfect_metrics = bool(
        int(aggregate["valid_folds"]) > 0
        and (
            aggregate.get("mean_accuracy") is not None
            and float(aggregate["mean_accuracy"]) >= 0.999999
        )
        and (
            aggregate.get("mean_f1") is not None
            and float(aggregate["mean_f1"]) >= 0.999999
        )
        and (
            aggregate.get("mean_roc_auc") is not None
            and float(aggregate["mean_roc_auc"]) >= 0.999999
        )
    )

    if int(aggregate["valid_folds"]) <= 0:
        status = "blocked"
        reason = "no_valid_training_folds"
        decision = "BLOQUEAR_CANDIDATO"
        recommendation = "Nenhum fold temporal válido foi treinado. Não há base para promoção nem registry."
    elif suspicious_perfect_metrics:
        status = "blocked"
        reason = "suspicious_perfect_metrics_possible_leakage"
        decision = "BLOQUEAR_CANDIDATO"
        recommendation = (
            "As métricas supervisionadas ficaram perfeitas, o que é incompatível com aprovação institucional sem auditoria adicional. "
            "Bloquear o candidato e revisar features, labels e fronteira temporal antes de qualquer registry/champion-challenger."
        )
    elif selected_rows < config.min_selected_rows:
        status = "blocked"
        reason = "insufficient_selected_rows"
        decision = "BLOQUEAR_CANDIDATO"
        recommendation = "O modelo selecionou poucas operações para evidência financeira confiável. Manter bloqueado."
    elif selected_net <= 0:
        status = "blocked"
        reason = "selected_net_pnl_not_positive"
        decision = "BLOQUEAR_CANDIDATO"
        recommendation = "O seletor supervisionado não gerou PnL positivo fora da amostra. Não promover."
    elif selected_net <= all_test_net:
        status = "warning"
        reason = "selector_does_not_beat_all_test_baseline"
        decision = "MANTER_EM_RESEARCH"
        recommendation = "O modelo treinou e gerou PnL positivo, mas não superou o baseline all-test. Manter em research."
    else:
        status = "ok"
        reason = "supervised_training_lab_completed"
        decision = "CANDIDATO_RESEARCH_ONLY"
        recommendation = "O modelo superou o baseline all-test na avaliação temporal. Pode seguir para registry/champion-challenger em branch futura, sem promoção automática."

    report.update(
        {
            "status": status,
            "reason": reason,
            "decision": decision,
            "recommendation": recommendation,
            "fold_reports": fold_reports,
            "aggregate_metrics": aggregate,
            "suspicious_perfect_metrics": suspicious_perfect_metrics,
            "prediction_rows": int(len(predictions)),
            "target_distribution": {
                str(key): int(value)
                for key, value in frame["target_original_win"].value_counts().sort_index().to_dict().items()
            },
            "feature_columns_sample": feature_columns[:25],
        }
    )

    final_model = None
    if write and status in {"ok", "warning", "blocked"} and feature_columns:
        if frame["target_original_win"].nunique(dropna=True) >= 2:
            final_model = build_final_model(frame, feature_columns, config)

    executive_summary = build_executive_summary(report)
    executive_markdown = render_executive_markdown(executive_summary)

    if write:
        atomic_write_parquet(paths.prediction_output_path, predictions)
        if final_model is not None:
            atomic_write_joblib(paths.model_output_path, final_model)
            report["model_exported"] = True
        atomic_write_json(paths.report_path, report)
        atomic_write_json(paths.summary_path, executive_summary)
        atomic_write_text(paths.executive_report_path, executive_markdown)
        report["write_performed"] = True
        atomic_write_json(paths.report_path, report)

    return SupervisedTrainingResult(predictions, report)
