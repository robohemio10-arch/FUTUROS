"""Evaluate daily paper auto-training quarantine candidates.

This module is research-only and read-only by default. It does not import or
execute Qlib, IA Shadow runtime, Freqtrade, ccxt, Docker tooling, RiskManager,
or signal producers. Optional writes are restricted to JSON/Markdown reports
under data/reports.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = "paper_autotrain_quarantine_candidate_evaluation_v1"

DECISION_KEEP_QUARANTINE = "MANTER_EM_QUARENTENA"
DECISION_MANUAL_REVIEW = "APROVADO_PARA_REVISAO_MANUAL_FUTURA"
DECISION_DISCARD = "DESCARTAR_CANDIDATO"
DECISION_INSUFFICIENT_EVIDENCE = "BLOQUEADO_POR_EVIDENCIA_INSUFICIENTE"

DEFAULT_REGISTRY_PATH = Path("data/registries/quarantine/paper_autotrain_candidate_registry_v1.json")
DEFAULT_MODEL_DIR = Path("data/models/quarantine/paper_autotrain")
DEFAULT_RESEARCH_DIR = Path("data/research/paper_autotrain_daily_quarantine")
DEFAULT_ACTIVATION_REPORT = Path("data/reports/paper_autotrain_daily_quarantine_activation_v1.json")
DEFAULT_DRIFT_REPORT = Path("data/reports/ai_qlib_drift_regime_monitor_v1.json")
DEFAULT_EXECUTION_COST_REPORT = Path("data/reports/event_driven_backtest_execution_cost_gate_v1.json")
DEFAULT_MONTE_CARLO_REPORT = Path("data/reports/monte_carlo_risk_ruin_stress_gate_v1.json")
DEFAULT_READINESS_REPORT = Path("data/reports/readiness_snapshot_v2.json")
DEFAULT_OUTPUT_JSON = Path("data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.json")
DEFAULT_OUTPUT_MARKDOWN = Path("data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.md")

MIN_MICROBATCH_ROWS = 100
MIN_CLASS_POSITIVE_COUNT = 20
MIN_CLASS_NEGATIVE_COUNT = 20
MIN_FEATURE_COUNT = 5

ALLOWED_REPORT_ROOT = Path("data/reports")


@dataclass(frozen=True)
class EvaluationPaths:
    registry: Path
    model_dir: Path
    research_dir: Path
    activation_report: Path
    drift_report: Path
    execution_cost_report: Path
    monte_carlo_report: Path
    readiness_report: Path
    output_json: Path
    output_markdown: Path


def build_paper_autotrain_quarantine_candidate_evaluation_v1(
    *,
    project_root: str | Path,
    write_report: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    fail_on_operational_write: bool = False,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the quarantine candidate evaluation report."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    paths = build_paths(root, output_json_path, output_markdown_path)
    output_paths = {
        "json": str(paths.output_json),
        "markdown": str(paths.output_markdown),
    }
    write_errors = validate_report_write_paths(root, paths) if (write_report or fail_on_operational_write) else []

    registry_payload, registry_reason = load_json(paths.registry)
    activation_payload, _ = load_json(paths.activation_report)
    drift_gate = read_gate(paths.drift_report, "drift_gate")
    execution_cost_gate = read_gate(paths.execution_cost_report, "execution_cost_gate")
    monte_carlo_gate = read_gate(paths.monte_carlo_report, "monte_carlo_gate")
    readiness_gate = read_gate(paths.readiness_report, "readiness")

    registry_exists = paths.registry.exists()
    registry_candidates = extract_registry_candidates(registry_payload)
    microbatch_metrics = collect_microbatch_metrics(
        root=root,
        paths=paths,
        registry_candidates=registry_candidates,
        activation_payload=activation_payload,
    )

    blockers: list[str] = []
    warnings: list[str] = []
    if write_errors:
        blockers.extend(write_errors)
    if not registry_exists:
        blockers.append("missing_quarantine_registry")
    elif not registry_candidates:
        blockers.append("no_quarantine_candidates")

    external_blockers = external_gate_blockers(
        drift_gate=drift_gate,
        execution_cost_gate=execution_cost_gate,
        monte_carlo_gate=monte_carlo_gate,
        readiness_gate=readiness_gate,
    )
    optional_gate_warnings = optional_gate_warnings_from(
        drift_gate=drift_gate,
        execution_cost_gate=execution_cost_gate,
        monte_carlo_gate=monte_carlo_gate,
        readiness_gate=readiness_gate,
    )
    warnings.extend(optional_gate_warnings)

    candidates = [
        evaluate_candidate(
            root=root,
            paths=paths,
            registry_candidate=candidate,
            external_blockers=external_blockers,
        )
        for candidate in registry_candidates
    ]

    if any(not bool(candidate["artifact_exists"]) or not bool(candidate["artifact_hash_validated"]) for candidate in candidates):
        blockers.append("quarantine_candidate_artifact_integrity_failed")

    if candidates and microbatch_metrics["observed_microbatch_rows"] < MIN_MICROBATCH_ROWS:
        blockers.append("min_microbatch_rows_not_met")
    if candidates and microbatch_metrics["observed_class_positive_count"] < MIN_CLASS_POSITIVE_COUNT:
        blockers.append("min_class_positive_count_not_met")
    if candidates and microbatch_metrics["observed_class_negative_count"] < MIN_CLASS_NEGATIVE_COUNT:
        blockers.append("min_class_negative_count_not_met")
    if candidates and microbatch_metrics["observed_feature_count"] < MIN_FEATURE_COUNT:
        blockers.append("min_feature_count_not_met")
    blockers.extend(external_blockers)

    blocked_candidate_count = sum(1 for candidate in candidates if candidate["evaluation_status"] == "blocked")
    warning_candidate_count = sum(1 for candidate in candidates if candidate["evaluation_status"] == "warning")
    eligible_candidate_count = sum(1 for candidate in candidates if candidate["eligible_for_manual_review"])
    artifact_integrity_status = "ok"
    if candidates and any(candidate["artifact_hash_validated"] is False for candidate in candidates):
        artifact_integrity_status = "failed"
    elif candidates and any(candidate["artifact_exists"] is False for candidate in candidates):
        artifact_integrity_status = "failed"
    elif not candidates:
        artifact_integrity_status = "not_evaluated"

    status, reason, decision = decide_report_status(
        blockers=blockers,
        candidates=candidates,
        eligible_candidate_count=eligible_candidate_count,
    )
    safety = safety_flags(write_requested=write_report, write_performed=False)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": decision,
        "quarantine_registry_path": str(paths.registry),
        "quarantine_registry_exists": registry_exists,
        "quarantine_registry_reason": registry_reason,
        "candidate_artifact_count": sum(1 for candidate in candidates if candidate["artifact_exists"]),
        "candidate_count": len(registry_candidates),
        "evaluated_candidate_count": len(candidates),
        "eligible_candidate_count": eligible_candidate_count,
        "blocked_candidate_count": blocked_candidate_count,
        "warning_candidate_count": warning_candidate_count,
        "qlib_candidate_count": sum(1 for candidate in candidates if candidate["backend_id"] == "qlib"),
        "ai_shadow_candidate_count": sum(1 for candidate in candidates if candidate["backend_id"] == "ai_shadow"),
        "min_microbatch_rows": MIN_MICROBATCH_ROWS,
        "observed_microbatch_rows": microbatch_metrics["observed_microbatch_rows"],
        "min_class_positive_count": MIN_CLASS_POSITIVE_COUNT,
        "observed_class_positive_count": microbatch_metrics["observed_class_positive_count"],
        "min_class_negative_count": MIN_CLASS_NEGATIVE_COUNT,
        "observed_class_negative_count": microbatch_metrics["observed_class_negative_count"],
        "min_feature_count": MIN_FEATURE_COUNT,
        "observed_feature_count": microbatch_metrics["observed_feature_count"],
        "artifact_integrity_status": artifact_integrity_status,
        "drift_gate_status": drift_gate["status"],
        "execution_cost_gate_status": execution_cost_gate["status"],
        "monte_carlo_gate_status": monte_carlo_gate["status"],
        "readiness_status": readiness_gate["status"],
        "blockers": sorted_unique(blockers),
        "warnings": sorted_unique(warnings),
        "candidates": candidates,
        "output_paths": output_paths,
        **safety,
        "safety_flags": safety,
    }

    if write_report and not write_errors:
        paths.output_json.parent.mkdir(parents=True, exist_ok=True)
        write_json(paths.output_json, report)
        paths.output_markdown.write_text(render_markdown(report), encoding="utf-8")
        safety = safety_flags(write_requested=True, write_performed=True)
        report.update(safety)
        report["safety_flags"] = safety
        report["write_performed"] = True
    return report


def evaluate_candidate(
    *,
    root: Path,
    paths: EvaluationPaths,
    registry_candidate: Mapping[str, Any],
    external_blockers: Sequence[str],
) -> dict[str, Any]:
    candidate_id = str(registry_candidate.get("candidate_id") or "")
    backend_id = str(registry_candidate.get("backend_id") or infer_backend_id(candidate_id))
    artifact_path = resolve_candidate_artifact_path(root, paths, registry_candidate, candidate_id, backend_id)
    artifact_exists = artifact_path.exists()
    artifact_payload: dict[str, Any] = {}
    artifact_hash: str | None = None
    artifact_hash_validated = False
    blockers: list[str] = []
    warnings: list[str] = []

    if not candidate_id:
        blockers.append("missing_candidate_id")
    if backend_id not in {"qlib", "ai_shadow"}:
        blockers.append("unknown_backend_id")
    if not artifact_exists:
        blockers.append("missing_candidate_artifact")
    else:
        artifact_hash = file_sha256(artifact_path)
        loaded, reason = load_json(artifact_path)
        if not isinstance(loaded, dict):
            blockers.append("invalid_candidate_artifact_json")
            warnings.append(reason)
        else:
            artifact_payload = loaded
            artifact_hash_validated = validate_artifact_hash(registry_candidate, artifact_hash)
            if not artifact_hash_validated:
                blockers.append("artifact_hash_mismatch")
            if str(artifact_payload.get("candidate_id") or "") != candidate_id:
                blockers.append("artifact_candidate_id_mismatch")
            if str(artifact_payload.get("backend_id") or "") != backend_id:
                blockers.append("artifact_backend_id_mismatch")

    row_count = int_safe(artifact_payload.get("row_count", registry_candidate.get("row_count", 0)))
    feature_count = int_safe(artifact_payload.get("feature_count", registry_candidate.get("feature_count", 0)))
    class_balance = normalize_class_balance(artifact_payload.get("class_balance", registry_candidate.get("class_balance", {})))
    positive_count = class_balance.get("1", 0)
    negative_count = class_balance.get("0", 0)

    if artifact_exists and artifact_hash_validated:
        if row_count < MIN_MICROBATCH_ROWS:
            blockers.append("min_microbatch_rows_not_met")
        if feature_count < MIN_FEATURE_COUNT:
            blockers.append("min_feature_count_not_met")
        if positive_count < MIN_CLASS_POSITIVE_COUNT:
            blockers.append("min_class_positive_count_not_met")
        if negative_count < MIN_CLASS_NEGATIVE_COUNT:
            blockers.append("min_class_negative_count_not_met")
    blockers.extend(external_blockers)

    promotion_eligible_from_artifact = bool(artifact_payload.get("promotion_eligible", registry_candidate.get("promotion_eligible", False)))
    if promotion_eligible_from_artifact:
        blockers.append("artifact_unexpectedly_promotion_eligible")

    unique_blockers = sorted_unique(blockers)
    unique_warnings = sorted_unique(warnings)
    if unique_blockers:
        evaluation_status = "blocked"
        evaluation_decision = DECISION_INSUFFICIENT_EVIDENCE
    elif unique_warnings:
        evaluation_status = "warning"
        evaluation_decision = DECISION_KEEP_QUARANTINE
    else:
        evaluation_status = "ok"
        evaluation_decision = DECISION_MANUAL_REVIEW

    return {
        "candidate_id": candidate_id,
        "backend_id": backend_id,
        "artifact_path": str(artifact_path),
        "artifact_exists": artifact_exists,
        "artifact_hash": artifact_hash,
        "artifact_hash_validated": artifact_hash_validated,
        "row_count": row_count,
        "feature_count": feature_count,
        "class_balance": class_balance,
        "mean_probability": float_safe(artifact_payload.get("mean_probability", registry_candidate.get("mean_probability"))),
        "promotion_eligible_from_artifact": promotion_eligible_from_artifact,
        "evaluation_status": evaluation_status,
        "evaluation_decision": evaluation_decision,
        "eligible_for_manual_review": evaluation_decision == DECISION_MANUAL_REVIEW,
        "eligible_for_promotion": False,
        "eligible_for_runtime": False,
        "blockers": unique_blockers,
        "warnings": unique_warnings,
    }


def collect_microbatch_metrics(
    *,
    root: Path,
    paths: EvaluationPaths,
    registry_candidates: Sequence[Mapping[str, Any]],
    activation_payload: Mapping[str, Any] | None,
) -> dict[str, int]:
    rows = int_safe((activation_payload or {}).get("microbatch_rows", 0))
    feature_count = int_safe((activation_payload or {}).get("feature_count", 0))
    positive_count = 0
    negative_count = 0
    for candidate in registry_candidates:
        row_count = int_safe(candidate.get("row_count", 0))
        rows = max(rows, row_count)
        feature_count = max(feature_count, int_safe(candidate.get("feature_count", 0)))
        balance = normalize_class_balance(candidate.get("class_balance", {}))
        positive_count = max(positive_count, balance.get("1", 0))
        negative_count = max(negative_count, balance.get("0", 0))

    latest_microbatch = latest_microbatch_path(root, paths, registry_candidates)
    if latest_microbatch is not None and latest_microbatch.exists():
        try:
            frame = pd.read_parquet(latest_microbatch)
        except (OSError, ValueError, ImportError):
            frame = pd.DataFrame()
        if not frame.empty:
            rows = max(rows, int(len(frame)))
            feature_count = max(feature_count, len([column for column in frame.columns if str(column).startswith("feature_")]))
            if "target_profitable" in frame.columns:
                target = pd.to_numeric(frame["target_profitable"], errors="coerce")
                positive_count = max(positive_count, int((target == 1).sum()))
                negative_count = max(negative_count, int((target == 0).sum()))

    return {
        "observed_microbatch_rows": rows,
        "observed_class_positive_count": positive_count,
        "observed_class_negative_count": negative_count,
        "observed_feature_count": feature_count,
    }


def latest_microbatch_path(root: Path, paths: EvaluationPaths, registry_candidates: Sequence[Mapping[str, Any]]) -> Path | None:
    run_ids = [candidate_run_id(candidate) for candidate in registry_candidates]
    for run_id in run_ids:
        if run_id:
            candidate = paths.research_dir / run_id / "incremental_training_microbatch.parquet"
            if candidate.exists():
                return candidate
    matches = sorted(paths.research_dir.glob("*/incremental_training_microbatch.parquet"), key=lambda path: path.stat().st_mtime)
    if matches:
        return matches[-1]
    fallback = root / "data/features/incremental_training_microbatch.parquet"
    return fallback if fallback.exists() else None


def read_gate(path: Path, gate_name: str) -> dict[str, Any]:
    payload, reason = load_json(path)
    if not path.exists():
        return {
            "gate_name": gate_name,
            "status": "missing",
            "reason": "optional_gate_report_missing",
            "path": str(path),
            "blocking": False,
        }
    if not isinstance(payload, Mapping):
        return {
            "gate_name": gate_name,
            "status": "invalid",
            "reason": reason,
            "path": str(path),
            "blocking": True,
        }
    status = normalize_status(payload.get("status") or payload.get("overall_status") or payload.get("readiness_status"))
    reason_value = str(payload.get("reason") or payload.get("status_reason") or status)
    blocking = status == "blocked" or bool(payload.get("blocked", False))
    if gate_name == "readiness":
        blocking = blocking or payload.get("live_release_allowed") is True or payload.get("readiness_approved") is False
        if payload.get("live_release_allowed") is True:
            reason_value = "readiness_live_release_allowed_unexpected"
    return {
        "gate_name": gate_name,
        "status": status,
        "reason": reason_value,
        "path": str(path),
        "blocking": blocking,
    }


def external_gate_blockers(
    *,
    drift_gate: Mapping[str, Any],
    execution_cost_gate: Mapping[str, Any],
    monte_carlo_gate: Mapping[str, Any],
    readiness_gate: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    for gate in (drift_gate, execution_cost_gate, monte_carlo_gate, readiness_gate):
        if gate.get("blocking") is True:
            blockers.append(f"{gate['gate_name']}_blocked")
    return blockers


def optional_gate_warnings_from(
    *,
    drift_gate: Mapping[str, Any],
    execution_cost_gate: Mapping[str, Any],
    monte_carlo_gate: Mapping[str, Any],
    readiness_gate: Mapping[str, Any],
) -> list[str]:
    warnings: list[str] = []
    for gate in (drift_gate, execution_cost_gate, monte_carlo_gate, readiness_gate):
        if gate.get("status") == "missing":
            warnings.append(f"{gate['gate_name']}_report_missing")
    return warnings


def decide_report_status(
    *,
    blockers: Sequence[str],
    candidates: Sequence[Mapping[str, Any]],
    eligible_candidate_count: int,
) -> tuple[str, str, str]:
    unique_blockers = sorted_unique(blockers)
    if "missing_quarantine_registry" in unique_blockers:
        return "blocked", "missing_quarantine_registry", DECISION_KEEP_QUARANTINE
    if "no_quarantine_candidates" in unique_blockers:
        return "blocked", "no_quarantine_candidates", DECISION_KEEP_QUARANTINE
    if "quarantine_candidate_artifact_integrity_failed" in unique_blockers:
        return "blocked", "quarantine_candidate_artifact_integrity_failed", DECISION_KEEP_QUARANTINE
    if "write_path_outside_allowed_reports" in unique_blockers:
        return "blocked", "write_boundary_validation_failed", DECISION_KEEP_QUARANTINE
    if "min_microbatch_rows_not_met" in unique_blockers:
        return "blocked", "min_microbatch_rows_not_met", DECISION_KEEP_QUARANTINE
    if "min_class_positive_count_not_met" in unique_blockers:
        return "blocked", "min_class_positive_count_not_met", DECISION_KEEP_QUARANTINE
    if "min_class_negative_count_not_met" in unique_blockers:
        return "blocked", "min_class_negative_count_not_met", DECISION_KEEP_QUARANTINE
    if "min_feature_count_not_met" in unique_blockers:
        return "blocked", "min_feature_count_not_met", DECISION_KEEP_QUARANTINE
    if unique_blockers:
        return "blocked", unique_blockers[0], DECISION_KEEP_QUARANTINE
    if eligible_candidate_count > 0:
        return "ok", "quarantine_candidates_eligible_for_manual_review", DECISION_MANUAL_REVIEW
    if candidates:
        return "warning", "quarantine_candidates_evaluated_no_manual_review_candidate", DECISION_KEEP_QUARANTINE
    return "blocked", "no_quarantine_candidates", DECISION_KEEP_QUARANTINE


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Autotrain Quarantine Candidate Evaluation V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Candidate count: `{report.get('candidate_count')}`",
        f"- Eligible for manual review: `{report.get('eligible_candidate_count')}`",
        f"- Observed microbatch rows: `{report.get('observed_microbatch_rows')}`",
        f"- Minimum microbatch rows: `{report.get('min_microbatch_rows')}`",
        f"- Artifact integrity: `{report.get('artifact_integrity_status')}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = report.get("blockers") or []
    lines.extend(f"- `{blocker}`" for blocker in blockers)
    if not blockers:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "This evaluator is research-only. It does not train, promote, update runtime, write active registries, alter Freqtrade/RiskManager, access private exchange APIs, or send orders.",
            "",
        ]
    )
    return "\n".join(lines)


def safety_flags(*, write_requested: bool, write_performed: bool) -> dict[str, bool]:
    read_only = not write_requested
    return {
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "quarantine_only": True,
        "read_only": read_only,
        "write_requested": bool(write_requested),
        "write_performed": bool(write_performed),
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "trains_model": False,
        "runs_training": False,
        "promotes_model": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "active_registry_changed": False,
        "writes_active_registry": False,
        "writes_active_model_artifact": False,
        "writes_quarantine_registry": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_thresholds": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "updates_freqtrade": False,
        "updates_freqtrade_config": False,
        "updates_freqtrade_strategy": False,
        "updates_risk_manager": False,
        "writes_signal_file": False,
        "writes_active_freqtrade_signals": False,
        "active_signal_file_written": False,
        "paper_selector_runtime_enabled": False,
        "scheduler_registered": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "starts_service": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }


def build_paths(
    root: Path,
    output_json_path: str | Path | None,
    output_markdown_path: str | Path | None,
) -> EvaluationPaths:
    return EvaluationPaths(
        registry=root / DEFAULT_REGISTRY_PATH,
        model_dir=root / DEFAULT_MODEL_DIR,
        research_dir=root / DEFAULT_RESEARCH_DIR,
        activation_report=root / DEFAULT_ACTIVATION_REPORT,
        drift_report=root / DEFAULT_DRIFT_REPORT,
        execution_cost_report=root / DEFAULT_EXECUTION_COST_REPORT,
        monte_carlo_report=root / DEFAULT_MONTE_CARLO_REPORT,
        readiness_report=root / DEFAULT_READINESS_REPORT,
        output_json=resolve_path(root, output_json_path, DEFAULT_OUTPUT_JSON),
        output_markdown=resolve_path(root, output_markdown_path, DEFAULT_OUTPUT_MARKDOWN),
    )


def validate_report_write_paths(root: Path, paths: EvaluationPaths) -> list[str]:
    errors: list[str] = []
    for path in (paths.output_json, paths.output_markdown):
        try:
            path.resolve().relative_to((root / ALLOWED_REPORT_ROOT).resolve())
        except ValueError:
            errors.append("write_path_outside_allowed_reports")
    return sorted_unique(errors)


def resolve_candidate_artifact_path(
    root: Path,
    paths: EvaluationPaths,
    candidate: Mapping[str, Any],
    candidate_id: str,
    backend_id: str,
) -> Path:
    explicit = candidate.get("artifact_path") or candidate.get("model_artifact_path")
    if explicit:
        return resolve_path(root, explicit, Path(str(explicit)))
    run_id = candidate_run_id(candidate) or strip_backend_prefix(candidate_id, backend_id)
    return paths.model_dir / run_id / f"{backend_id}_candidate_model.json"


def candidate_run_id(candidate: Mapping[str, Any]) -> str:
    run_id = str(candidate.get("run_id") or "")
    if run_id:
        return run_id
    candidate_id = str(candidate.get("candidate_id") or "")
    backend_id = str(candidate.get("backend_id") or infer_backend_id(candidate_id))
    return strip_backend_prefix(candidate_id, backend_id)


def strip_backend_prefix(candidate_id: str, backend_id: str) -> str:
    prefix = f"{backend_id}_"
    return candidate_id[len(prefix) :] if candidate_id.startswith(prefix) else candidate_id


def infer_backend_id(candidate_id: str) -> str:
    if candidate_id.startswith("qlib_"):
        return "qlib"
    if candidate_id.startswith("ai_shadow_"):
        return "ai_shadow"
    return ""


def validate_artifact_hash(candidate: Mapping[str, Any], actual_hash: str) -> bool:
    expected = candidate.get("artifact_hash") or candidate.get("model_artifact_hash") or candidate.get("sha256")
    if expected is None:
        return True
    return str(expected).strip().lower() == actual_hash.lower()


def extract_registry_candidates(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return []
    return [dict(candidate) for candidate in candidates if isinstance(candidate, Mapping)]


def load_json(path: Path) -> tuple[dict[str, Any] | None, str]:
    if not path.exists():
        return None, "missing"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"invalid_json:{exc.__class__.__name__}"
    if not isinstance(loaded, dict):
        return None, "invalid_json_root"
    return loaded, "ok"


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_class_balance(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): int_safe(item) for key, item in value.items()}


def normalize_status(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    if text in {"ok", "warning", "blocked", "missing", "invalid"}:
        return text
    return "unknown" if not text else text


def resolve_path(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def sorted_unique(values: Sequence[Any]) -> list[str]:
    return sorted({str(value) for value in values if str(value).strip()})


def int_safe(value: Any) -> int:
    try:
        if pd.isna(value):
            return 0
    except (TypeError, ValueError):
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def float_safe(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value
