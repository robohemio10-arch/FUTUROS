"""Institutional B04 quantitative validation and Strategy Factory pipeline.

The pipeline is no-write by default and has no operational authority.  It only
accepts candidate outcomes that already carry B03 execution/cost evidence.  The
built-in fixture exists solely to certify the protocol implementation and is
always marked non-authoritative.
"""

from __future__ import annotations

import json
import math
import platform
import shutil
# Local Git metadata probe only.
import subprocess  # nosec B404
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.stats import ttest_1samp

from .contracts import (
    B03_EXECUTION_ENGINE_VERSION,
    SAFETY_FLAGS,
    AcceptanceGates,
    CandidateDecision,
    DatasetAuthority,
    RobustnessContract,
    SplitMode,
    StepEvidence,
    StepStatus,
    StrategyCandidate,
    TemporalSplitContract,
    ValidationProtocol,
    json_safe,
    stable_hash,
    validate_step_coverage,
)
from .data_quality import validate_dataset
from .factory import (
    DEFAULT_FAMILIES,
    candidate_registry_records,
    generate_candidates,
    parameter_space_hash,
)
from .metrics import aggregate_fold_metrics, compute_trade_metrics, segment_metrics
from .reporting import render_markdown
from .robustness import (
    adjust_pvalues,
    cpcv_probability_of_backtest_overfitting,
    deflated_sharpe_ratio,
    run_monte_carlo_suite,
    white_reality_check,
)
from .splits import build_temporal_splits
from .stability import analyze_parameter_stability

DEFAULT_CONFIG = Path("config/quant_validation_strategy_factory_v2.json")
DEFAULT_JSON = Path("data/reports/quant_validation_strategy_factory_v2.json")
DEFAULT_MARKDOWN = Path("data/reports/quant_validation_strategy_factory_v2.md")


class QuantValidationError(ValueError):
    """Raised for invalid B04 input or configuration."""


