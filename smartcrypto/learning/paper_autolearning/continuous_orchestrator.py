"""Idempotent Paper auto-learning cycle orchestration.

This module connects the authoritative closed-trade feedback loop to quarantine
training and candidate evaluation without granting any operational authority.
It is deliberately safe to invoke repeatedly: the upstream outcome dedupe and
downstream incremental watermark remain the source of truth for incremental
processing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from smartcrypto.learning.paper_autotrain_daily_quarantine_activation import (
    build_paper_autotrain_daily_quarantine_activation_v1,
)
from smartcrypto.learning.paper_autotrain_quarantine_candidate_evaluation import (
    build_paper_autotrain_quarantine_candidate_evaluation_v1,
)

from .live_feedback_loop import run_paper_autolearning_live_feedback_loop_v1

SCHEMA_VERSION = "paper_autolearning_continuous_orchestrator_v1"
DECISION_RESEARCH_ONLY = "MANTER_EM_PAPER_RESEARCH"

LiveFeedbackRunner = Callable[..., dict[str, Any]]
QuarantineRunner = Callable[..., dict[str, Any]]
CandidateEvaluator = Callable[..., dict[str, Any]]
MicrobatchLoader = Callable[[Path], pd.DataFrame]

UNSAFE_TRUE_FIELDS: tuple[str, ...] = (
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "sends_orders",
    "exchange_private_access",
    "changes_risk",
    "writes_runtime",
    "writes_sqlite",
    "model_promotion_performed",
    "active_model_changed",
    "runtime_updated",
    "active_registry_changed",
    "active_signal_file_written",
    "paper_selector_runtime_enabled",
)


def run_paper_autolearning_continuous_orchestrator_v1(
    *,
    project_root: str | Path,
    explicit_paper_db_path: str | Path | None = None,
    write_feedback: bool = False,
    train_challenger: bool = False,
    write_quarantine_artifacts: bool = False,
    write_reports: bool = False,
    evaluate_candidates: bool = True,
    live_feedback_runner: LiveFeedbackRunner = run_paper_autolearning_live_feedback_loop_v1,
    quarantine_runner: QuarantineRunner = build_paper_autotrain_daily_quarantine_activation_v1,
    candidate_evaluator: CandidateEvaluator = build_paper_autotrain_quarantine_candidate_evaluation_v1,
    microbatch_loader: MicrobatchLoader = pd.read_parquet,
) -> dict[str, Any]:
    """Run one idempotent Paper learning cycle.

    The default invocation is a full dry-run. Downstream quarantine processing
    only starts after canonical feedback/microbatch materialization is explicitly
    enabled. Training remains quarantine-only and candidate evaluation remains
    research-only; model promotion and runtime authority are always disabled.
    """

    root = Path(project_root).resolve()
    feedback = live_feedback_runner(
        project_root=root,
        explicit_paper_db_path=explicit_paper_db_path,
        write=write_feedback,
    )
    unsafe = _unsafe_findings(feedback, stage="live_feedback")
    if unsafe:
        return _report(
            status="blocked",
            reason="unsafe_live_feedback_contract",
            feedback=feedback,
            quarantine={},
            evaluation={},
            blockers=unsafe,
            write_feedback=write_feedback,
            train_challenger=train_challenger,
            write_quarantine_artifacts=write_quarantine_artifacts,
            write_reports=write_reports,
        )
    if feedback.get("status") != "ok":
        return _report(
            status="blocked",
            reason=str(feedback.get("reason") or "live_feedback_blocked"),
            feedback=feedback,
            quarantine={},
            evaluation={},
            blockers=["live_feedback_not_ready"],
            write_feedback=write_feedback,
            train_challenger=train_challenger,
            write_quarantine_artifacts=write_quarantine_artifacts,
            write_reports=write_reports,
        )

    new_outcomes = int(feedback.get("new_outcome_event_count") or 0)
    microbatch_rows = int(feedback.get("microbatch_rows") or 0)
    if new_outcomes == 0 or microbatch_rows == 0:
        return _report(
            status="ok",
            reason="no_new_training_evidence",
            feedback=feedback,
            quarantine={},
            evaluation={},
            blockers=[],
            write_feedback=write_feedback,
            train_challenger=train_challenger,
            write_quarantine_artifacts=write_quarantine_artifacts,
            write_reports=write_reports,
        )

    if not write_feedback:
        return _report(
            status="ok",
            reason="dry_run_new_training_evidence_detected",
            feedback=feedback,
            quarantine={},
            evaluation={},
            blockers=[],
            write_feedback=False,
            train_challenger=train_challenger,
            write_quarantine_artifacts=write_quarantine_artifacts,
            write_reports=write_reports,
        )

    microbatch_path = _resolve_microbatch_path(root, feedback.get("microbatch_output_path"))
    if microbatch_path is None or not microbatch_path.is_file():
        return _report(
            status="blocked",
            reason="materialized_microbatch_missing",
            feedback=feedback,
            quarantine={},
            evaluation={},
            blockers=["materialized_microbatch_missing"],
            write_feedback=write_feedback,
            train_challenger=train_challenger,
            write_quarantine_artifacts=write_quarantine_artifacts,
            write_reports=write_reports,
        )

    try:
        raw_microbatch = microbatch_loader(microbatch_path)
    except (OSError, ValueError, TypeError) as exc:
        return _report(
            status="blocked",
            reason="materialized_microbatch_unreadable",
            feedback=feedback,
            quarantine={},
            evaluation={},
            blockers=[f"materialized_microbatch_unreadable:{type(exc).__name__}"],
            write_feedback=write_feedback,
            train_challenger=train_challenger,
            write_quarantine_artifacts=write_quarantine_artifacts,
            write_reports=write_reports,
        )

    quarantine_microbatch, bridge_errors = build_quarantine_microbatch(raw_microbatch)
    if bridge_errors or quarantine_microbatch.empty:
        return _report(
            status="blocked",
            reason="quarantine_microbatch_bridge_blocked",
            feedback=feedback,
            quarantine={
                "bridge_status": "blocked",
                "bridge_errors": bridge_errors,
                "source_rows": int(len(raw_microbatch)),
                "bridged_rows": int(len(quarantine_microbatch)),
            },
            evaluation={},
            blockers=bridge_errors or ["empty_quarantine_microbatch"],
            write_feedback=write_feedback,
            train_challenger=train_challenger,
            write_quarantine_artifacts=write_quarantine_artifacts,
            write_reports=write_reports,
        )

    quarantine = quarantine_runner(
        project_root=root,
        once=True,
        write_feedback=False,
        train_challenger=train_challenger,
        write_quarantine_artifacts=write_quarantine_artifacts,
        write_report=write_reports,
        dry_run=False,
        scheduler_check=False,
        fail_on_operational_write=True,
        microbatch_frame=quarantine_microbatch,
    )
    quarantine = {
        **quarantine,
        "bridge_status": "ok",
        "bridge_errors": [],
        "bridge_source_rows": int(len(raw_microbatch)),
        "bridge_rows": int(len(quarantine_microbatch)),
    }
    unsafe = _unsafe_findings(quarantine, stage="quarantine")
    if unsafe:
        return _report(
            status="blocked",
            reason="unsafe_quarantine_contract",
            feedback=feedback,
            quarantine=quarantine,
            evaluation={},
            blockers=unsafe,
            write_feedback=write_feedback,
            train_challenger=train_challenger,
            write_quarantine_artifacts=write_quarantine_artifacts,
            write_reports=write_reports,
        )

    evaluation: dict[str, Any] = {}
    should_evaluate = bool(
        evaluate_candidates
        and train_challenger
        and write_quarantine_artifacts
        and int(quarantine.get("quarantine_candidate_count") or 0) > 0
    )
    if should_evaluate:
        evaluation = candidate_evaluator(
            project_root=root,
            write_report=write_reports,
            fail_on_operational_write=True,
        )
        unsafe = _unsafe_findings(evaluation, stage="candidate_evaluation")
        if unsafe:
            return _report(
                status="blocked",
                reason="unsafe_candidate_evaluation_contract",
                feedback=feedback,
                quarantine=quarantine,
                evaluation=evaluation,
                blockers=unsafe,
                write_feedback=write_feedback,
                train_challenger=train_challenger,
                write_quarantine_artifacts=write_quarantine_artifacts,
                write_reports=write_reports,
            )

    status = "ok" if quarantine.get("status") in {"ok", "warning"} else "blocked"
    reason = _final_reason(quarantine, evaluation, should_evaluate)
    blockers = list(quarantine.get("blockers") or []) if status == "blocked" else []
    return _report(
        status=status,
        reason=reason,
        feedback=feedback,
        quarantine=quarantine,
        evaluation=evaluation,
        blockers=blockers,
        write_feedback=write_feedback,
        train_challenger=train_challenger,
        write_quarantine_artifacts=write_quarantine_artifacts,
        write_reports=write_reports,
    )


def build_quarantine_microbatch(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Bridge canonical AutoLearning labels into the quarantine trainer contract."""

    if frame.empty:
        return pd.DataFrame(), ["empty_source_microbatch"]
    output = frame.copy()
    lookahead = sorted(column for column in output.columns if str(column).startswith("future_ret_"))
    if lookahead:
        return pd.DataFrame(), [f"lookahead_columns_detected:{','.join(lookahead)}"]

    feature_columns = [column for column in output.columns if str(column).startswith("feature_")]
    if not feature_columns:
        return pd.DataFrame(), ["missing_feature_columns"]

    if "target_profitable" not in output.columns:
        if "label_sign" not in output.columns:
            return pd.DataFrame(), ["missing_target_source:label_sign"]
        label_sign = pd.to_numeric(output["label_sign"], errors="coerce")
        output["target_profitable"] = label_sign.map(_target_from_label_sign)

    output["target_profitable"] = pd.to_numeric(output["target_profitable"], errors="coerce")
    output = output.loc[output["target_profitable"].isin([0, 1])].copy()
    if output.empty:
        return pd.DataFrame(), ["no_valid_binary_targets"]
    return output.reset_index(drop=True), []


