"""Research-only AI Shadow quality veto challenger trainer."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.paper_autolearning.outcome_schema import SAFETY_FLAGS, utc_now_iso
from smartcrypto.learning.qlib_trainer.dataset_adapter import resolve

from .challenger_artifacts import write_challenger_artifact as write_challenger_artifact_files
from .challenger_artifacts import write_report_artifacts
from .quality_dataset import load_quality_dataset_bundle, source_entries
from .veto_metrics import aggregate_metrics, baseline_comparison, quality_label, split_metrics
from .veto_thresholds import threshold_by_symbol_side_regime

SCHEMA_VERSION = "ai_shadow_quality_veto_trainer_v1"
DEFAULT_REPORT_JSON = Path("data/reports/ai_shadow_quality_veto_trainer_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/ai_shadow_quality_veto_trainer_v1.md")
DEFAULT_METRICS_JSON = Path("data/reports/ai_shadow_quality_veto_metrics_v1.json")
DEFAULT_METRICS_MD = Path("data/reports/ai_shadow_quality_veto_metrics_v1.md")


def build_ai_shadow_quality_veto_trainer_report(
    *,
    project_root: str | Path,
    train: bool = False,
    write_report: bool = False,
    write_challenger_artifact: bool = False,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    target_store_path: str | Path | None = None,
    walkforward_path: str | Path | None = None,
    baseline_path: str | Path | None = None,
    dataset_path: str | Path | None = None,
    qlib_trainer_report_path: str | Path | None = None,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    metrics_json_path: str | Path | None = None,
    metrics_markdown_path: str | Path | None = None,
    registry_write_requested: bool = False,
    model_promotion_requested: bool = False,
    ai_shadow_runtime_update_requested: bool = False,
    veto_registry_write_requested: bool = False,
    veto_runtime_active_requested: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    generated_at = utc_now_iso()
    bundle, qlib_report, qlib_report_hash = load_quality_dataset_bundle(
        project_root=root,
        feature_contract_path=feature_contract_path,
        dataset_manifest_path=dataset_manifest_path,
        target_store_path=target_store_path,
        walkforward_path=walkforward_path,
        baseline_path=baseline_path,
        dataset_path=dataset_path,
        qlib_trainer_report_path=qlib_trainer_report_path,
    )
    validation_errors = list(bundle.validation_errors)
    if registry_write_requested:
        validation_errors.append("registry_write_forbidden")
    if model_promotion_requested:
        validation_errors.append("model_promotion_forbidden")
    if ai_shadow_runtime_update_requested:
        validation_errors.append("ai_shadow_runtime_update_forbidden")
    if veto_registry_write_requested:
        validation_errors.append("veto_registry_write_forbidden")
    if veto_runtime_active_requested:
        validation_errors.append("veto_runtime_active_forbidden")
    if write_challenger_artifact and not train:
        validation_errors.append("challenger_artifact_requires_train")

    backend_available = sklearn_backend_available()
    trainer_status = "ok"
    reason = "dry_run_validated"
    training_performed = False
    metrics_by_split: list[dict[str, Any]] = []
    thresholds: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    aggregate = empty_aggregate_metrics()
    baselines = baseline_comparison([], bundle.baseline_summary)
    model_payload: dict[str, Any] = {}

    if train:
        if not backend_available:
            trainer_status = "blocked"
            reason = "ai_shadow_backend_unavailable"
            validation_errors.append("ai_shadow_backend_unavailable")
        elif validation_errors:
            trainer_status = "blocked"
            reason = validation_errors[0]
        else:
            evaluation = train_quality_veto_challenger(bundle)
            metrics_by_split = evaluation["metrics_by_split"]
            thresholds = evaluation["threshold_by_symbol_side_regime"]
            decision_rows = evaluation["decision_rows"]
            aggregate = evaluation["aggregate_metrics"]
            baselines = evaluation["baseline_comparison"]
            model_payload = evaluation["model_payload"]
            training_performed = True
            trainer_status = "ok"
            reason = "quality_veto_challenger_trained_research_only"
    elif validation_errors:
        trainer_status = "blocked"
        reason = validation_errors[0]

    candidate_decision = decide_candidate(
        train=train,
        training_performed=training_performed,
        reason=reason,
        aggregate_metrics=aggregate,
        baseline=baselines,
    )
    status = "blocked" if trainer_status == "blocked" else "ok"
    output_paths = {
        "trainer_report_json": str(resolve(root, report_json_path, DEFAULT_REPORT_JSON)),
        "trainer_report_markdown": str(resolve(root, report_markdown_path, DEFAULT_REPORT_MD)),
        "metrics_json": str(resolve(root, metrics_json_path, DEFAULT_METRICS_JSON)),
        "metrics_markdown": str(resolve(root, metrics_markdown_path, DEFAULT_METRICS_MD)),
    }
    artifact_paths: dict[str, str] = {}
    artifact_hashes: dict[str, str] = {}
    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "input_sources": source_entries(root, bundle, qlib_report_hash),
        "selected_dataset_path": str(bundle.selected_dataset_path) if bundle.selected_dataset_path is not None else None,
        "selected_dataset_rows": int(len(bundle.dataset)),
        "feature_contract_hash": bundle.feature_contract.get("contract_hash"),
        "dataset_hash": bundle.dataset_manifest.get("dataset_hash"),
        "target_store_hash": bundle.target_store.get("target_store_hash"),
        "split_engine_hash": bundle.walkforward.get("split_engine_hash"),
        "qlib_trainer_report_hash": qlib_report_hash,
        "qlib_trainer_report_available": bool(qlib_report),
        "lineage_drift_detected": bundle.lineage_drift_detected,
        "backend_status": "available" if backend_available else "unavailable",
        "backend_name": "sklearn_logistic_regression" if backend_available else "none",
        "trainer_status": trainer_status,
        "challenger_model_status": "trained_research_only" if training_performed else "not_trained",
        "feature_column_count": len(bundle.feature_columns),
        "feature_columns": bundle.feature_columns,
        "primary_label": "quality_label",
        "probability_output": "probability_quality",
        "probability_column": "probability_quality",
        "decision_output": "ai_shadow_candidate_decision",
        "challenger_decisions": ("AI_ACCEPT", "AI_REJECT"),
        "threshold_scope": "symbol_side_regime",
        "threshold_by_symbol_side_regime": thresholds,
        "split_count": int(bundle.walkforward.get("split_count", len(bundle.reconstructed_splits)) or 0),
        "trained_split_count": len(metrics_by_split),
        "evaluated_split_count": len(metrics_by_split),
        "metrics_by_split": metrics_by_split,
        "aggregate_metrics": aggregate,
        "baseline_comparison": baselines,
        "candidate_decision": candidate_decision,
        "promotion_eligible": False,
        "ai_shadow_runtime_update_requested": bool(ai_shadow_runtime_update_requested),
        "ai_shadow_runtime_updated": False,
        "veto_runtime_active": False,
        "veto_registry_write_requested": bool(veto_registry_write_requested),
        "veto_registry_write_performed": False,
        "registry_write_requested": bool(registry_write_requested),
        "registry_write_performed": False,
        "model_promotion_requested": bool(model_promotion_requested),
        "model_promotion_performed": False,
        "active_model_changed": False,
        "write_report_requested": bool(write_report),
        "write_report_performed": False,
        "write_challenger_artifact_requested": bool(write_challenger_artifact),
        "write_challenger_artifact_performed": False,
        "artifact_paths": artifact_paths,
        "artifact_hashes": artifact_hashes,
        **safety_flags(),
        "training_requested": bool(train),
        "ai_shadow_challenger_training_performed": bool(training_performed),
        "qlib_training_performed": False,
        "qlib_runtime_updated": False,
        "decision_sample": decision_rows[:10],
        "safety_flags": safety_flags(),
        "validation_errors": sorted(set(validation_errors)),
    }
    metrics_payload = {
        "schema_version": "ai_shadow_quality_veto_metrics_v1",
        "generated_at_utc": generated_at,
        "metrics_by_split": metrics_by_split,
        "aggregate_metrics": aggregate,
        "baseline_comparison": baselines,
        "threshold_by_symbol_side_regime": thresholds,
        "evaluated_split_count": len(metrics_by_split),
        "promotion_eligible": False,
        "candidate_decision": candidate_decision,
    }
    if write_challenger_artifact and training_performed:
        artifact_metadata = {
            "schema_version": "ai_shadow_quality_veto_challenger_artifact_v1",
            "generated_at_utc": generated_at,
            "feature_contract_hash": report["feature_contract_hash"],
            "dataset_hash": report["dataset_hash"],
            "target_store_hash": report["target_store_hash"],
            "split_engine_hash": report["split_engine_hash"],
            "backend_name": report["backend_name"],
            "promotion_eligible": False,
            "candidate_decision": candidate_decision,
            "safety_flags": safety_flags(),
        }
        artifact_paths, artifact_hashes = write_challenger_artifact_files(
            root=root,
            generated_at_utc=generated_at,
            metadata=artifact_metadata,
            model_payload=model_payload,
            metrics=metrics_payload,
            thresholds=thresholds,
        )
        report["artifact_paths"] = artifact_paths
        report["artifact_hashes"] = artifact_hashes
        report["write_challenger_artifact_performed"] = True
    if write_report:
        report["write_report_performed"] = True
        write_report_artifacts(
            report=report,
            metrics_payload=metrics_payload,
            report_json=Path(output_paths["trainer_report_json"]),
            report_md=Path(output_paths["trainer_report_markdown"]),
            metrics_json=Path(output_paths["metrics_json"]),
            metrics_md=Path(output_paths["metrics_markdown"]),
        )
    return report


def sklearn_backend_available() -> bool:
    required = ("sklearn.impute", "sklearn.linear_model", "sklearn.metrics", "sklearn.pipeline", "sklearn.preprocessing")
    return all(importlib.util.find_spec(module) is not None for module in required)


def train_quality_veto_challenger(bundle: Any) -> dict[str, Any]:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    metrics_by_split: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    model_payload: dict[str, Any] = {"backend_name": "sklearn_logistic_regression", "models": []}
    for split in bundle.reconstructed_splits:
        train = bundle.dataset.loc[split["_train_indices"]]
        validation = bundle.dataset.loc[split["_validation_indices"]]
        test = bundle.dataset.loc[split["_test_indices"]]
        x_train = numeric_features(train, bundle.feature_columns)
        x_test = numeric_features(test, bundle.feature_columns)
        y_train = quality_label(train)
        model = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("classifier", LogisticRegression(max_iter=500, random_state=17)),
            ]
        )
        if y_train.nunique(dropna=False) < 2:
            positive_probability = float(y_train.iloc[0]) if not y_train.empty else 0.0
            probability = pd.Series([positive_probability] * len(test), index=test.index)
            model_payload["models"].append({"split_id": split["split_id"], "single_class_probability": positive_probability})
        else:
            model.fit(x_train, y_train)
            probability = pd.Series(model.predict_proba(x_test)[:, 1], index=test.index)
            classifier = model.named_steps["classifier"]
            model_payload["models"].append(
                {
                    "split_id": split["split_id"],
                    "classes": [int(value) for value in classifier.classes_],
                    "coefficients": classifier.coef_.tolist(),
                    "intercept": classifier.intercept_.tolist(),
                }
            )
        decision = probability.map(lambda value: "AI_ACCEPT" if float(value) >= 0.5 else "AI_REJECT")
        metrics_by_split.append(
            split_metrics(
                split_id=split["split_id"],
                train_row_count=len(train),
                validation_row_count=len(validation),
                test_frame=test,
                probability_quality=probability,
                decision=decision,
            )
        )
        decision_rows.extend(decision_records(split["split_id"], test, probability, decision))
    thresholds = threshold_by_symbol_side_regime(decision_rows)
    aggregate = aggregate_metrics(metrics_by_split)
    return {
        "metrics_by_split": metrics_by_split,
        "threshold_by_symbol_side_regime": thresholds,
        "decision_rows": decision_rows,
        "aggregate_metrics": aggregate,
        "baseline_comparison": baseline_comparison(metrics_by_split, bundle.baseline_summary),
        "model_payload": model_payload,
    }


def numeric_features(frame: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index)
    for column in feature_columns:
        series = pd.to_numeric(frame[column], errors="coerce")
        output[column] = series.fillna(0.0) if series.isna().all() else series
    return output


def decision_records(split_id: str, frame: pd.DataFrame, probability: pd.Series, decision: pd.Series) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    expected_value = pd.to_numeric(frame.get("target_expected_value_component", 0.0), errors="coerce").fillna(0.0)
    for index in frame.index:
        rows.append(
            {
                "split_id": split_id,
                "row_index": int(index),
                "order_id": str(frame.at[index, "order_id"]) if "order_id" in frame.columns else "",
                "symbol": normalize_symbol(frame.at[index, "symbol_norm"] if "symbol_norm" in frame.columns else ""),
                "side": normalize_side(frame.at[index, "side"] if "side" in frame.columns else ""),
                "regime": normalize_regime(frame.at[index, "regime"] if "regime" in frame.columns else "global"),
                "regime_source": "default_global",
                "probability_quality": round(float(probability.loc[index]), 10),
                "ai_shadow_candidate_decision": str(decision.loc[index]),
                "target_expected_value_component": round(float(expected_value.loc[index]), 10),
            }
        )
    return rows


def decide_candidate(*, train: bool, training_performed: bool, reason: str, aggregate_metrics: dict[str, Any], baseline: dict[str, Any]) -> str:
    if not train:
        return "NOT_TRAINED_DRY_RUN"
    if reason == "ai_shadow_backend_unavailable":
        return "BLOCKED_BACKEND_UNAVAILABLE"
    if not training_performed:
        return "MANTER_EM_RESEARCH"
    beats_accept = int(baseline.get("beats_always_accept_split_count", 0) or 0)
    beats_reject = int(baseline.get("beats_always_reject_split_count", 0) or 0)
    split_count = int(aggregate_metrics.get("split_count", 0) or 0)
    if split_count and beats_accept == split_count and beats_reject == split_count:
        return "RESEARCH_CHALLENGER_ONLY"
    return "MANTER_EM_RESEARCH"


def empty_aggregate_metrics() -> dict[str, Any]:
    return aggregate_metrics([])


def normalize_symbol(value: Any) -> str:
    return str(value or "").replace("/", "").replace("-", "").upper()


def normalize_side(value: Any) -> str:
    text = str(value or "").lower()
    if "short" in text:
        return "short"
    if "long" in text:
        return "long"
    return text or "unknown"


def normalize_regime(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text or "global"


def safety_flags() -> dict[str, bool]:
    return {
        **SAFETY_FLAGS,
        "training_requested": False,
        "ai_shadow_challenger_training_performed": False,
        "qlib_training_performed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "veto_runtime_active": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_signal_producer": False,
        "veto_registry_write_performed": False,
        "registry_write_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }
