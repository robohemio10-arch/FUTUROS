"""Orchestration for profit-aware daily paper auto-training.

Research/paper/shadow only: this module does not mutate Freqtrade, RiskManager,
active models, order submission, canary, live settings or containers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.paper_autotrain_daily_quarantine_activation.activation import (
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MD,
    build_paper_autotrain_daily_quarantine_activation_v1,
    load_microbatch,
    render_markdown,
    resolve,
    write_json,
)
from smartcrypto.research.profit_research import (
    build_profit_research,
    resolve_profit_research_paths,
)
from smartcrypto.research.profit_research.paper_analysis import (
    financial_metrics,
    load_market_candles,
)

from .candidates import (
    _build_combined_candidates,
    _build_filter_candidates,
    _build_joint_candidates,
    _rank_candidates,
)
from .contracts import (
    FINANCIAL_OBJECTIVES,
    KNOWN_FINANCIAL_SAMPLE_INVALID_IDS,
    NO_RUNTIME_CHANGE_FLAGS,
    SCHEMA_VERSION,
    FinancialObjectiveResult,
)
from .research import (
    _attach_scores,
    _authoritative_profit_sources_exist,
    _build_trader_master_reference,
    _data_limitations,
    _load_score_sources,
    _loser_summary,
    _prepare_financial_microbatch,
    _prepare_research_rows,
    _resolve_master_rows,
    _winner_capture_summary,
)
from .trainer import FinancialObjectiveTrainerBackend
from .weighting import _weight_microbatch


def build_financial_objective(
    project_root: str | Path,
    *,
    microbatch_frame: pd.DataFrame | None = None,
    profit_dataset_frame: pd.DataFrame | None = None,
    profit_report: Mapping[str, Any] | None = None,
    trader_master_rows: Sequence[Mapping[str, Any]] | None = None,
    score_rows: Sequence[Mapping[str, Any]] | None = None,
    candles_frame: pd.DataFrame | None = None,
) -> FinancialObjectiveResult:
    """Build profit diagnostics and financially weight the daily microbatch."""

    root = Path(project_root).resolve()
    raw_microbatch, microbatch_source = load_microbatch(root, microbatch_frame)
    microbatch = _prepare_financial_microbatch(raw_microbatch)
    authoritative_expected = _authoritative_profit_sources_exist(root)

    research_dataset = (
        profit_dataset_frame.copy()
        if profit_dataset_frame is not None
        else pd.DataFrame()
    )
    research_report = dict(profit_report or {})
    if profit_dataset_frame is None and authoritative_expected:
        result = build_profit_research(resolve_profit_research_paths(root), write=False)
        research_dataset = result.dataset.copy()
        research_report = dict(result.report)
    if not research_report:
        research_report = {
            "status": "not_available",
            "reason": "authoritative_profit_sources_not_materialized",
        }

    research = _prepare_research_rows(research_dataset)
    if score_rows is not None:
        resolved_score_rows = [dict(row) for row in score_rows]
        score_inventory = [
            {"source": "in_memory", "status": "ok", "row_count": len(resolved_score_rows)}
        ]
    else:
        resolved_score_rows, score_inventory = _load_score_sources(root)
    research, score_coverage = _attach_scores(research, resolved_score_rows, microbatch)

    master_rows, master_read = _resolve_master_rows(root, trader_master_rows)
    trader_master_reference = _build_trader_master_reference(master_rows)

    eligible = (
        research.loc[~research["financial_sample_invalid"]].copy()
        if not research.empty
        else research
    )
    baseline_metrics = financial_metrics(eligible) if not eligible.empty else {}
    winner_capture = _winner_capture_summary(eligible)
    loser_analysis = _loser_summary(eligible)
    filter_candidates = _build_filter_candidates(eligible)
    combined_candidates = _build_combined_candidates(eligible, filter_candidates[:10])
    exit_candidates = list(research_report.get("candidate_exit_changes", []))
    candles = candles_frame.copy() if candles_frame is not None else pd.DataFrame()
    if candles_frame is None and authoritative_expected:
        try:
            candles, _candle_inventory = load_market_candles(
                resolve_profit_research_paths(root).candles
            )
        except (OSError, ValueError, ImportError):
            candles = pd.DataFrame()
    joint_candidates = _build_joint_candidates(
        eligible,
        [*filter_candidates[:6], *combined_candidates[:6]],
        exit_candidates[:4],
        candles,
    )
    ranked_candidates = _rank_candidates(
        [*filter_candidates, *combined_candidates, *joint_candidates]
    )
    best_candidate = ranked_candidates[0] if ranked_candidates else None

    weighted, invalid_count = _weight_microbatch(microbatch, research)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": str(research_report.get("status", "not_available")),
        "reason": research_report.get("reason"),
        "objective_priority": list(FINANCIAL_OBJECTIVES),
        "authoritative_expected": authoritative_expected,
        "microbatch_source": microbatch_source,
        "financial_weighted_rows": int(len(weighted)),
        "financial_sample_invalid_count": invalid_count,
        "known_financial_sample_invalid_ids": sorted(KNOWN_FINANCIAL_SAMPLE_INVALID_IDS),
        "baseline_financial_metrics": baseline_metrics,
        "winner_capture": winner_capture,
        "loser_analysis": loser_analysis,
        "top_profitable_segments": research_report.get("top_profitable_segments", []),
        "top_harmful_segments": research_report.get("top_harmful_segments", []),
        "entry_filter_candidates": filter_candidates[:20],
        "combined_filter_candidates": combined_candidates[:20],
        "exit_policy_candidates": exit_candidates[:10],
        "joint_profit_candidates": joint_candidates[:20],
        "ranked_financial_candidates": ranked_candidates[:20],
        "best_candidate": best_candidate,
        "candidate_stake_policies": research_report.get("candidate_stake_policies", []),
        "entry_timing_scenarios": research_report.get("entry_timing_scenarios", []),
        "trader_master_reference": trader_master_reference,
        "trader_master_read": master_read,
        "score_source_inventory": score_inventory,
        "score_coverage": score_coverage,
        "data_limitations": _data_limitations(research_report, score_coverage),
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "model_promotion_performed": False,
        "runtime_changed": False,
        **NO_RUNTIME_CHANGE_FLAGS,
    }
    return FinancialObjectiveResult(microbatch=weighted, summary=summary)


def build_profit_aware_daily_autotrain(
    project_root: str | Path,
    *,
    once: bool = False,
    write_feedback: bool = False,
    train_challenger: bool = False,
    write_quarantine_artifacts: bool = False,
    write_report: bool = False,
    dry_run: bool = False,
    scheduler_check: bool = False,
    fail_on_operational_write: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
    closed_trades_frame: pd.DataFrame | None = None,
    microbatch_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Run the canonical daily quarantine cycle with the financial objective."""

    root = Path(project_root).resolve()
    objective = build_financial_objective(root, microbatch_frame=microbatch_frame)
    summary = objective.summary
    block_training = bool(
        train_challenger
        and summary.get("authoritative_expected")
        and summary.get("status") not in {"ok", "warning"}
    )
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=root,
        once=once,
        write_feedback=write_feedback,
        train_challenger=train_challenger and not block_training,
        write_quarantine_artifacts=write_quarantine_artifacts,
        write_report=False,
        dry_run=dry_run,
        scheduler_check=scheduler_check,
        fail_on_operational_write=fail_on_operational_write,
        output_json_path=output_json_path,
        output_markdown_path=output_markdown_path,
        generated_at_utc=generated_at_utc,
        closed_trades_frame=closed_trades_frame,
        microbatch_frame=objective.microbatch,
        trainer_backend=FinancialObjectiveTrainerBackend(),
    )
    report["profit_maximization_summary"] = summary
    report["financial_objectives"] = list(FINANCIAL_OBJECTIVES)
    report["profit_objective_applied_to_training"] = bool(
        len(objective.microbatch)
        and "financial_sample_weight" in objective.microbatch.columns
    )
    report["financial_sample_invalid_count"] = int(
        summary.get("financial_sample_invalid_count", 0)
    )
    report["best_financial_candidate"] = summary.get("best_candidate")
    report.update(NO_RUNTIME_CHANGE_FLAGS)
    report["safety_flags"] = {
        **dict(report.get("safety_flags", {})),
        **NO_RUNTIME_CHANGE_FLAGS,
    }
    if block_training:
        report["blockers"] = sorted(
            set([*report.get("blockers", []), "profit_maximization_research_blocked"])
        )
        report["status"] = "blocked"
        report["reason"] = "profit_maximization_research_blocked"
        report["qlib_challenger_train_status"] = "blocked"
        report["ai_shadow_challenger_train_status"] = "blocked"

    if write_report:
        output_json = resolve(root, output_json_path, DEFAULT_REPORT_JSON)
        output_markdown = resolve(root, output_markdown_path, DEFAULT_REPORT_MD)
        report.setdefault("output_paths", {})["report_json"] = str(output_json)
        report["output_paths"]["report_markdown"] = str(output_markdown)
        report["write_performed"] = True
        write_json(output_json, report)
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.write_text(render_markdown(report), encoding="utf-8")
    return report
