from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.ml.sidecar_financial_evaluation import (
    SidecarFinancialEvaluationError,
    run_sidecar_financial_evaluation,
    write_json,
)


FORBIDDEN_NORMALIZED_FEATURE_COLUMNS = {
    "return_pct",
    "raw_return_pct",
    "normalized_return_pct",
    "gross_return_pct",
    "leveraged_return_pct",
    "net_return_pct",
    "mfe_pct",
    "mae_pct",
    "pnl",
}


class NormalizedFinancialEvaluationError(ValueError):
    pass


def run_normalized_financial_evaluation(
    features: pd.DataFrame,
    sidecar: pd.DataFrame,
    *,
    features_path: str | Path,
    sidecar_path: str | Path,
    id_column: str = "trade_id",
    target_column: str = "target_win",
    return_column: str = "net_return_pct",
    folds: int = 5,
    embargo_minutes: int = 60,
    seed: int = 42,
    sidecar_report: dict[str, Any] | str | Path | None = None,
    allow_blocked_sidecar: bool = False,
) -> dict[str, Any]:
    validate_normalized_features(features)
    sidecar_gate = normalize_sidecar_report(sidecar_report)
    if sidecar_gate["status"] == "BLOCKED" and not allow_blocked_sidecar:
        return blocked_sidecar_payload(
            features_path=features_path,
            sidecar_path=sidecar_path,
            id_column=id_column,
            target_column=target_column,
            return_column=return_column,
            sidecar_gate=sidecar_gate,
        )
    if return_column not in sidecar.columns:
        raise NormalizedFinancialEvaluationError(f"normalized_return_column_missing:{return_column}")
    if sidecar[return_column].isna().any():
        raise NormalizedFinancialEvaluationError("normalized_return_column_contains_nulls")
    try:
        report = run_sidecar_financial_evaluation(
            features,
            sidecar,
            features_path=features_path,
            sidecar_path=sidecar_path,
            id_column=id_column,
            target_column=target_column,
            return_column=return_column,
            folds=folds,
            embargo_minutes=embargo_minutes,
            seed=seed,
        )
    except SidecarFinancialEvaluationError as exc:
        raise NormalizedFinancialEvaluationError(str(exc)) from exc
    payload = report.to_dict()
    payload["return_semantics"] = "normalized_net_return_pct"
    payload["sidecar_status"] = sidecar_gate["status"]
    payload["sidecar_quality_flag_counts"] = sidecar_gate["quality_flag_counts"]
    payload["sidecar_outlier_summary"] = sidecar_gate["outlier_summary"]
    payload["limitations"] = list(payload.get("limitations", []))
    payload["recommended_next_action"] = "normalized_financial_metrics_available_for_offline_research"
    if sidecar_gate["missing"]:
        payload["status"] = "WARNING"
        payload["limitations"].append("sidecar_report_missing_or_not_provided")
        payload["recommended_next_action"] = "review_sidecar_report_before_using_metrics"
    elif sidecar_gate["status"] == "BLOCKED" and allow_blocked_sidecar:
        payload["status"] = "WARNING"
        payload["limitations"].append("blocked_sidecar_allowed_for_diagnostic_run")
        payload["recommended_next_action"] = "diagnostic_only_do_not_validate_financial_metrics"
    else:
        payload["status"] = "OK" if sidecar_gate["status"] == "OK" else "WARNING"
    rename_financial_metrics(payload)
    return payload


def validate_normalized_features(frame: pd.DataFrame) -> None:
    forbidden = [
        column
        for column in frame.columns
        if column in FORBIDDEN_NORMALIZED_FEATURE_COLUMNS
        or column.startswith("close_1m_")
        or column.startswith("close_5m_")
    ]
    if forbidden:
        raise NormalizedFinancialEvaluationError(f"features_contain_forbidden_financial_columns:{forbidden}")


def normalize_sidecar_report(sidecar_report: dict[str, Any] | str | Path | None) -> dict[str, Any]:
    missing = False
    payload: dict[str, Any]
    if sidecar_report is None:
        payload = {}
        missing = True
    elif isinstance(sidecar_report, (str, Path)):
        path = Path(sidecar_report)
        if not path.exists():
            payload = {}
            missing = True
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise NormalizedFinancialEvaluationError("sidecar_report_root_must_be_mapping")
    elif isinstance(sidecar_report, dict):
        payload = dict(sidecar_report)
    else:
        raise NormalizedFinancialEvaluationError("sidecar_report_must_be_mapping_or_path")

    status = str(payload.get("status") or ("WARNING" if missing else "UNKNOWN")).upper()
    quality_counts = payload.get("quality_flag_counts") or {}
    outlier_summary = payload.get("outlier_summary") or {}
    return {
        "status": status,
        "missing": missing,
        "quality_flag_counts": quality_counts if isinstance(quality_counts, dict) else {},
        "outlier_summary": outlier_summary if isinstance(outlier_summary, dict) else {},
        "recommended_next_action": payload.get("recommended_next_action"),
    }


def blocked_sidecar_payload(
    *,
    features_path: str | Path,
    sidecar_path: str | Path,
    id_column: str,
    target_column: str,
    return_column: str,
    sidecar_gate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "reason": "normalized_return_sidecar_blocked",
        "features_path": str(features_path),
        "sidecar_path": str(sidecar_path),
        "id_column": id_column,
        "target_column": target_column,
        "return_column": return_column,
        "return_semantics": "normalized_net_return_pct",
        "sidecar_status": "BLOCKED",
        "sidecar_quality_flag_counts": sidecar_gate["quality_flag_counts"],
        "sidecar_outlier_summary": sidecar_gate["outlier_summary"],
        "fold_metrics": [],
        "aggregate_metrics": {},
        "limitations": ["normalized_return_sidecar_blocked"],
        "recommended_next_action": (
            sidecar_gate.get("recommended_next_action")
            or "block_normalized_financial_metrics_until_required_price_side_inputs_are_repaired"
        ),
    }


def rename_financial_metrics(payload: dict[str, Any]) -> None:
    for fold in payload.get("fold_metrics", []):
        for metrics in fold.get("baseline_metrics", {}).values():
            rename_metric_keys(metrics)
    for metrics in payload.get("aggregate_metrics", {}).values():
        rename_metric_keys(metrics)


def rename_metric_keys(metrics: dict[str, Any]) -> None:
    if "average_return_pct" in metrics:
        metrics["average_net_return_pct"] = metrics.pop("average_return_pct")
    if "total_return_pct" in metrics:
        metrics["total_net_return_pct"] = metrics.pop("total_return_pct")