def build_quant_validation_strategy_factory_report(
    *,
    project_root: str | Path,
    input_path: str | Path | None = None,
    config_path: str | Path | None = None,
    candidate_family: str | None = None,
    candidate_id: str | None = None,
    seed: int | None = None,
    write_report: bool = False,
    output_json: str | Path = DEFAULT_JSON,
    output_markdown: str | Path = DEFAULT_MARKDOWN,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic B04 report without operational side effects."""

    root = Path(project_root).resolve()
    config = load_config(root, config_path)
    protocol = protocol_from_config(config, seed=seed)
    protocol_errors = protocol.validate()
    protocol_errors.extend(validate_methodology_contract(config))
    if protocol_errors:
        return _blocked_report(
            root=root,
            protocol=protocol,
            reason="invalid_validation_protocol",
            blockers=protocol_errors,
            write_report=write_report,
            output_json=output_json,
            output_markdown=output_markdown,
            generated_at_utc=generated_at_utc,
        )

    candidates = generate_candidates(
        _family_spaces(config),
        strategy_version=str(config.get("strategy_version", "v2")),
        include_families=(candidate_family,) if candidate_family else None,
    )
    if candidate_id:
        candidates = tuple(item for item in candidates if item.candidate_id == candidate_id)
    if not candidates:
        return _blocked_report(
            root=root,
            protocol=protocol,
            reason="no_candidates_selected",
            blockers=("no_candidates_selected",),
            write_report=write_report,
            output_json=output_json,
            output_markdown=output_markdown,
            generated_at_utc=generated_at_utc,
        )

    input_mode = "synthetic_fixture" if input_path is None else "external_candidate_evidence"
    if input_path is None:
        source_frame = build_synthetic_candidate_fixture(candidates, protocol=protocol)
        dataset_authority = DatasetAuthority.SYNTHETIC_FIXTURE
        source_path = None
    else:
        source_path = _resolve_input(root, input_path)
        source_frame = read_frame(source_path)
        dataset_authority = _dataset_authority(config, source_frame)

    source_frame = source_frame.copy(deep=True)
    if "candidate_id" not in source_frame.columns:
        if len(candidates) == 1:
            source_frame["candidate_id"] = candidates[0].candidate_id
            source_frame["candidate_family"] = candidates[0].candidate_family
        else:
            return _blocked_report(
                root=root,
                protocol=protocol,
                reason="candidate_id_column_required_for_multiple_candidates",
                blockers=("candidate_id_column_required_for_multiple_candidates",),
                write_report=write_report,
                output_json=output_json,
                output_markdown=output_markdown,
                generated_at_utc=generated_at_utc,
            )

    feature_columns = tuple(str(item) for item in config.get("feature_columns", ()))
    feature_availability = {
        str(key): str(value)
        for key, value in dict(config.get("feature_availability", {})).items()
    }
    dataset_hash = frame_hash(source_frame)
    cost_model_hashes = sorted(
        set(source_frame.get("cost_model_hash", pd.Series(dtype=str)).dropna().astype(str))
    )
    cost_model_hash = (
        cost_model_hashes[0]
        if len(cost_model_hashes) == 1
        else stable_hash(cost_model_hashes)
    )
    execution_config_hashes = sorted(
        set(
            source_frame.get("execution_config_hash", pd.Series(dtype=str))
            .dropna()
            .astype(str)
        )
    )
    execution_config_hash = (
        execution_config_hashes[0]
        if len(execution_config_hashes) == 1
        else stable_hash(execution_config_hashes)
    )
    current_parameter_space_hash = parameter_space_hash(_family_spaces(config))
    git_state = read_git_state(root)
    report_time = generated_at_utc or datetime.now(tz=UTC).isoformat()

    preliminary: dict[str, dict[str, Any]] = {}
    candidate_pvalues: dict[str, float] = {}
    fold_scores: dict[str, list[float]] = {}

    for candidate in candidates:
        candidate_frame = source_frame.loc[
            source_frame["candidate_id"].astype(str) == candidate.candidate_id
        ].copy()
        evaluation = evaluate_candidate_preliminary(
            candidate=candidate,
            frame=candidate_frame,
            dataset_authority=dataset_authority,
            feature_columns=feature_columns,
            feature_availability=feature_availability,
            protocol=protocol,
        )
        preliminary[candidate.candidate_id] = evaluation
        returns = np.asarray(evaluation["oos_returns"], dtype=float)
        fold_scores[candidate.candidate_id] = [
            float(item["metrics"].get("expectancy", 0.0))
            for item in evaluation["walk_forward"]["folds"]
        ]
        candidate_pvalues[candidate.candidate_id] = _one_sided_positive_mean_pvalue(returns)

    aligned_returns = align_candidate_returns(source_frame, candidates)
    aligned_frame = aligned_reference_frame(source_frame, candidates)
    pbo = cpcv_probability_of_backtest_overfitting(
        aligned_returns,
        group_count=protocol.robustness.cpcv_group_count,
        test_group_count=protocol.robustness.cpcv_test_group_count,
        frame=aligned_frame if not aligned_frame.empty else None,
        split_contract=protocol.split if not aligned_frame.empty else None,
    )
    benchmark_id = next(
        (item.candidate_id for item in candidates if item.baseline_control),
        None,
    )
    benchmark_returns = (
        aligned_returns.get(benchmark_id, np.zeros(_aligned_length(aligned_returns), dtype=float))
        if benchmark_id
        else np.zeros(_aligned_length(aligned_returns), dtype=float)
    )
    reality_check = white_reality_check(
        aligned_returns,
        benchmark_returns,
        simulations=protocol.robustness.monte_carlo_simulations,
        block_size=protocol.robustness.block_bootstrap_size,
        seed=protocol.robustness.seed,
    )
    adjusted = adjust_pvalues(candidate_pvalues) if candidate_pvalues else {}
    candidate_scores = {
        candidate_id_: float(result["oos_metrics"].get("expectancy", 0.0))
        for candidate_id_, result in preliminary.items()
    }
    stability = analyze_parameter_stability(
        candidates,
        candidate_scores=candidate_scores,
        fold_scores=fold_scores,
    )

    scorecards: list[dict[str, Any]] = []
    decisions: dict[str, str] = {}
    blockers_by_candidate: dict[str, Sequence[str]] = {}
    for candidate in candidates:
        result = preliminary[candidate.candidate_id]
        candidate_stability = stability["candidate_stability"].get(candidate.candidate_id, {})
        dsr = deflated_sharpe_ratio(
            result["oos_returns"],
            trial_count=len(candidates),
            annualization_factor=protocol.robustness.annualization_factor,
        )
        decision, blockers = decide_candidate(
            candidate=candidate,
            preliminary=result,
            pbo=pbo,
            reality_check=reality_check,
            dsr=dsr,
            multiple_testing=adjusted.get(candidate.candidate_id, {}),
            stability=candidate_stability,
            protocol=protocol,
        )
        decisions[candidate.candidate_id] = decision.value
        blockers_by_candidate[candidate.candidate_id] = blockers
        scorecards.append(
            build_scorecard(
                candidate=candidate,
                preliminary=result,
                protocol=protocol,
                dataset_hash=dataset_hash,
                cost_model_hash=cost_model_hash,
                execution_config_hash=execution_config_hash,
                parameter_space_hash_value=current_parameter_space_hash,
                total_trials=len(candidates),
                pbo=pbo,
                reality_check=reality_check,
                dsr=dsr,
                multiple_testing=adjusted.get(candidate.candidate_id, {}),
                stability=candidate_stability,
                decision=decision,
                blockers=blockers,
                authoritative_result=dataset_authority.can_produce_authoritative_research,
            )
        )

    combined_split_hash = _combined_split_hash(preliminary)
    registry = candidate_registry_records(
        candidates,
        protocol_hash=protocol.protocol_hash,
        dataset_hash=dataset_hash,
        split_hash=combined_split_hash,
        cost_model_hash=cost_model_hash,
        commit_sha=git_state["commit_sha"],
        decisions=decisions,
        blockers=blockers_by_candidate,
        created_at_utc=report_time,
    )
    research_challengers = [
        item["candidate_id"]
        for item in scorecards
        if item["final_decision"] == CandidateDecision.RESEARCH_CHALLENGER.value
    ]
    rejected = [
        item["candidate_id"]
        for item in scorecards
        if item["final_decision"].startswith("REJECTED_")
    ]
    material_negative = sorted(
        {
            segment
            for item in scorecards
            for segment in item["material_negative_segments"]
        }
    )
    canonical_payload = {
        "schema_version": "quant_validation_strategy_factory_report_v2",
        "protocol": protocol.to_dict(),
        "protocol_hash": protocol.protocol_hash,
        "dataset_hash": dataset_hash,
        "dataset_authority": dataset_authority.value,
        "input_mode": input_mode,
        "parameter_space_hash": current_parameter_space_hash,
        "cost_model_hash": cost_model_hash,
        "execution_config_hash": execution_config_hash,
        "candidate_scorecards": scorecards,
        "cpcv_pbo": pbo,
        "white_reality_check": reality_check,
        "candidate_registry_evaluation_hashes": [
            item["evaluation_hash"] for item in registry
        ],
        "safety_flags": dict(SAFETY_FLAGS),
    }
    evaluation_hash = stable_hash(canonical_payload)
    execution_manifest, manifest_object = build_b02_execution_manifest(
        root=root,
        generated_at_utc=report_time,
        evaluation_hash=evaluation_hash,
        protocol=protocol,
        dataset_hash=dataset_hash,
        dataset_authority=dataset_authority,
        row_count=len(source_frame),
        split_hash=combined_split_hash,
        cost_model_hash=cost_model_hash,
        execution_config_hash=execution_config_hash,
        parameter_space_hash_value=current_parameter_space_hash,
        git_state=git_state,
        input_mode=input_mode,
        material_negative_segments=material_negative,
    )
    report: dict[str, Any] = {
        "status": "ok",
        "reason": "quant_validation_strategy_factory_completed_research_only",
        "schema_version": "quant_validation_strategy_factory_report_v2",
        "generated_at_utc": report_time,
        "input_mode": input_mode,
        "input_path": str(source_path) if source_path is not None else None,
        "fixture_only": input_mode == "synthetic_fixture",
        "authoritative_result": dataset_authority.can_produce_authoritative_research,
        "dataset_authority": dataset_authority.value,
        "dataset_hash": dataset_hash,
        "row_count": int(len(source_frame)),
        "protocol": protocol.to_dict(),
        "protocol_hash": protocol.protocol_hash,
        "parameter_space_hash": current_parameter_space_hash,
        "cost_model_hash": cost_model_hash,
        "execution_config_hash": execution_config_hash,
        "execution_engine_version": B03_EXECUTION_ENGINE_VERSION,
        "execution_manifest": execution_manifest,
        "execution_manifest_content_hash": execution_manifest.get("content_hash"),
        "candidate_count": len(scorecards),
        "research_challengers": research_challengers,
        "research_challenger_count": len(research_challengers),
        "rejected_candidates": rejected,
        "rejected_candidate_count": len(rejected),
        "material_negative_segments": material_negative,
        "candidate_scorecards": scorecards,
        "cpcv_pbo": pbo,
        "white_reality_check": reality_check,
        "multiple_testing": adjusted,
        "parameter_stability": stability,
        "candidate_registry": list(registry),
        "candidate_registry_mode": "research_only_content_addressed",
        "gate_b04": "PASS",
        "release_gate": "BLOCKED_RESEARCH_ONLY",
        "promotion_allowed": False,
        "operational_authority": False,
        "write_requested": bool(write_report),
        "write_performed": False,
        "manifest_write_performed": False,
        "manifest_output_root": "data/reports/quant_validation_strategy_factory_v2/manifests",
        "output_json": _display_path(root, output_json),
        "output_markdown": _display_path(root, output_markdown),
        "git_state": git_state,
        "result_hash": evaluation_hash,
        "safety_flags": dict(SAFETY_FLAGS),
        **dict(SAFETY_FLAGS),
    }
    if write_report:
        _write_report(
            root=root,
            report=report,
            output_json=output_json,
            output_markdown=output_markdown,
            manifest_object=manifest_object,
        )
        report["write_performed"] = True
        report["manifest_write_performed"] = manifest_object is not None
    return json_safe(report)


def evaluate_candidate_preliminary(
    *,
    candidate: StrategyCandidate,
    frame: pd.DataFrame,
    dataset_authority: DatasetAuthority,
    feature_columns: Sequence[str],
    feature_availability: Mapping[str, str],
    protocol: ValidationProtocol,
) -> dict[str, Any]:
    quality = validate_dataset(
        frame,
        dataset_authority=dataset_authority,
        feature_columns=feature_columns,
        feature_availability=feature_availability,
    )
    split_result = build_temporal_splits(quality.frame, protocol.split)
    execution = _execution_evidence(quality.frame)
    cost = _cost_evidence(quality.frame)
    fold_rows: list[dict[str, Any]] = []
    oos_indices: list[int] = []
    if split_result.folds and quality.evidence.status is StepStatus.PASS and quality.leakage_evidence.status is StepStatus.PASS:
        for fold in split_result.folds:
            train_metrics = compute_trade_metrics(quality.frame.iloc[list(fold.train_indices)])
            validation_metrics = compute_trade_metrics(quality.frame.iloc[list(fold.validation_indices)])
            test_metrics = compute_trade_metrics(quality.frame.iloc[list(fold.test_indices)])
            oos_indices.extend(fold.test_indices)
            fold_rows.append(
                {
                    "fold_id": fold.fold_id,
                    "fold_hash": fold.fold_hash,
                    "train_metrics": train_metrics,
                    "validation_metrics": validation_metrics,
                    "metrics": test_metrics,
                    "is_to_oos_expectancy_degradation": (
                        float(train_metrics["expectancy"]) - float(test_metrics["expectancy"])
                    ),
                }
            )
    oos_indices = sorted(set(oos_indices))
    oos_frame = quality.frame.iloc[oos_indices].copy() if oos_indices else quality.frame.iloc[0:0].copy()
    oos_metrics = compute_trade_metrics(oos_frame, annualization_factor=protocol.robustness.annualization_factor)
    segments = segment_metrics(oos_frame, gates=protocol.gates)
    walk_forward_aggregate: dict[str, Any] = aggregate_fold_metrics(fold_rows)
    walk_forward: dict[str, Any] = {
        "folds": fold_rows,
        "aggregate": walk_forward_aggregate,
        "oos_metrics": oos_metrics,
    }
    walk_status = (
        StepStatus.PASS
        if fold_rows and all(item["metrics"]["trade_count"] >= protocol.gates.minimum_trades_per_fold for item in fold_rows)
        else StepStatus.BLOCKED_INSUFFICIENT_SAMPLE
    )
    walk_evidence = StepEvidence(
        step="walk_forward",
        status=walk_status,
        reason="walk_forward_ok" if walk_status is StepStatus.PASS else "minimum_trades_per_fold_not_met",
        metrics={
            "fold_count": len(fold_rows),
            "oos_trade_count": oos_metrics["trade_count"],
            "worst_fold": walk_forward_aggregate.get("worst_fold"),
        },
        blockers=() if walk_status is StepStatus.PASS else ("minimum_trades_per_fold_not_met",),
    )
    monte_carlo = run_monte_carlo_suite(
        oos_frame.get("net_pnl", pd.Series(dtype=float)).tolist(),
        contract=protocol.robustness,
        initial_capital=float(max(1.0, abs(oos_metrics.get("gross_pnl", 0.0)) + 100.0)),
        cost_stress_per_trade=float(oos_frame.get("total_cost", pd.Series([0.0])).mean()) if not oos_frame.empty else 0.0,
    )
    mc_evidence = StepEvidence(
        step="monte_carlo",
        status=StepStatus.PASS if monte_carlo.get("status") == "ok" else StepStatus.BLOCKED_INSUFFICIENT_SAMPLE,
        reason=str(monte_carlo.get("reason")),
        metrics={
            "risk_of_ruin": monte_carlo.get("risk_of_ruin"),
            "worst_method": monte_carlo.get("worst_method"),
        },
        blockers=() if monte_carlo.get("status") == "ok" else (str(monte_carlo.get("reason")),),
    )
    stability_placeholder = StepEvidence(
        step="parameter_stability",
        status=StepStatus.PASS,
        reason="parameter_stability_deferred_to_factory_surface",
    )
    cpcv_placeholder = StepEvidence(step="cpcv_pbo", status=StepStatus.PASS, reason="cpcv_pbo_deferred_to_factory")
    multiple_placeholder = StepEvidence(step="multiple_testing", status=StepStatus.PASS, reason="multiple_testing_deferred_to_factory")
    segment_evidence = StepEvidence(
        step="oos_segments",
        status=StepStatus.BLOCKED if segments["material_negative_segments"] else StepStatus.PASS,
        reason="material_negative_segment_detected" if segments["material_negative_segments"] else "oos_segments_ok",
        metrics={"material_negative_segment_count": segments["material_negative_segment_count"]},
        blockers=tuple(segments["material_negative_segments"]),
    )
    scorecard_placeholder = StepEvidence(step="scorecard", status=StepStatus.PASS, reason="scorecard_materialized")
    steps = {
        "data_quality": quality.evidence,
        "anti_leakage": quality.leakage_evidence,
        "temporal_split": split_result.evidence,
        "event_driven_execution": execution,
        "cost_reconciliation": cost,
        "walk_forward": walk_evidence,
        "cpcv_pbo": cpcv_placeholder,
        "monte_carlo": mc_evidence,
        "multiple_testing": multiple_placeholder,
        "parameter_stability": stability_placeholder,
        "oos_segments": segment_evidence,
        "scorecard": scorecard_placeholder,
    }
    coverage_errors = validate_step_coverage(protocol, steps)
    return {
        "candidate": candidate.canonical_payload(),
        "candidate_id": candidate.candidate_id,
        "total_trade_count": int(len(quality.frame)),
        "data_quality_findings": list(quality.findings),
        "steps": {key: evidence.to_dict() for key, evidence in steps.items()},
        "step_coverage_errors": coverage_errors,
        "split_hash": split_result.split_hash,
        "folds": [fold.to_dict(include_indices=False) for fold in split_result.folds],
        "walk_forward": walk_forward,
        "oos_metrics": oos_metrics,
        "oos_segments": segments,
        "monte_carlo": monte_carlo,
        "oos_returns": pd.to_numeric(oos_frame.get("net_pnl", pd.Series(dtype=float)), errors="coerce").dropna().astype(float).tolist(),
    }


def decide_candidate(
    *,
    candidate: StrategyCandidate,
    preliminary: Mapping[str, Any],
    pbo: Mapping[str, Any],
    reality_check: Mapping[str, Any],
    dsr: Mapping[str, Any],
    multiple_testing: Mapping[str, Any],
    stability: Mapping[str, Any],
    protocol: ValidationProtocol,
) -> tuple[CandidateDecision, tuple[str, ...]]:
    steps = preliminary["steps"]
    if steps["data_quality"]["status"] != StepStatus.PASS.value:
        return CandidateDecision.REJECTED_DATA_QUALITY, tuple(steps["data_quality"]["blockers"])
    if steps["anti_leakage"]["status"] != StepStatus.PASS.value:
        return CandidateDecision.REJECTED_LEAKAGE, tuple(steps["anti_leakage"]["blockers"])
    if steps["event_driven_execution"]["status"] != StepStatus.PASS.value:
        return CandidateDecision.REJECTED_DATA_QUALITY, tuple(
            steps["event_driven_execution"]["blockers"]
        )
    if steps["cost_reconciliation"]["status"] != StepStatus.PASS.value:
        return CandidateDecision.REJECTED_COST_SENSITIVITY, tuple(
            steps["cost_reconciliation"]["blockers"]
        )
    if steps["temporal_split"]["status"] != StepStatus.PASS.value:
        return CandidateDecision.REJECTED_INSUFFICIENT_SAMPLE, tuple(
            steps["temporal_split"]["blockers"]
        )
    metrics = preliminary["oos_metrics"]
    if int(preliminary.get("total_trade_count", 0)) < protocol.gates.minimum_total_trades or steps["walk_forward"]["status"] != StepStatus.PASS.value:
        return CandidateDecision.REJECTED_INSUFFICIENT_SAMPLE, ("minimum_trade_sample_not_met",)
    if candidate.baseline_control:
        return CandidateDecision.RESEARCH_BASELINE_CONTROL, ()
    if preliminary["oos_segments"]["material_negative_segments"]:
        return CandidateDecision.REJECTED_MATERIAL_NEGATIVE_SEGMENT, tuple(preliminary["oos_segments"]["material_negative_segments"])
    risk_of_ruin = preliminary["monte_carlo"].get("risk_of_ruin")
    if risk_of_ruin is None or float(risk_of_ruin) > protocol.robustness.max_risk_of_ruin:
        return CandidateDecision.REJECTED_RISK_OF_RUIN, ("risk_of_ruin_gate_failed",)
    if float(metrics.get("net_pnl", 0.0)) <= 0.0 or float(metrics.get("expectancy", 0.0)) < protocol.gates.minimum_oos_expectancy:
        return CandidateDecision.REJECTED_NEGATIVE_OOS, ("negative_or_zero_oos",)
    profit_factor = metrics.get("profit_factor")
    if profit_factor is None or float(profit_factor) < protocol.gates.minimum_oos_profit_factor:
        return CandidateDecision.REJECTED_NEGATIVE_OOS, ("oos_profit_factor_below_gate",)
    cost_drag = metrics.get("cost_drag_ratio")
    if cost_drag is None or float(cost_drag) > protocol.gates.maximum_cost_drag_ratio:
        return CandidateDecision.REJECTED_COST_SENSITIVITY, ("cost_drag_ratio_above_gate",)
    if pbo.get("status") != "ok" or float(pbo.get("pbo", 1.0)) > protocol.robustness.max_pbo:
        return CandidateDecision.REJECTED_OVERFIT, ("pbo_gate_failed",)
    if dsr.get("status") != "ok" or float(dsr.get("probability", 0.0)) < protocol.gates.minimum_deflated_sharpe_probability:
        return CandidateDecision.REJECTED_OVERFIT, ("deflated_sharpe_gate_failed",)
    if reality_check.get("status") != "ok" or float(reality_check.get("pvalue", 1.0)) > protocol.gates.maximum_white_reality_check_pvalue:
        return CandidateDecision.REJECTED_OVERFIT, ("white_reality_check_gate_failed",)
    if float(multiple_testing.get("holm", 1.0)) > protocol.robustness.significance_level:
        return CandidateDecision.REJECTED_OVERFIT, ("holm_adjusted_pvalue_gate_failed",)
    if (
        float(stability.get("parameter_stability", 0.0)) < protocol.gates.minimum_parameter_stability
        or bool(stability.get("knife_edge_optimum", True))
    ):
        return CandidateDecision.REJECTED_UNSTABLE_PARAMETERS, ("parameter_stability_gate_failed",)
    return CandidateDecision.RESEARCH_CHALLENGER, ()


def build_scorecard(
    *,
    candidate: StrategyCandidate,
    preliminary: Mapping[str, Any],
    protocol: ValidationProtocol,
    dataset_hash: str,
    cost_model_hash: str,
    execution_config_hash: str,
    parameter_space_hash_value: str,
    total_trials: int,
    pbo: Mapping[str, Any],
    reality_check: Mapping[str, Any],
    dsr: Mapping[str, Any],
    multiple_testing: Mapping[str, Any],
    stability: Mapping[str, Any],
    decision: CandidateDecision,
    blockers: Sequence[str],
    authoritative_result: bool,
) -> dict[str, Any]:
    metrics = preliminary["oos_metrics"]
    aggregate = preliminary["walk_forward"]["aggregate"]
    worst_by_dimension = preliminary["oos_segments"]["worst_by_dimension"]
    steps = {key: dict(value) for key, value in preliminary["steps"].items()}
    steps["cpcv_pbo"] = {
        "step": "cpcv_pbo",
        "status": StepStatus.PASS.value if pbo.get("status") == "ok" else StepStatus.BLOCKED.value,
        "reason": str(pbo.get("reason")),
        "metrics": {
            "pbo": pbo.get("pbo"),
            "path_count": pbo.get("path_count", 0),
            "purge_applied": pbo.get("purge_applied", False),
            "embargo_applied": pbo.get("embargo_applied", False),
        },
        "blockers": [] if pbo.get("status") == "ok" else [str(pbo.get("reason"))],
    }
    multiple_ok = dsr.get("status") == "ok" and reality_check.get("status") == "ok" and bool(multiple_testing)
    steps["multiple_testing"] = {
        "step": "multiple_testing",
        "status": StepStatus.PASS.value if multiple_ok else StepStatus.BLOCKED.value,
        "reason": "multiple_testing_completed" if multiple_ok else "multiple_testing_blocked",
        "metrics": {
            "deflated_sharpe_probability": dsr.get("probability"),
            "white_reality_check_pvalue": reality_check.get("pvalue"),
            "holm_adjusted_pvalue": multiple_testing.get("holm"),
        },
        "blockers": [] if multiple_ok else ["multiple_testing_incomplete"],
    }
    stability_ok = bool(stability) and not bool(stability.get("knife_edge_optimum", True))
    steps["parameter_stability"] = {
        "step": "parameter_stability",
        "status": StepStatus.PASS.value if stability_ok else StepStatus.BLOCKED.value,
        "reason": "parameter_stability_completed" if stability_ok else "parameter_stability_blocked",
        "metrics": dict(stability),
        "blockers": [] if stability_ok else ["parameter_stability_gate_failed"],
    }
    payload = {
        "candidate_id": candidate.candidate_id,
        "candidate_family": candidate.candidate_family,
        "candidate_version": candidate.strategy_version,
        "parameters": json_safe(candidate.parameters),
        "parameter_hash": candidate.parameter_hash,
        "protocol_version": protocol.protocol_version,
        "protocol_hash": protocol.protocol_hash,
        "dataset_hash": dataset_hash,
        "split_hash": preliminary["split_hash"],
        "execution_engine_version": protocol.execution_engine_version,
        "execution_config_hash": execution_config_hash,
        "cost_model_hash": cost_model_hash,
        "parameter_space_hash": parameter_space_hash_value,
        "total_trials": int(total_trials),
        "total_trades": int(preliminary.get("total_trade_count", metrics["trade_count"])),
        "oos_trade_count": metrics["trade_count"],
        "fold_count": aggregate.get("fold_count", 0),
        "valid_fold_count": aggregate.get("valid_fold_count", 0),
        "blocked_fold_count": aggregate.get("blocked_fold_count", 0),
        "oos_net_pnl": metrics["net_pnl"],
        "oos_expectancy": metrics["expectancy"],
        "oos_profit_factor": metrics["profit_factor"],
        "oos_sharpe": metrics["sharpe"],
        "deflated_sharpe": dsr,
        "white_reality_check": reality_check,
        "multiple_testing": dict(multiple_testing),
        "pbo": pbo.get("pbo"),
        "risk_of_ruin": preliminary["monte_carlo"].get("risk_of_ruin"),
        "maximum_drawdown": metrics["maximum_drawdown"],
        "turnover": metrics["turnover"],
        "total_costs": metrics["total_costs"],
        "cost_drag_ratio": metrics["cost_drag_ratio"],
        "worst_fold": aggregate.get("worst_fold"),
        "worst_symbol": worst_by_dimension.get("symbol"),
        "worst_side": worst_by_dimension.get("side"),
        "worst_regime": worst_by_dimension.get("regime"),
        "worst_liquidity_bucket": worst_by_dimension.get("liquidity_bucket"),
        "worst_volatility_bucket": worst_by_dimension.get("volatility_bucket"),
        "worst_funding_bucket": worst_by_dimension.get("funding_bucket"),
        "parameter_stability": dict(stability),
        "sample_sufficiency": preliminary["steps"]["walk_forward"]["status"],
        "leakage_status": preliminary["steps"]["anti_leakage"]["status"],
        "cost_reconciliation_status": preliminary["steps"]["cost_reconciliation"]["status"],
        "segment_gate_status": preliminary["oos_segments"]["segment_gate_status"],
        "robustness_gate_status": preliminary["monte_carlo"].get("status"),
        "material_negative_segments": preliminary["oos_segments"]["material_negative_segments"],
        "steps": steps,
        "final_decision": decision.value,
        "rejection_reasons": sorted(set(blockers)),
        "authoritative_result": bool(authoritative_result),
        "research_only": True,
        "operational_authority": False,
        "promotion_allowed": False,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    return {**payload, "scorecard_hash": stable_hash(payload)}


def build_synthetic_candidate_fixture(
    candidates: Sequence[StrategyCandidate],
    *,
    protocol: ValidationProtocol,
) -> pd.DataFrame:
    """Create a deterministic, sanitized non-authoritative fixture."""

    rows_per_candidate = max(
        protocol.gates.minimum_total_trades + 20,
        protocol.split.minimum_train_rows
        + protocol.split.fold_count * (protocol.split.validation_rows + protocol.split.test_rows),
    )
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    records: list[dict[str, Any]] = []
    for candidate_index, candidate in enumerate(candidates):
        quality = _fixture_candidate_quality(candidate)
        for index in range(rows_per_candidate):
            symbol = "BTCUSDT" if index % 2 == 0 else "ETHUSDT"
            side = "LONG" if index % 3 else "SHORT"
            regime = ("trend_up", "range", "trend_down", "high_volatility")[index % 4]
            cyclical = math.sin(index / 7.0) * 0.09 + math.cos(index / 11.0) * 0.05
            deterministic_noise = ((index * 37 + candidate_index * 11) % 29 - 14) / 100.0
            regime_penalty = -0.15 if regime == "trend_down" and candidate.candidate_family == "entry_threshold" else 0.0
            net_pnl = quality + cyclical + deterministic_noise + regime_penalty
            total_cost = 0.035 + (index % 4) * 0.005
            gross_pnl = net_pnl + total_cost
            open_time = base_time + timedelta(hours=index * 3)
            close_time = open_time + timedelta(minutes=12 + index % 90)
            records.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "candidate_family": candidate.candidate_family,
                    "trade_id": f"fixture_{index:05d}",
                    "observation_id": f"observation_{index:05d}",
                    "symbol": symbol,
                    "side": side,
                    "open_time_utc": open_time.isoformat(),
                    "close_time_utc": close_time.isoformat(),
                    "feature_time_utc": (open_time - timedelta(seconds=1)).isoformat(),
                    "gross_pnl": round(gross_pnl, 10),
                    "net_pnl": round(net_pnl, 10),
                    "total_cost": round(total_cost, 10),
                    "trading_fee": round(total_cost * 0.50, 10),
                    "funding_fee": round(total_cost * 0.10, 10),
                    "slippage_cost": round(total_cost * 0.20, 10),
                    "market_impact_cost": round(total_cost * 0.20, 10),
                    "turnover": 100.0 + index,
                    "liquidity_role": "maker" if index % 4 == 0 else "taker",
                    "liquidation_count": 0,
                    "exposure": 0.10,
                    "concentration": 0.50,
                    "execution_engine_version": B03_EXECUTION_ENGINE_VERSION,
                    "execution_config_hash": stable_hash(
                        {"fixture": "execution_config_v2", "seed": protocol.robustness.seed}
                    ),
                    "cost_model_hash": stable_hash({"fixture": "cost_model_v2"}),
                    "regime": regime,
                    "volatility_bucket": "high" if index % 5 == 0 else "normal",
                    "liquidity_bucket": "low" if index % 7 == 0 else "normal",
                    "funding_bucket": "positive" if index % 2 == 0 else "negative",
                    "holding_period_bucket": "under_30m" if index % 2 == 0 else "30m_to_3h",
                    "entry_score_bucket": "high" if index % 3 == 0 else "medium",
                    "cost_bucket": "high" if total_cost > 0.045 else "normal",
                    "market_impact_bucket": "low",
                    "leverage_bucket": "5x",
                    "feature_momentum": round(math.sin(index / 10.0), 10),
                }
            )
    return pd.DataFrame.from_records(records).sort_values(
        ["candidate_id", "open_time_utc"], kind="mergesort"
    ).reset_index(drop=True)


def align_candidate_returns(
    frame: pd.DataFrame,
    candidates: Sequence[StrategyCandidate],
) -> dict[str, np.ndarray]:
    identity, ordered_common, candidate_frames = _aligned_candidate_frames(frame, candidates)
    if not ordered_common:
        return {}
    aligned: dict[str, np.ndarray] = {}
    for candidate_id_, subset in candidate_frames.items():
        indexed = subset.set_index(identity)
        aligned[candidate_id_] = pd.to_numeric(
            indexed.loc[ordered_common, "net_pnl"], errors="coerce"
        ).fillna(0.0).to_numpy(dtype=float)
    return aligned


def aligned_reference_frame(
    frame: pd.DataFrame,
    candidates: Sequence[StrategyCandidate],
) -> pd.DataFrame:
    identity, ordered_common, candidate_frames = _aligned_candidate_frames(frame, candidates)
    if not ordered_common or not candidate_frames:
        return frame.iloc[0:0].copy()
    first_id = sorted(candidate_frames)[0]
    source = frame.loc[frame["candidate_id"].astype(str) == first_id].copy()
    source[identity] = source[identity].astype(str)
    source = source.drop_duplicates(identity, keep=False).set_index(identity)
    return source.loc[ordered_common].reset_index()


def _aligned_candidate_frames(
    frame: pd.DataFrame,
    candidates: Sequence[StrategyCandidate],
) -> tuple[str, list[str], dict[str, pd.DataFrame]]:
    identity = "observation_id" if "observation_id" in frame.columns else "trade_id"
    candidate_frames: dict[str, pd.DataFrame] = {}
    common: set[str] | None = None
    for candidate in candidates:
        subset = frame.loc[
            frame["candidate_id"].astype(str) == candidate.candidate_id,
            [identity, "net_pnl"],
        ].copy()
        subset[identity] = subset[identity].astype(str)
        subset = subset.drop_duplicates(identity, keep=False).sort_values(identity, kind="mergesort")
        candidate_frames[candidate.candidate_id] = subset
        identities = set(subset[identity])
        common = identities if common is None else common.intersection(identities)
    return identity, sorted(common or ()), candidate_frames


def load_config(root: Path, config_path: str | Path | None) -> dict[str, Any]:
    path = root / DEFAULT_CONFIG if config_path is None else _resolve_input(root, config_path)
    if not path.exists():
        return default_config()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise QuantValidationError("config_root_must_be_object")
    return payload


def default_config() -> dict[str, Any]:
    return {
        "schema_version": "quant_validation_strategy_factory_config_v2",
        "strategy_version": "v2",
        "dataset_authority": DatasetAuthority.PAPER_OUTCOME_RECONCILED.value,
        "feature_columns": ["feature_momentum"],
        "feature_availability": {},
        "methodology": {
            "normalization_scope": "fold_local",
            "feature_selection_scope": "fold_local",
            "threshold_calibration_scope": "validation_only",
            "regime_construction": "point_in_time"
        },
        "family_spaces": {family: {key: list(values) for key, values in space.items()} for family, space in DEFAULT_FAMILIES.items()},
        "split": {
            "mode": SplitMode.EXPANDING.value,
            "fold_count": 3,
            "validation_rows": 20,
            "test_rows": 20,
            "minimum_train_rows": 60,
            "rolling_train_rows": 120,
            "purge_seconds": 0,
            "embargo_seconds": 3600,
            "feature_lookback_seconds": 0,
            "label_horizon_seconds": 0,
        },
        "robustness": {
            "monte_carlo_simulations": 200,
            "block_bootstrap_size": 10,
            "cpcv_group_count": 5,
            "cpcv_test_group_count": 2,
            "ruin_threshold_fraction": 0.30,
            "max_risk_of_ruin": 0.05,
            "max_pbo": 0.50,
            "significance_level": 0.05,
            "annualization_factor": 365,
            "seed": 42,
        },
        "gates": asdict(AcceptanceGates()),
    }


def validate_methodology_contract(config: Mapping[str, Any]) -> list[str]:
    methodology = dict(config.get("methodology", {}))
    expected = {
        "normalization_scope": "fold_local",
        "feature_selection_scope": "fold_local",
        "threshold_calibration_scope": "validation_only",
        "regime_construction": "point_in_time",
    }
    errors: list[str] = []
    for key, value in expected.items():
        if methodology.get(key) != value:
            errors.append(f"unsafe_methodology:{key}")
    return errors


def protocol_from_config(config: Mapping[str, Any], *, seed: int | None) -> ValidationProtocol:
    split_payload = dict(config.get("split", {}))
    split_payload["mode"] = SplitMode(str(split_payload.get("mode", SplitMode.EXPANDING.value)))
    robustness_payload = dict(config.get("robustness", {}))
    if seed is not None:
        robustness_payload["seed"] = int(seed)
    return ValidationProtocol(
        split=TemporalSplitContract(**split_payload),
        robustness=RobustnessContract(**robustness_payload),
        gates=AcceptanceGates(**dict(config.get("gates", {}))),
    )


def read_frame(path: Path) -> pd.DataFrame:
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise QuantValidationError("input_path_must_be_regular_file")
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise QuantValidationError("pyarrow_required_for_parquet") from exc
        parquet_file = pq.ParquetFile(path)
        try:
            table = parquet_file.read(use_threads=False)
            return table.to_pandas(use_threads=False)
        finally:
            parquet_file.close()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".json", ".jsonl"}:
        return pd.read_json(path, lines=suffix == ".jsonl")
    raise QuantValidationError(f"unsupported_input_extension:{suffix}")


def frame_hash(frame: pd.DataFrame) -> str:
    normalized = frame.copy(deep=True)
    normalized = normalized.reindex(sorted(normalized.columns), axis=1)
    records = normalized.astype(object).where(pd.notna(normalized), None).to_dict(orient="records")
    return stable_hash(records)


def read_git_state(root: Path) -> dict[str, Any]:
    commit = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current") or "unresolved"
    status = _git(root, "status", "--porcelain=v1", allow_empty=True)
    return {
        "branch": branch,
        "commit_sha": commit if len(commit) == 40 else None,
        "dirty_worktree": bool(status),
    }


def _git(root: Path, *arguments: str, allow_empty: bool = False) -> str:
    git_executable = shutil.which("git")
    if git_executable is None:
        return ""
    try:
        # Executable is resolved locally; arguments are internal constants.
        completed = subprocess.run(  # nosec B603
            [git_executable, *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    value = completed.stdout.strip()
    return value if value or allow_empty else ""


def _execution_evidence(frame: pd.DataFrame) -> StepEvidence:
    versions = set(frame.get("execution_engine_version", pd.Series(dtype=str)).dropna().astype(str))
    blockers: list[str] = []
    if not versions:
        blockers.append("missing_execution_engine_version")
    elif versions != {B03_EXECUTION_ENGINE_VERSION}:
        blockers.append("unsupported_execution_engine_version")
    return StepEvidence(
        step="event_driven_execution",
        status=StepStatus.BLOCKED if blockers else StepStatus.PASS,
        reason="b03_execution_evidence_blocked" if blockers else "b03_execution_evidence_ok",
        metrics={"execution_engine_versions": sorted(versions)},
        blockers=tuple(blockers),
    )


def _cost_evidence(frame: pd.DataFrame) -> StepEvidence:
    blockers: list[str] = []
    if "cost_model_hash" not in frame.columns or frame["cost_model_hash"].isna().any():
        blockers.append("missing_cost_model_hash")
    if {"gross_pnl", "total_cost", "net_pnl"}.issubset(frame.columns):
        residual = (
            pd.to_numeric(frame["gross_pnl"], errors="coerce")
            - pd.to_numeric(frame["total_cost"], errors="coerce")
            - pd.to_numeric(frame["net_pnl"], errors="coerce")
        ).abs()
        if bool((residual > 1e-8).fillna(True).any()):
            blockers.append("cost_reconciliation_mismatch")
    else:
        blockers.append("missing_cost_reconciliation_columns")
    return StepEvidence(
        step="cost_reconciliation",
        status=StepStatus.BLOCKED if blockers else StepStatus.PASS,
        reason="cost_reconciliation_blocked" if blockers else "cost_reconciliation_ok",
        metrics={"row_count": len(frame)},
        blockers=tuple(sorted(set(blockers))),
    )


def _one_sided_positive_mean_pvalue(returns: np.ndarray) -> float:
    clean = returns[np.isfinite(returns)]
    if clean.size < 2:
        return 1.0
    statistic, two_sided = ttest_1samp(clean, popmean=0.0, nan_policy="omit")
    if not np.isfinite(statistic) or not np.isfinite(two_sided):
        return 1.0
    return float(two_sided / 2.0 if statistic > 0 else 1.0 - two_sided / 2.0)


def _fixture_candidate_quality(candidate: StrategyCandidate) -> float:
    if candidate.baseline_control:
        return 0.01
    parameter_values = [float(value) for value in candidate.parameters.values() if isinstance(value, (int, float))]
    center_penalty = sum(abs(value - np.median(parameter_values)) for value in parameter_values) if parameter_values else 0.0
    family_base = {
        "fixed_tp_sl": 0.18,
        "atr_stop": 0.14,
        "trailing_stop": 0.11,
        "entry_threshold": 0.08,
        "holding_period": 0.06,
    }.get(candidate.candidate_family, 0.03)
    return float(family_base - 0.0005 * center_penalty)


def _family_spaces(config: Mapping[str, Any]) -> dict[str, dict[str, Sequence[Any]]]:
    raw = config.get("family_spaces", DEFAULT_FAMILIES)
    return {
        str(family): {str(key): tuple(values) for key, values in dict(space).items()}
        for family, space in dict(raw).items()
    }


def _dataset_authority(config: Mapping[str, Any], frame: pd.DataFrame) -> DatasetAuthority:
    value = str(config.get("dataset_authority", DatasetAuthority.PERMANENT_QUARANTINE.value))
    if "dataset_authority" in frame.columns:
        values = sorted(set(frame["dataset_authority"].dropna().astype(str)))
        if len(values) == 1:
            value = values[0]
        elif len(values) > 1:
            raise QuantValidationError("mixed_dataset_authority")
    return DatasetAuthority(value)


def _combined_split_hash(preliminary: Mapping[str, Mapping[str, Any]]) -> str | None:
    hashes = sorted(
        str(item["split_hash"])
        for item in preliminary.values()
        if item.get("split_hash")
    )
    return stable_hash(hashes) if hashes else None


def _aligned_length(values: Mapping[str, np.ndarray]) -> int:
    return len(next(iter(values.values()))) if values else 0


def _resolve_input(root: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise QuantValidationError("path_outside_project_root") from exc
    return resolved


def _display_path(root: Path, value: str | Path) -> str:
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def build_b02_execution_manifest(
    *,
    root: Path,
    generated_at_utc: str,
    evaluation_hash: str,
    protocol: ValidationProtocol,
    dataset_hash: str,
    dataset_authority: DatasetAuthority,
    row_count: int,
    split_hash: str | None,
    cost_model_hash: str,
    execution_config_hash: str,
    parameter_space_hash_value: str,
    git_state: Mapping[str, Any],
    input_mode: str,
    material_negative_segments: Sequence[str],
) -> tuple[dict[str, Any], Any | None]:
    dataset_manifest_hash = stable_hash(
        {
            "dataset_hash": dataset_hash,
            "dataset_authority": dataset_authority.value,
            "row_count": int(row_count),
        }
    )
    schema_hash = stable_hash(
        {
            "schema_version": "quant_validation_strategy_factory_report_v2",
            "protocol_hash": protocol.protocol_hash,
        }
    )
    config_hash = stable_hash(
        {
            "protocol_hash": protocol.protocol_hash,
            "execution_config_hash": execution_config_hash,
            "parameter_space_hash": parameter_space_hash_value,
        }
    )
    source_hashes = {
        "candidate_evidence": dataset_hash,
        "parameter_space": parameter_space_hash_value,
    }
    blockers = (
        ("fixture_only_non_authoritative",)
        if input_mode == "synthetic_fixture"
        else ()
    )
    warnings = tuple(
        ["material_negative_segments_detected"]
        if material_negative_segments
        else []
    )
    try:
        from smartcrypto.data.canonical_data_foundation_v2.manifest import (
            build_execution_manifest,
        )
    except ImportError:
        canonical = {
            "schema_version": "canonical_execution_manifest_v2_compatible",
            "execution_type": "quantitative_evaluation",
            "project": "SMART FUTUROS",
            "branch": git_state["branch"],
            "commit_sha": git_state["commit_sha"],
            "dirty_worktree": git_state["dirty_worktree"],
            "dataset_hash": dataset_hash,
            "dataset_manifest_hash": dataset_manifest_hash,
            "split_hash": split_hash,
            "cost_model_hash": cost_model_hash,
            "config_hash": config_hash,
            "schema_hash": schema_hash,
            "seed": protocol.robustness.seed,
            "row_count": int(row_count),
            "status": "ok",
            "blockers": list(blockers),
            "warnings": list(warnings),
            "safety_flags": dict(SAFETY_FLAGS),
            "integration_status": "b02_contract_unavailable_in_local_snapshot",
        }
        envelope = {
            "execution_id": f"b04_{evaluation_hash[:24]}",
            "execution_started_at_utc": generated_at_utc,
            "execution_completed_at_utc": generated_at_utc,
        }
        return {
            **envelope,
            "canonical_payload": canonical,
            "content_hash": stable_hash(canonical),
        }, None

    manifest = build_execution_manifest(
        execution_id=f"b04_{evaluation_hash[:24]}",
        execution_type="quantitative_evaluation",
        execution_started_at_utc=generated_at_utc,
        execution_completed_at_utc=generated_at_utc,
        project="SMART FUTUROS",
        branch=str(git_state["branch"]),
        commit_sha=str(git_state["commit_sha"]),
        dirty_worktree=bool(git_state["dirty_worktree"]),
        containerized=False,
        container_digest=None,
        runtime_environment={
            "execution_boundary": "research_only",
            "input_mode": input_mode,
            "platform": platform.system(),
        },
        python_version=platform.python_version(),
        dependency_lock_hash=_optional_file_hash(root / "requirements-dev.lock"),
        dataset_id="b04_candidate_evidence_v2",
        dataset_hash=dataset_hash,
        dataset_manifest_hash=dataset_manifest_hash,
        feature_contract_hash=None,
        target_store_hash=None,
        split_hash=split_hash,
        cost_model_hash=cost_model_hash,
        config_hash=config_hash,
        schema_hash=schema_hash,
        source_hashes=source_hashes,
        seed=protocol.robustness.seed,
        command="scripts/build_quant_validation_strategy_factory_v2.py",
        arguments=("--project-root", ".", "--no-write", "--json"),
        row_count=int(row_count),
        status="ok",
        blockers=blockers,
        warnings=warnings,
        safety_flags=SAFETY_FLAGS,
    )
    return manifest.to_dict(), manifest


def _optional_file_hash(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_report(
    *,
    root: Path,
    report: Mapping[str, Any],
    output_json: str | Path,
    output_markdown: str | Path,
    manifest_object: Any | None,
) -> None:
    try:
        from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
            AtomicWritePolicy,
            atomic_write_json,
            atomic_write_text,
        )
    except ImportError as exc:
        raise QuantValidationError("b01_atomic_writer_unavailable") from exc
    reports_root = (root / "data" / "reports").resolve(strict=False)
    json_target = _resolve_output(root, output_json, reports_root)
    markdown_target = _resolve_output(root, output_markdown, reports_root)
    policy = AtomicWritePolicy.restricted([reports_root], working_directory=root)
    write_payload = {
        **dict(report),
        "write_performed": True,
        "manifest_write_performed": manifest_object is not None,
    }
    atomic_write_json(json_target, write_payload, policy=policy, allow_nan=False)
    atomic_write_text(markdown_target, render_markdown(write_payload), policy=policy)
    if manifest_object is not None:
        from smartcrypto.data.canonical_data_foundation_v2.manifest import (
            write_execution_manifest,
        )

        write_execution_manifest(
            manifest=manifest_object,
            output_root="data/reports/quant_validation_strategy_factory_v2/manifests",
            project_root=root,
        )


def _resolve_output(root: Path, value: str | Path, reports_root: Path) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve(strict=False)
    try:
        resolved.relative_to(reports_root)
    except ValueError as exc:
        raise QuantValidationError("output_outside_data_reports") from exc
    if resolved.suffix.lower() not in {".json", ".md"}:
        raise QuantValidationError("unsupported_output_extension")
    return resolved


def _blocked_report(
    *,
    root: Path,
    protocol: ValidationProtocol,
    reason: str,
    blockers: Sequence[str],
    write_report: bool,
    output_json: str | Path,
    output_markdown: str | Path,
    generated_at_utc: str | None,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "schema_version": "quant_validation_strategy_factory_report_v2",
        "generated_at_utc": generated_at_utc or datetime.now(tz=UTC).isoformat(),
        "blockers": sorted(set(blockers)),
        "protocol": protocol.to_dict(),
        "authoritative_result": False,
        "candidate_count": 0,
        "candidate_scorecards": [],
        "promotion_allowed": False,
        "operational_authority": False,
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_json": _display_path(root, output_json),
        "output_markdown": _display_path(root, output_markdown),
        "safety_flags": dict(SAFETY_FLAGS),
        **dict(SAFETY_FLAGS),
    }
