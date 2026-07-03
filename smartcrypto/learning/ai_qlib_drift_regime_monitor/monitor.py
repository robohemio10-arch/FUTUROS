"""Research-only AI/Qlib drift and regime monitor.

The monitor reads existing JSON evidence and optionally writes a report under
``data/reports`` only when explicitly requested. It never trains models,
promotes artifacts, writes registries, updates runtime, or submits orders.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ai_qlib_drift_regime_monitor_v1"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_REPORT_JSON = Path("data/reports/ai_qlib_drift_regime_monitor_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/ai_qlib_drift_regime_monitor_v1.md")
MIN_SPLITS_FOR_DRIFT = 2
CRITICAL_RELATIVE_DROP = 0.5
CRITICAL_ABSOLUTE_RANGE = 0.35
DEGRADED_ABSOLUTE_RANGE = 0.15
MAX_MISSINGNESS_WARNING = 0.1
MAX_MISSINGNESS_BLOCK = 0.3

INPUT_SOURCES: tuple[tuple[str, str, bool], ...] = (
    ("feature_contract", "data/reports/ai_unified_feature_contract_v1.json", True),
    ("dataset_manifest", "data/reports/ai_unified_dataset_manifest_v1.json", True),
    ("target_store", "data/reports/financial_label_target_store_v1.json", True),
    ("walkforward", "data/reports/walkforward_anti_leakage_split_engine_v1.json", True),
    ("walkforward_baseline", "data/reports/walkforward_baseline_summary_v1.json", False),
    ("qlib_trainer", "data/reports/qlib_institutional_ranking_trainer_v1.json", False),
    ("ai_shadow_trainer", "data/reports/ai_shadow_quality_veto_trainer_v1.json", False),
    ("paper_autotrain_feedback_loop", "data/reports/paper_autotrain_feedback_loop_v1.json", False),
    (
        "daily_learning_evidence_readiness",
        "data/reports/daily_learning_evidence_readiness_integration_v1.json",
        False,
    ),
)


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    relative_path: str
    path: Path
    required: bool
    exists: bool
    sha256: str | None
    load_error: str | None
    payload: dict[str, Any] | None

    def as_report_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "relative_path": self.relative_path,
            "path": str(self.path),
            "required": self.required,
            "exists": self.exists,
            "sha256": self.sha256,
            "load_error": self.load_error,
        }


def build_ai_qlib_drift_regime_monitor_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    report_json_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a read-only drift/regime report from existing evidence."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    sources = load_input_sources(root)
    payloads = {
        source.source_id: source.payload
        for source in sources
        if source.exists and source.load_error is None and isinstance(source.payload, dict)
    }
    missing_required = [
        source.relative_path
        for source in sources
        if source.required and (not source.exists or source.load_error is not None)
    ]
    blockers = [f"missing_required_source:{path}" for path in missing_required]
    warnings: list[str] = []

    lineage_hashes = collect_lineage_hashes(payloads)
    feature_drift = build_feature_drift_section(payloads)
    target_drift = build_target_drift_section(payloads)
    walkforward_regime = build_walkforward_regime_section(payloads)
    qlib_drift = build_qlib_performance_drift_section(payloads)
    ai_shadow_drift = build_ai_shadow_quality_drift_section(payloads)
    sections = [feature_drift, target_drift, walkforward_regime, qlib_drift, ai_shadow_drift]

    for section in sections:
        warnings.extend(str(item) for item in section.get("warnings", []) if item)
        blockers.extend(str(item) for item in section.get("blockers", []) if item)

    regime_summary = summarize_regimes(sections)
    drift_summary = summarize_drift(sections)
    status, reason = decide_status(blockers, warnings, drift_summary)
    safety = safety_flags()
    report_json = resolve(root, report_json_path, DEFAULT_REPORT_JSON)
    report_md = resolve(root, report_markdown_path, DEFAULT_REPORT_MD)

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "input_sources": [source.as_report_dict() for source in sources],
        "lineage_hashes": lineage_hashes,
        "feature_drift_section": feature_drift,
        "target_drift_section": target_drift,
        "walkforward_regime_section": walkforward_regime,
        "qlib_performance_drift_section": qlib_drift,
        "ai_shadow_quality_drift_section": ai_shadow_drift,
        "regime_summary": regime_summary,
        "drift_summary": drift_summary,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": {
            "json": str(report_json),
            "markdown": str(report_md),
        },
        **safety,
        "safety_flags": safety,
    }
    if write_report:
        write_reports(report, report_json, report_md)
        report["write_performed"] = True
        write_json(report_json, report)
    return report


def load_input_sources(project_root: Path) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for source_id, relative_path, required in INPUT_SOURCES:
        path = project_root / relative_path
        exists = path.is_file()
        sha256 = file_sha256(path) if exists else None
        payload: dict[str, Any] | None = None
        load_error: str | None = None
        if exists:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                load_error = f"invalid_json:{exc.msg}"
            else:
                if isinstance(parsed, dict):
                    payload = parsed
                else:
                    load_error = "json_root_not_object"
        records.append(
            SourceRecord(
                source_id=source_id,
                relative_path=relative_path,
                path=path.resolve(),
                required=required,
                exists=exists,
                sha256=sha256,
                load_error=load_error,
                payload=payload,
            )
        )
    return records


def build_feature_drift_section(payloads: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    contract = payloads.get("feature_contract", {})
    manifest = payloads.get("dataset_manifest", {})
    feature_columns = list_of_strings(contract.get("feature_columns"))
    dataset_columns = set(list_of_strings(manifest.get("selected_training_dataset_columns")))
    null_counts = mapping_or_empty(manifest.get("null_counts"))
    row_count = to_int(manifest.get("row_count") or manifest.get("selected_training_dataset_rows"))

    missing_features = sorted(column for column in feature_columns if dataset_columns and column not in dataset_columns)
    coverage_ratio = 1.0
    if feature_columns and dataset_columns:
        coverage_ratio = round((len(feature_columns) - len(missing_features)) / len(feature_columns), 8)

    missingness = []
    for column in feature_columns:
        null_count = to_int(null_counts.get(column))
        null_rate = round(null_count / row_count, 8) if row_count > 0 else None
        missingness.append({"feature": column, "null_count": null_count, "null_rate": null_rate})
    known_rates = [item["null_rate"] for item in missingness if isinstance(item.get("null_rate"), float)]
    max_missingness = max(known_rates) if known_rates else None
    mean_missingness = round(sum(known_rates) / len(known_rates), 8) if known_rates else None

    distribution = feature_distribution_drift(contract, manifest)
    blockers: list[str] = []
    warnings: list[str] = []
    if missing_features:
        blockers.append("feature_contract_columns_missing_from_dataset_manifest")
    if max_missingness is None:
        warnings.append("feature_missingness_drift_insufficient_data")
    elif max_missingness > MAX_MISSINGNESS_BLOCK:
        blockers.append("feature_missingness_critical")
    elif max_missingness > MAX_MISSINGNESS_WARNING:
        warnings.append("feature_missingness_degraded")
    if distribution["regime_label"] == "insufficient_data":
        warnings.append("feature_distribution_drift_insufficient_data")
    elif distribution["regime_label"] == "unstable":
        blockers.append("feature_distribution_drift_critical")
    elif distribution["regime_label"] == "degraded":
        warnings.append("feature_distribution_drift_degraded")

    regime_label = worst_regime(
        [
            "unstable" if blockers else "stable",
            "degraded" if warnings else "stable",
            distribution["regime_label"],
        ]
    )
    return {
        "status": "ok" if not blockers else "blocked",
        "regime_label": regime_label,
        "feature_column_count": len(feature_columns),
        "dataset_column_count": len(dataset_columns),
        "coverage_ratio": coverage_ratio,
        "missing_feature_columns": missing_features,
        "feature_missingness": missingness,
        "max_missingness_rate": max_missingness,
        "mean_missingness_rate": mean_missingness,
        "feature_distribution_drift": distribution,
        "blockers": blockers,
        "warnings": warnings,
    }


def build_target_drift_section(payloads: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    target = payloads.get("target_store", {})
    manifest = payloads.get("dataset_manifest", {})
    target_distribution = normalize_distribution(
        first_mapping(
            target,
            ("label_distribution", "target_distribution", "class_distribution"),
        )
    )
    manifest_distribution = normalize_distribution(manifest.get("label_distribution"))
    if not target_distribution:
        target_distribution = distribution_from_records(target.get("target_records"), "target_win_loss")

    drift = distribution_delta(target_distribution, manifest_distribution)
    warnings: list[str] = []
    blockers: list[str] = []
    if not target_distribution:
        warnings.append("target_distribution_drift_insufficient_data")
        regime_label = "insufficient_data"
    elif drift is not None and drift > CRITICAL_ABSOLUTE_RANGE:
        blockers.append("target_distribution_drift_critical")
        regime_label = "unstable"
    elif drift is not None and drift > DEGRADED_ABSOLUTE_RANGE:
        warnings.append("target_distribution_drift_degraded")
        regime_label = "degraded"
    else:
        regime_label = "stable"

    return {
        "status": "ok" if not blockers else "blocked",
        "regime_label": regime_label,
        "target_columns": list_of_strings(target.get("target_columns")),
        "target_distribution": target_distribution,
        "reference_distribution": manifest_distribution,
        "max_distribution_delta": drift,
        "blockers": blockers,
        "warnings": warnings,
    }


def build_walkforward_regime_section(payloads: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    walkforward = payloads.get("walkforward", {})
    splits = list_of_mappings(walkforward.get("splits"))
    split_count = to_int(walkforward.get("split_count"), default=len(splits))
    row_counts = [to_int(split.get("test_row_count") or split.get("row_count")) for split in splits]
    min_rows = min(row_counts) if row_counts else None
    max_rows = max(row_counts) if row_counts else None
    row_count_ratio = round(min_rows / max_rows, 8) if min_rows is not None and max_rows else None
    leakage_audit = mapping_or_empty(walkforward.get("leakage_audit"))

    warnings: list[str] = []
    blockers: list[str] = []
    if split_count < MIN_SPLITS_FOR_DRIFT:
        warnings.append("walkforward_split_count_insufficient_for_drift")
        regime_label = "insufficient_data"
    elif leakage_audit.get("leakage_status") not in {None, "ok"}:
        blockers.append("walkforward_leakage_not_ok")
        regime_label = "unstable"
    elif row_count_ratio is not None and row_count_ratio < 0.5:
        warnings.append("walkforward_split_balance_degraded")
        regime_label = "degraded"
    else:
        regime_label = "stable"

    return {
        "status": "ok" if not blockers else "blocked",
        "regime_label": regime_label,
        "split_count": split_count,
        "split_row_counts": row_counts,
        "min_split_rows": min_rows,
        "max_split_rows": max_rows,
        "split_row_count_ratio": row_count_ratio,
        "leakage_status": leakage_audit.get("leakage_status"),
        "blockers": blockers,
        "warnings": warnings,
    }


def build_qlib_performance_drift_section(payloads: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    qlib = payloads.get("qlib_trainer", {})
    metrics = list_of_mappings(qlib.get("metrics_by_split"))
    rank_ic = metric_drift(metrics, "rank_ic")
    precision = metric_drift(metrics, "precision_at_10")
    expected_value = metric_drift(metrics, "selected_top_k_expected_value")
    warnings: list[str] = []
    blockers: list[str] = []
    metric_labels = [rank_ic["regime_label"], precision["regime_label"], expected_value["regime_label"]]
    if not metrics:
        warnings.append("qlib_metrics_drift_insufficient_data")
    for label, blocker, warning in (
        (rank_ic["regime_label"], "qlib_rank_ic_drift_critical", "qlib_rank_ic_drift_degraded"),
        (precision["regime_label"], "qlib_precision_at_10_drift_critical", "qlib_precision_at_10_drift_degraded"),
        (
            expected_value["regime_label"],
            "qlib_selected_top_k_expected_value_drift_critical",
            "qlib_selected_top_k_expected_value_drift_degraded",
        ),
    ):
        if label == "unstable":
            blockers.append(blocker)
        elif label == "degraded":
            warnings.append(warning)
    return {
        "status": "ok" if not blockers else "blocked",
        "regime_label": worst_regime(metric_labels),
        "metric_split_count": len(metrics),
        "rank_ic_drift": rank_ic,
        "precision_at_10_drift": precision,
        "selected_top_k_expected_value_drift": expected_value,
        "aggregate_metrics": mapping_or_empty(qlib.get("aggregate_metrics")),
        "training_performed_in_source_report": bool(qlib.get("qlib_training_performed")),
        "training_performed_by_monitor": False,
        "blockers": blockers,
        "warnings": warnings,
    }


def build_ai_shadow_quality_drift_section(payloads: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    shadow = payloads.get("ai_shadow_trainer", {})
    metrics = list_of_mappings(shadow.get("metrics_by_split"))
    ev_delta = metric_drift(metrics, "net_ev_delta_if_applied_research_only")
    precision_reject = metric_drift(metrics, "precision_reject")
    recall_reject = metric_drift(metrics, "recall_reject")
    warnings: list[str] = []
    blockers: list[str] = []
    metric_labels = [ev_delta["regime_label"], precision_reject["regime_label"], recall_reject["regime_label"]]
    if not metrics:
        warnings.append("ai_shadow_quality_drift_insufficient_data")
    for label, blocker, warning in (
        (ev_delta["regime_label"], "ai_shadow_net_ev_delta_drift_critical", "ai_shadow_net_ev_delta_drift_degraded"),
        (
            precision_reject["regime_label"],
            "ai_shadow_precision_reject_drift_critical",
            "ai_shadow_precision_reject_drift_degraded",
        ),
        (
            recall_reject["regime_label"],
            "ai_shadow_recall_reject_drift_critical",
            "ai_shadow_recall_reject_drift_degraded",
        ),
    ):
        if label == "unstable":
            blockers.append(blocker)
        elif label == "degraded":
            warnings.append(warning)
    return {
        "status": "ok" if not blockers else "blocked",
        "regime_label": worst_regime(metric_labels),
        "metric_split_count": len(metrics),
        "net_ev_delta_drift": ev_delta,
        "precision_reject_drift": precision_reject,
        "recall_reject_drift": recall_reject,
        "aggregate_metrics": mapping_or_empty(shadow.get("aggregate_metrics")),
        "training_performed_in_source_report": bool(shadow.get("ai_shadow_challenger_training_performed")),
        "training_performed_by_monitor": False,
        "blockers": blockers,
        "warnings": warnings,
    }


def summarize_regimes(sections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    labels = [str(section.get("regime_label", "insufficient_data")) for section in sections]
    counts = {label: labels.count(label) for label in ("stable", "degraded", "unstable", "insufficient_data")}
    return {
        "overall_regime": worst_regime(labels),
        "regime_counts": counts,
        "section_regimes": {str(section_name(section)): section.get("regime_label") for section in sections},
    }


def summarize_drift(sections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    blockers = [item for section in sections for item in section.get("blockers", [])]
    warnings = [item for section in sections for item in section.get("warnings", [])]
    return {
        "drift_status": "blocked" if blockers else "warning" if warnings else "ok",
        "critical_drift_detected": bool(blockers),
        "warning_drift_detected": bool(warnings),
        "blocked_section_count": sum(1 for section in sections if section.get("status") == "blocked"),
        "warning_count": len(warnings),
        "blocker_count": len(blockers),
        "promotion_eligible": False,
    }


def decide_status(blockers: Sequence[str], warnings: Sequence[str], drift_summary: Mapping[str, Any]) -> tuple[str, str]:
    if blockers:
        return "blocked", "critical_drift_or_missing_required_sources"
    if bool(drift_summary.get("critical_drift_detected")):
        return "blocked", "critical_drift_detected"
    if warnings:
        return "warning", "drift_monitor_warnings_present"
    return "ok", "drift_regime_stable_research_only"


def metric_drift(metrics: Sequence[Mapping[str, Any]], metric_name: str) -> dict[str, Any]:
    values = [to_float(metric.get(metric_name)) for metric in metrics if to_float(metric.get(metric_name)) is not None]
    values = [value for value in values if value is not None]
    if len(values) < MIN_SPLITS_FOR_DRIFT:
        return {
            "metric": metric_name,
            "values": values,
            "split_count": len(values),
            "latest": values[-1] if values else None,
            "first": values[0] if values else None,
            "delta": None,
            "relative_drop": None,
            "range": None,
            "regime_label": "insufficient_data",
        }
    first = values[0]
    latest = values[-1]
    delta = round(latest - first, 10)
    value_range = round(max(values) - min(values), 10)
    range_scale = max(max(abs(value) for value in values), 1.0)
    normalized_range = round(value_range / range_scale, 10)
    relative_drop = None
    if first > 0 and latest < first:
        relative_drop = round((first - latest) / abs(first), 10)
    regime_label = "stable"
    if relative_drop is not None and relative_drop >= CRITICAL_RELATIVE_DROP:
        regime_label = "unstable"
    elif normalized_range >= CRITICAL_ABSOLUTE_RANGE:
        regime_label = "unstable"
    elif relative_drop is not None and relative_drop >= 0.25:
        regime_label = "degraded"
    elif normalized_range >= DEGRADED_ABSOLUTE_RANGE:
        regime_label = "degraded"
    return {
        "metric": metric_name,
        "values": values,
        "split_count": len(values),
        "latest": latest,
        "first": first,
        "delta": delta,
        "relative_drop": relative_drop,
        "range": value_range,
        "normalized_range": normalized_range,
        "regime_label": regime_label,
    }


def feature_distribution_drift(contract: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    feature_columns = list_of_strings(contract.get("feature_columns"))
    stats = first_mapping(
        manifest,
        (
            "feature_statistics",
            "feature_distribution",
            "feature_distributions",
            "column_statistics",
            "feature_stats",
        ),
    )
    deltas: list[dict[str, Any]] = []
    for column in feature_columns:
        column_stats = mapping_or_empty(stats.get(column))
        reference = first_numeric(column_stats, ("reference_mean", "baseline_mean", "train_mean", "mean_reference"))
        current = first_numeric(column_stats, ("current_mean", "recent_mean", "test_mean", "mean_current"))
        reference_std = first_numeric(column_stats, ("reference_std", "baseline_std", "train_std", "std_reference"))
        if reference is None or current is None:
            continue
        denominator = max(abs(reference), abs(reference_std or 0.0), 1.0)
        normalized_delta = abs(current - reference) / denominator
        deltas.append(
            {
                "feature": column,
                "reference_mean": reference,
                "current_mean": current,
                "normalized_delta": round(normalized_delta, 10),
            }
        )
    if not deltas:
        return {"regime_label": "insufficient_data", "max_normalized_delta": None, "feature_deltas": []}
    max_delta = max(float(item["normalized_delta"]) for item in deltas)
    if max_delta >= CRITICAL_ABSOLUTE_RANGE:
        regime_label = "unstable"
    elif max_delta >= DEGRADED_ABSOLUTE_RANGE:
        regime_label = "degraded"
    else:
        regime_label = "stable"
    return {
        "regime_label": regime_label,
        "max_normalized_delta": round(max_delta, 10),
        "feature_deltas": deltas,
    }


def distribution_delta(current: Mapping[str, float], reference: Mapping[str, float]) -> float | None:
    if not current or not reference:
        return None
    keys = set(current) | set(reference)
    return round(max(abs(float(current.get(key, 0.0)) - float(reference.get(key, 0.0))) for key in keys), 10)


def normalize_distribution(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    numeric = {str(key): to_float(raw) for key, raw in value.items()}
    numeric = {key: value for key, value in numeric.items() if value is not None and value >= 0}
    total = sum(numeric.values())
    if total <= 0:
        return {}
    return {key: round(value / total, 10) for key, value in sorted(numeric.items())}


def distribution_from_records(records: Any, column: str) -> dict[str, float]:
    counts: dict[str, float] = {}
    for record in list_of_mappings(records):
        value = record.get(column)
        if value is None:
            continue
        key = str(value)
        counts[key] = counts.get(key, 0.0) + 1.0
    return normalize_distribution(counts)


def collect_lineage_hashes(payloads: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "feature_contract_hash": payloads.get("feature_contract", {}).get("contract_hash"),
        "dataset_hash": payloads.get("dataset_manifest", {}).get("dataset_hash"),
        "target_store_hash": payloads.get("target_store", {}).get("target_store_hash"),
        "walkforward_split_engine_hash": payloads.get("walkforward", {}).get("split_engine_hash"),
        "qlib_feature_contract_hash": payloads.get("qlib_trainer", {}).get("feature_contract_hash"),
        "qlib_dataset_hash": payloads.get("qlib_trainer", {}).get("dataset_hash"),
        "ai_shadow_feature_contract_hash": payloads.get("ai_shadow_trainer", {}).get("feature_contract_hash"),
        "ai_shadow_dataset_hash": payloads.get("ai_shadow_trainer", {}).get("dataset_hash"),
        "paper_autotrain_lineage_hashes": payloads.get("paper_autotrain_feedback_loop", {}).get("lineage_hashes", {}),
    }


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "operational_authority": False,
        "readiness_release_authority": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "changes_model": False,
        "model_promotion_performed": False,
        "registry_write_performed": False,
        "active_model_changed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }


def section_name(section: Mapping[str, Any]) -> str:
    for key in (
        "feature_column_count",
        "target_columns",
        "split_count",
        "rank_ic_drift",
        "net_ev_delta_drift",
    ):
        if key in section:
            return {
                "feature_column_count": "feature_drift",
                "target_columns": "target_drift",
                "split_count": "walkforward_regime",
                "rank_ic_drift": "qlib_performance_drift",
                "net_ev_delta_drift": "ai_shadow_quality_drift",
            }[key]
    return "unknown"


def worst_regime(labels: Iterable[Any]) -> str:
    order = {"stable": 0, "insufficient_data": 1, "degraded": 2, "unstable": 3}
    normalized = [str(label) for label in labels if str(label) in order]
    if not normalized:
        return "insufficient_data"
    return max(normalized, key=lambda label: order[label])


def first_mapping(payload: Mapping[str, Any], keys: Sequence[str]) -> Mapping[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def first_numeric(payload: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = to_float(payload.get(key))
        if value is not None:
            return value
    return None


def mapping_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def list_of_strings(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [str(item) for item in value if str(item)]
    return []


def list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def to_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, str):
        try:
            numeric = float(value.strip())
        except ValueError:
            return None
        return numeric if math.isfinite(numeric) else None
    return None


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_reports(report: dict[str, Any], report_json: Path, report_md: Path) -> None:
    write_json(report_json, report)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_markdown(report), encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def render_markdown(report: Mapping[str, Any]) -> str:
    qlib = mapping_or_empty(report.get("qlib_performance_drift_section"))
    shadow = mapping_or_empty(report.get("ai_shadow_quality_drift_section"))
    drift = mapping_or_empty(report.get("drift_summary"))
    regime = mapping_or_empty(report.get("regime_summary"))
    return "\n".join(
        [
            "# AI/Qlib Drift Regime Monitor V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Overall regime: `{regime.get('overall_regime')}`",
            f"- Critical drift detected: `{drift.get('critical_drift_detected')}`",
            f"- Qlib regime: `{qlib.get('regime_label')}`",
            f"- AI Shadow regime: `{shadow.get('regime_label')}`",
            f"- Blockers: `{len(report.get('blockers', []))}`",
            f"- Warnings: `{len(report.get('warnings', []))}`",
            "",
            "Research-only evidence. This monitor does not train, promote, write registry, update runtime, access private exchange, or send orders.",
            "",
        ]
    )
