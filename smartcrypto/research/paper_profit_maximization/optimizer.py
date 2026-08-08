"""Profit-first optimizer orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from .candidates import (
    build_combined_filter_candidates,
    build_entry_filter_candidates,
    build_score_threshold_candidates,
    rank_candidates,
    standardize_exit_candidates,
)
from .contracts import (
    KNOWN_CORRUPT_PAPER_TRADE_IDS,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    ProfitMaximizationResult,
)
from .metrics import (
    build_loser_analysis,
    build_winner_capture_analysis,
    normalize_trader_master_rows,
    prepare_profit_dataset,
    profit_metrics,
    sort_trades,
    value_counts,
)


def build_profit_maximization(
    paper_dataset: pd.DataFrame,
    *,
    trader_master_rows: Sequence[Mapping[str, Any]] = (),
    score_rows: Sequence[Mapping[str, Any]] = (),
    exit_candidates: Sequence[Mapping[str, Any]] = (),
) -> ProfitMaximizationResult:
    prepared, score_report = prepare_profit_dataset(paper_dataset, score_rows=score_rows)
    eligible = sort_trades(
        prepared.loc[prepared["profit_optimization_eligible"]].copy()
    )
    baseline = profit_metrics(eligible)
    master_frame = normalize_trader_master_rows(trader_master_rows)
    master_eligible = master_frame.loc[
        master_frame["profit_optimization_eligible"]
    ].copy()
    master_metrics = profit_metrics(master_eligible)
    winner_capture = build_winner_capture_analysis(eligible)
    loser_analysis = build_loser_analysis(eligible)
    entry_candidates = build_entry_filter_candidates(eligible)
    score_candidates = build_score_threshold_candidates(eligible)
    singles = rank_candidates([*entry_candidates, *score_candidates])
    combined_candidates = build_combined_filter_candidates(eligible, singles[:12])
    standardized_exit = standardize_exit_candidates(eligible, exit_candidates)
    all_candidates = rank_candidates(
        [*entry_candidates, *score_candidates, *combined_candidates, *standardized_exit]
    )
    best = all_candidates[0] if all_candidates else None
    status = "ok" if not eligible.empty else "blocked"
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": (
            "paper_profit_maximization_completed"
            if status == "ok"
            else "no_profit_optimization_eligible_paper_trades"
        ),
        "objective": "maximize_net_pnl_and_winner_capture_in_paper",
        "decision": str(best["decision"]) if best else "MANTER_EM_RESEARCH",
        "paper_trade_count": int(len(prepared)),
        "eligible_trade_count": int(len(eligible)),
        "excluded_trade_count": int((~prepared["profit_optimization_eligible"]).sum()),
        "exclusion_reason_counts": value_counts(
            prepared.loc[
                ~prepared["profit_optimization_eligible"],
                "profit_optimization_exclusion_reason",
            ]
        ),
        "known_corrupt_trade_ids_excluded": sorted(
            value
            for value in KNOWN_CORRUPT_PAPER_TRADE_IDS
            if bool(prepared["trade_id_numeric"].eq(value).any())
        ),
        "baseline_paper_metrics": baseline,
        "trader_master_metrics": master_metrics,
        "trader_master_trade_count": int(len(master_frame)),
        "trader_master_eligible_trade_count": int(len(master_eligible)),
        "winner_capture": winner_capture,
        "loser_analysis": loser_analysis,
        "score_enrichment": score_report,
        "entry_candidate_count": len(entry_candidates),
        "score_candidate_count": len(score_candidates),
        "combined_candidate_count": len(combined_candidates),
        "exit_candidate_count": len(standardized_exit),
        "ranked_candidates": all_candidates[:20],
        "best_candidate": best,
        "positive_historical_candidate_found": bool(
            best is not None and best.get("decision") == "PROMOVER_PARA_PAPER_AB"
        ),
        **SAFETY_FLAGS,
    }
    return ProfitMaximizationResult(dataset=prepared, report=report)
