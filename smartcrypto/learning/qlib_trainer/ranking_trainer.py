"""Research-only institutional Qlib ranking challenger trainer."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from smartcrypto.learning.paper_autolearning.outcome_schema import SAFETY_FLAGS, utc_now_iso
from smartcrypto.learning.qlib_backend_gate import build_qlib_research_backend_gate_report

from .challenger_artifacts import write_challenger_artifact, write_report_artifacts
from .dataset_adapter import DEFAULT_WALKFORWARD_BASELINE_JSON, DEFAULT_WALKFORWARD_JSON, load_ranking_dataset_bundle, resolve
from .walkforward_evaluator import evaluate_walkforward_challenger

SCHEMA_VERSION = "qlib_institutional_ranking_trainer_v1"
DEFAULT_REPORT_JSON = Path("data/reports/qlib_institutional_ranking_trainer_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/qlib_institutional_ranking_trainer_v1.md")
DEFAULT_METRICS_JSON = Path("data/reports/qlib_institutional_ranking_metrics_v1.json")
DEFAULT_METRICS_MD = Path("data/reports/qlib_institutional_ranking_metrics_v1.md")
DEFAULT_BACKEND_GATE_REPORT = Path("data/reports/qlib_research_backend_gate_v1.json")


def build_qlib_institutional_ranking_trainer_report(
    *,
    project_root: str | Path,
    train: bool = False,
    write_report: bool = False,
    write_challenger_artifact: bool = False,
    allow_research_fallback: bool = False,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    target_store_path: str | Path | None = None,
    walkforward_path: str | Path | None = None,
    baseline_path: str | Path | None = None,
    dataset_path: str | Path | None = None,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    metrics_json_path: str | Path | None = None,
    metrics_markdown_path: str | Path | None = None,
    backend_gate_report_path: str | Path | None = None,
    registry_write_requested: bool = False,
    model_promotion_requested: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    generated_at = utc_now_iso()
    bundle = load_ranking_dataset_bundle(
        project_root=root,
        feature_contract_path=feature_contract_path,
        dataset_manifest_path=dataset_manifest_path,
        target_store_path=target_store_path,
        walkforward_path=walkforward_path,
        baseline_path=baseline_path,
        dataset_path=dataset_path,
    )
    backend_gate_report, backend_gate_status = resolve_backend_gate_report(
        root=root,
        backend_gate_report_path=backend_gate_report_path,
    )
    backend_probe = qlib_backend_probe_from_gate(backend_gate_report)
    if backend_probe is None:
        qlib_available = importlib.util.find_spec("qlib") is not None
        qlib_backend_status = "available" if qlib_available else "unavailable"
        backend_gate_status = "not_provided"
    else:
        qlib_backend_status = backend_probe["qlib_backend_status"]
        qlib_available = qlib_backend_status == "available"
    validation_errors = list(bundle.validation_errors)
    if registry_write_requested:
        validation_errors.append("registry_write_forbidden")
    if model_promotion_requested:
        validation_errors.append("model_promotion_forbidden")
    if write_challenger_artifact and not train:
        validation_errors.append("challenger_artifact_requires_train")

    trainer_status = "ok"
    reason = "dry_run_validated"
    backend_name = "qlib_research" if qlib_available else "none"
    challenger_status = "not_trained"
    training_performed = False
    metrics_by_split: list[dict[str, Any]] = []
    aggregate_metrics = empty_aggregate_metrics()
    baseline_comparison = empty_baseline_comparison(bundle.baseline_summary)
    model_payload: dict[str, Any] = {}

    if train:
        if qlib_backend_status == "blocked":
            trainer_status = "blocked"
            reason = "qlib_backend_blocked"
            validation_errors.append("qlib_backend_blocked")
        elif qlib_backend_status in {"unavailable", "partial"} and not allow_research_fallback:
            trainer_status = "blocked"
            reason = f"qlib_backend_{qlib_backend_status}"
            validation_errors.append(reason)
        elif validation_errors:
            trainer_status = "blocked"
            reason = validation_errors[0]
        else:
            evaluation = evaluate_walkforward_challenger(bundle)
            metrics_by_split = evaluation["metrics_by_split"]
            aggregate_metrics = evaluation["aggregate_metrics"]
            baseline_comparison = evaluation["baseline_comparison"]
            model_payload = {
                "backend_name": evaluation["backend_name"],
                "models": evaluation["models"],
                "scaler_fit_row_counts": evaluation["scaler_fit_row_counts"],
            }
            backend_name = "qlib_research" if qlib_available else evaluation["backend_name"]
            if not qlib_available:
                qlib_backend_status = "research_fallback_allowed"
            challenger_status = "trained_research_only"
            trainer_status = "ok"
            reason = "research_challenger_trained"
            training_performed = True
    elif validation_errors:
        trainer_status = "blocked"
        reason = validation_errors[0]

    candidate_decision = decide_candidate(
        train=train,
        training_performed=training_performed,
        reason=reason,
        aggregate_metrics=aggregate_metrics,
        baseline_comparison=baseline_comparison,
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
    qlib_training_performed_flag = bool(training_performed and qlib_backend_status == "available")
    report_safety_flags = safety_flags(
        training_requested=bool(train),
        qlib_challenger_training_performed=bool(training_performed),
        qlib_training_performed=qlib_training_performed_flag,
    )
    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "input_sources": input_sources(root, bundle),
        "selected_dataset_path": str(bundle.selected_dataset_path) if bundle.selected_dataset_path is not None else None,
        "selected_dataset_rows": int(len(bundle.dataset)),
        "feature_contract_hash": bundle.feature_contract.get("contract_hash"),
        "dataset_hash": bundle.dataset_manifest.get("dataset_hash"),
        "target_store_hash": bundle.target_store.get("target_store_hash"),
        "split_engine_hash": bundle.walkforward.get("split_engine_hash"),
        "lineage_drift_detected": bundle.lineage_drift_detected,
        "qlib_backend_status": qlib_backend_status,
        "qlib_importable": bool(backend_gate_report.get("qlib_importable", qlib_available)) if isinstance(backend_gate_report, dict) else bool(qlib_available),
        "qlib_version": backend_gate_report.get("qlib_version") if isinstance(backend_gate_report, dict) else None,
        "environment_lock_status": backend_gate_report.get("environment_lock_status") if isinstance(backend_gate_report, dict) else None,
        "dependency_contract_hash": backend_gate_report.get("dependency_contract_hash") if isinstance(backend_gate_report, dict) else None,
        "backend_gate_report_status": backend_gate_status,
        "backend_gate_report_path": str(resolve(root, backend_gate_report_path, DEFAULT_BACKEND_GATE_REPORT)),
        "trainer_status": trainer_status,
        "challenger_model_status": challenger_status,
        "backend_name": backend_name,
        "feature_column_count": len(bundle.feature_columns),
        "feature_columns": bundle.feature_columns,
        "primary_target": bundle.primary_target,
        "auxiliary_targets": bundle.auxiliary_targets,
        "split_count": int(bundle.walkforward.get("split_count", len(bundle.reconstructed_splits)) or 0),
        "trained_split_count": len(metrics_by_split),
        "evaluated_split_count": len(metrics_by_split),
        "metrics_by_split": metrics_by_split,
        "aggregate_metrics": aggregate_metrics,
        "baseline_comparison": baseline_comparison,
        "candidate_decision": candidate_decision,
        "promotion_eligible": False,
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
        **report_safety_flags,
        "training_requested": bool(train),
        "qlib_challenger_training_performed": bool(training_performed),
        "qlib_training_performed": qlib_training_performed_flag,
        "qlib_runtime_updated": False,
        "ai_shadow_training_performed": False,
        "safety_flags": report_safety_flags,
        "validation_errors": sorted(set(validation_errors)),
    }
    metrics_payload = {
        "schema_version": "qlib_institutional_ranking_metrics_v1",
        "generated_at_utc": generated_at,
        "metrics_by_split": metrics_by_split,
        "aggregate_metrics": aggregate_metrics,
        "baseline_comparison": baseline_comparison,
        "evaluated_split_count": len(metrics_by_split),
        "promotion_eligible": False,
        "candidate_decision": candidate_decision,
    }
    if write_challenger_artifact and training_performed:
        artifact_metadata = {
            "schema_version": "qlib_institutional_ranking_challenger_artifact_v1",
            "generated_at_utc": generated_at,
            "feature_contract_hash": report["feature_contract_hash"],
            "dataset_hash": report["dataset_hash"],
            "target_store_hash": report["target_store_hash"],
            "split_engine_hash": report["split_engine_hash"],
            "backend_name": backend_name,
            "promotion_eligible": False,
            "candidate_decision": candidate_decision,
            "safety_flags": report_safety_flags,
        }
        artifact_paths, artifact_hashes = write_challenger_artifact_files(
            root=root,
            generated_at_utc=generated_at,
            metadata=artifact_metadata,
            metrics=metrics_payload,
            model_payload=model_payload,
        )
        report["artifact_paths"] = artifact_paths
        report["artifact_hashes"] = artifact_hashes
        report["write_challenger_artifact_performed"] = True
    if write_report:
        report["write_report_performed"] = True
        write_report_artifacts(
            report=report,
            metrics=metrics_payload,
            report_json=Path(output_paths["trainer_report_json"]),
            report_md=Path(output_paths["trainer_report_markdown"]),
            metrics_json=Path(output_paths["metrics_json"]),
            metrics_md=Path(output_paths["metrics_markdown"]),
        )
    return report


def write_challenger_artifact_files(
    *,
    root: Path,
    generated_at_utc: str,
    metadata: dict[str, Any],
    metrics: dict[str, Any],
    model_payload: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    return write_challenger_artifact(
        root=root,
        generated_at_utc=generated_at_utc,
        metadata=metadata,
        metrics=metrics,
        model_payload=model_payload,
    )


def decide_candidate(
    *,
    train: bool,
    training_performed: bool,
    reason: str,
    aggregate_metrics: dict[str, Any],
    baseline_comparison: dict[str, Any],
) -> str:
    if not train:
        return "NOT_TRAINED_DRY_RUN"
    if reason in {"qlib_backend_unavailable", "qlib_backend_partial", "qlib_backend_blocked"}:
        return "BLOCKED_BACKEND_UNAVAILABLE"
    if not training_performed:
        return "MANTER_EM_RESEARCH"
    beats_no_trade = int(baseline_comparison.get("beats_no_trade_split_count", 0) or 0)
    beats_random = int(baseline_comparison.get("beats_random_split_count", 0) or 0)
    split_count = int(aggregate_metrics.get("split_count", 0) or 0)
    if split_count and beats_no_trade == split_count and beats_random == split_count:
        return "RESEARCH_CHALLENGER_ONLY"
    return "MANTER_EM_RESEARCH"


def empty_aggregate_metrics() -> dict[str, Any]:
    return {
        "split_count": 0,
        "mean_rank_ic": 0.0,
        "mean_precision_at_10": 0.0,
        "selected_top_k_expected_value_total": 0.0,
        "beats_no_trade_split_count": 0,
        "beats_always_allow_split_count": 0,
        "beats_random_split_count": 0,
    }


def empty_baseline_comparison(baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseline_status": baseline.get("baseline_status", "unknown"),
        "candidate_selected_top_k_expected_value_total": 0.0,
        "baseline_no_trade_expected_value": baseline.get("no_trade_expected_value", 0.0),
        "baseline_always_allow_expected_value": baseline.get("always_allow_expected_value", 0.0),
        "baseline_random_expected_value": baseline.get("random_deterministic_expected_value", 0.0),
        "beats_no_trade_split_count": 0,
        "beats_always_allow_split_count": 0,
        "beats_random_split_count": 0,
    }


def input_sources(root: Path, bundle: Any) -> list[dict[str, Any]]:
    paths = [
        root / "data/reports/ai_unified_feature_contract_v1.json",
        root / "data/reports/ai_unified_dataset_manifest_v1.json",
        root / "data/reports/financial_label_target_store_v1.json",
        root / "data/reports/walkforward_anti_leakage_split_engine_v1.json",
        root / "data/reports/walkforward_baseline_summary_v1.json",
    ]
    if bundle.selected_dataset_path is not None:
        paths.append(bundle.selected_dataset_path)
    return [{"path": str(path.resolve()), "exists": path.exists()} for path in paths]


def resolve_backend_gate_report(
    *,
    root: Path,
    backend_gate_report_path: str | Path | None,
) -> tuple[dict[str, Any] | None, str]:
    """Resolve Qlib backend status without trusting stale default runtime reports.

    If the caller supplies --backend-gate-report, the trainer treats that file as an
    explicit immutable evidence input. Otherwise, it performs a live no-write gate
    probe, because data/reports/qlib_research_backend_gate_v1.json can be stale
    when the auditor was previously run without --write.
    """

    if backend_gate_report_path is not None:
        return load_backend_gate_report(resolve(root, backend_gate_report_path, DEFAULT_BACKEND_GATE_REPORT)), "provided"
    try:
        report = build_qlib_research_backend_gate_report(project_root=root, write=False)
    except Exception as exc:  # noqa: BLE001 - returned as controlled blocked evidence, not swallowed.
        return {
            "qlib_backend_status": "blocked",
            "validation_errors": [f"backend_gate_live_probe_failed:{type(exc).__name__}"],
        }, "live_probe_failed"
    if not isinstance(report, dict):
        return {"qlib_backend_status": "blocked", "validation_errors": ["backend_gate_live_probe_invalid"]}, "live_probe_failed"
    return report, "live_probe"


def load_backend_gate_report(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"qlib_backend_status": "blocked", "validation_errors": ["backend_gate_report_invalid"]}
    if not isinstance(payload, dict):
        return {"qlib_backend_status": "blocked", "validation_errors": ["backend_gate_report_invalid"]}
    return payload


def qlib_backend_probe_from_gate(report: dict[str, Any] | None) -> dict[str, str] | None:
    if report is None:
        return None
    status = str(report.get("qlib_backend_status") or "")
    if status not in {"available", "unavailable", "partial", "blocked"}:
        status = "blocked"
    return {"qlib_backend_status": status}


def safety_flags(
    *,
    training_requested: bool = False,
    qlib_challenger_training_performed: bool = False,
    qlib_training_performed: bool = False,
) -> dict[str, bool]:
    return {
        **SAFETY_FLAGS,
        "training_requested": bool(training_requested),
        "qlib_challenger_training_performed": bool(qlib_challenger_training_performed),
        "qlib_training_performed": bool(qlib_training_performed),
        "qlib_runtime_updated": False,
        "ai_shadow_training_performed": False,
        "registry_write_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_sqlite": False,
    }