def _target_from_label_sign(value: Any) -> int | None:
    if pd.isna(value):
        return None
    return 1 if float(value) > 0 else 0


def _resolve_microbatch_path(root: Path, value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _unsafe_findings(report: Mapping[str, Any], *, stage: str) -> list[str]:
    findings = [f"{stage}:{field}" for field in UNSAFE_TRUE_FIELDS if report.get(field) is True]
    safety = report.get("safety_flags")
    if isinstance(safety, Mapping):
        findings.extend(
            f"{stage}:safety_flags.{field}"
            for field in UNSAFE_TRUE_FIELDS
            if safety.get(field) is True
        )
    return sorted(set(findings))


def _final_reason(quarantine: Mapping[str, Any], evaluation: Mapping[str, Any], evaluated: bool) -> str:
    if quarantine.get("status") not in {"ok", "warning"}:
        return str(quarantine.get("reason") or "quarantine_stage_blocked")
    if evaluated:
        return str(evaluation.get("reason") or "quarantine_candidate_evaluated")
    if quarantine.get("training_prevented_by_watermark") is True:
        return "no_new_incremental_records_after_watermark"
    if quarantine.get("train_challenger_requested") is True:
        return "quarantine_training_cycle_completed"
    return "feedback_to_quarantine_cycle_completed"


def _report(
    *,
    status: str,
    reason: str,
    feedback: Mapping[str, Any],
    quarantine: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    blockers: list[str],
    write_feedback: bool,
    train_challenger: bool,
    write_quarantine_artifacts: bool,
    write_reports: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH_ONLY,
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "qlib_operational_authority": False,
        "ai_shadow_operational_authority": False,
        "treatment_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "write_feedback_requested": bool(write_feedback),
        "train_challenger_requested": bool(train_challenger),
        "write_quarantine_artifacts_requested": bool(write_quarantine_artifacts),
        "write_reports_requested": bool(write_reports),
        "new_outcome_event_count": int(feedback.get("new_outcome_event_count") or 0),
        "microbatch_rows": int(feedback.get("microbatch_rows") or 0),
        "quarantine_candidate_count": int(quarantine.get("quarantine_candidate_count") or 0),
        "candidate_evaluation_status": evaluation.get("status"),
        "candidate_evaluation_decision": evaluation.get("decision"),
        "blockers": sorted(set(blockers)),
        "feedback_stage": dict(feedback),
        "quarantine_stage": dict(quarantine),
        "candidate_evaluation_stage": dict(evaluation),
    }
