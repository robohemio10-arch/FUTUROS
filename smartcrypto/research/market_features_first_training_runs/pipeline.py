"""Research-only orchestration with environment and candidate gates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.data.trader_master_fingerprint_v2.source_profile import (
    load_source_profile,
)
from smartcrypto.research.profit_research_dataset.trade_snapshot import (
    build_paper_trade_snapshot,
)

from .contracts import (
    MARKET_FEATURE_COLUMNS,
    MODEL_FEATURE_COLUMNS,
    NO_CANDIDATE_DECISION,
    PAPER_V1_WATERMARK_UTC,
    POINT_IN_TIME_FEATURE_COLUMNS,
    RESEARCH_DECISION,
    SCHEMA_VERSION,
    TIMEFRAME_SECONDS,
    PipelineConfig,
    PipelinePaths,
    safety_flags,
)
from .reporting import write_research_outputs
from .training import (
    TrainingResult,
    block_monte_carlo,
    build_baselines,
    build_candidate_rankings,
    build_fold_contribution_report,
    evaluate_paper_holdout,
    qlib_gate,
    run_cohort_aware_experiments,
    run_supervised_models,
)
from .validation import (
    build_concept_drift_report,
    evaluate_canonical_environment,
    normalize_5m_features,
    normalize_master,
    normalize_paper,
)


@dataclass(frozen=True)
class PipelineResult:
    master_dataset: pd.DataFrame
    paper_dataset: pd.DataFrame
    predictions: pd.DataFrame
    report: dict[str, Any]


def run_market_features_first_training_pipeline(
    paths: PipelinePaths,
    config: PipelineConfig,
) -> PipelineResult:
    report = _base_report(paths, config)
    environment = evaluate_canonical_environment(config.environment_override)
    report["environment_gate"] = environment
    report["diagnostics_allowed"] = True
    report["training_allowed"] = bool(environment["training_allowed"])
    requested_financial_execution = _financial_execution_requested(config)
    if requested_financial_execution and not environment["training_allowed"]:
        report["stage_blockers"].append(
            {
                "stage": "canonical_environment",
                "reason": "canonical_training_environment_mismatch",
            }
        )

    if not config.rematerialize_features:
        report.update(status="blocked", reason="feature_rematerialization_not_requested")
        report["stage_blockers"].append(
            {"stage": "rematerialization", "reason": "explicit_flag_required"}
        )
        return PipelineResult(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), report)
    if not paths.master_path.is_file():
        report.update(status="blocked", reason="missing_trades_master")
        return PipelineResult(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), report)
    if not paths.market_features_path.is_file():
        report.update(status="blocked", reason="missing_market_features")
        return PipelineResult(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), report)

    try:
        master_raw = pd.read_parquet(paths.master_path)
        feature_raw = pd.read_parquet(paths.market_features_path)
        master, master_blockers = normalize_master(master_raw)
        features = normalize_5m_features(feature_raw)
        master, alignment_blockers = attach_point_in_time_5m(master, features, "master")
        row_blockers = [*master_blockers, *alignment_blockers]
    except (OSError, ValueError, KeyError, ImportError, TypeError) as exc:
        report.update(
            status="blocked",
            reason="master_rematerialization_failed",
            validation_errors=[f"{type(exc).__name__}:{exc}"],
        )
        return PipelineResult(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), report)

    paper = master.iloc[0:0].copy()
    paper_metadata: dict[str, Any] = {"status": "not_requested"}
    if config.allow_paper_read:
        paper, paper_metadata, paper_blockers = _load_paper(
            paths,
            features,
            empty_template=master.iloc[0:0].copy(),
        )
        row_blockers.extend(paper_blockers)
    elif config.evaluate_paper_holdout:
        report["stage_blockers"].append(
            {"stage": "paper_holdout", "reason": "paper_read_not_allowed"}
        )
    paper = mark_paper_evaluation_sets(paper)

    reconciliation = reconcile_master_rows(
        canonical_rows=len(master_raw), expected_rows=config.expected_master_rows
    )
    consumed_count = int(
        paper.get("paper_evaluation_set", pd.Series(dtype=str))
        .eq("paper_evaluation_set_v1_consumed")
        .sum()
    )
    prospective_count = int(
        paper.get("paper_evaluation_set", pd.Series(dtype=str))
        .eq("prospective_holdout_v2")
        .sum()
    )
    drift = build_concept_drift_report(master, paper)
    report.update(
        market_features_source_rows=int(len(feature_raw)),
        five_minute_feature_rows=int(len(features)),
        master_source_row_count=int(len(master_raw)),
        master_ready_row_count=int(master["row_status"].eq("ready").sum()),
        master_blocked_row_count=int(master["row_status"].eq("blocked").sum()),
        paper_source_row_count=int(len(paper)),
        paper_ready_row_count=int(paper["row_status"].eq("ready").sum()),
        paper_blocked_row_count=int(paper["row_status"].eq("blocked").sum()),
        paper_source_metadata=paper_metadata,
        master_row_reconciliation=reconciliation,
        known_input_fields=[
            "symbol",
            "side",
            "entry_hour_utc",
            "entry_day_of_week",
            "feature_age_seconds",
            "market_regime",
            "volatility_regime",
        ],
        model_feature_columns=list(MODEL_FEATURE_COLUMNS),
        model_feature_count=len(MODEL_FEATURE_COLUMNS),
        provenance_used_as_feature=False,
        master_observed_cost_total=float(master["observed_cost"].sum()),
        paper_observed_cost_total=float(paper["observed_cost"].sum()),
        row_blockers=row_blockers,
        blocker_reason_counts=_blocker_counts(row_blockers),
        individual_blocker_reporting=True,
        imputation_performed=False,
        forward_fill_performed=False,
        paper_rows_used_for_fit=0,
        paper_rows_used_for_calibration=0,
        paper_rows_used_for_threshold=0,
        paper_evaluation_set_v1_status="consumed",
        paper_evaluation_set_v1_consumed_count=consumed_count,
        paper_evaluation_set_v1_watermark_utc=PAPER_V1_WATERMARK_UTC,
        prospective_holdout_v2_count=prospective_count,
        concept_drift=drift,
    )

    can_train = bool(environment["training_allowed"])
    report["baselines"] = (
        build_baselines(master, seed=config.seed)
        if config.run_baselines and can_train
        else []
    )
    if config.run_baselines and not can_train:
        report["stage_blockers"].append(
            {
                "stage": "baselines",
                "reason": "canonical_training_environment_mismatch",
            }
        )
    training = _empty_training()
    if config.run_supervised_training and can_train:
        training = run_supervised_models(
            master,
            seed=config.seed,
            embargo_seconds=config.embargo_seconds,
            run_walkforward=config.run_walkforward,
        )
        report["stage_blockers"].extend(training.blockers)
    elif config.run_supervised_training:
        report["stage_blockers"].append(
            {
                "stage": "supervised_training",
                "reason": "canonical_training_environment_mismatch",
            }
        )
    elif config.run_walkforward or config.run_backtest or config.run_monte_carlo:
        report["stage_blockers"].append(
            {"stage": "training", "reason": "supervised_training_not_requested"}
        )

    training_performed = bool(training.model_summaries)
    report["supervised_training"] = {
        "requested": config.run_supervised_training,
        "performed": training_performed,
        "blocked_by_environment": bool(
            config.run_supervised_training and not can_train
        ),
        "model_families": [item["model_name"] for item in training.model_summaries],
        "model_summaries": list(training.model_summaries),
        "paper_rows_used_for_fit": 0,
        "paper_rows_used_for_calibration": 0,
        "paper_rows_used_for_threshold": 0,
        "imputation_performed": False,
    }
    report["walkforward"] = {
        "requested": config.run_walkforward,
        "performed": bool(can_train and not training.predictions.empty),
        "prediction_rows": int(len(training.predictions)),
        "purging_applied": training_performed,
        "embargo_applied": training_performed,
        "embargo_seconds": int(config.embargo_seconds),
        "fold_baseline_always_allow": list(training.fold_baselines),
        "baseline_uses_exact_model_test_rows": True,
    }
    if can_train and training_performed:
        report["cohort_aware_experiments"] = run_cohort_aware_experiments(
            master,
            full_population_result=training,
            seed=config.seed,
            embargo_seconds=config.embargo_seconds,
        )
        report["fold_3_contribution"] = build_fold_contribution_report(
            training.predictions,
            fold_id=3,
        )
    else:
        blocked_reason = (
            "canonical_training_environment_mismatch"
            if not can_train
            else "supervised_training_not_available"
        )
        report["cohort_aware_experiments"] = {
            "status": "blocked",
            "reason": blocked_reason,
            "experiments": {},
            "provenance_used_as_feature": False,
            "paper_rows_used_for_fit": 0,
            "paper_rows_used_for_calibration": 0,
            "paper_rows_used_for_threshold": 0,
            "selection_authority": False,
        }
        report["fold_3_contribution"] = {
            "status": "blocked",
            "reason": blocked_reason,
            "fold_id": 3,
            "dimensions": {},
            "baseline_fold_matched": True,
            "paper_rows_used": 0,
        }
    report["backtest"] = {
        "requested": config.run_backtest,
        "performed": bool(can_train and config.run_backtest and training_performed),
        "blocked_by_environment": bool(config.run_backtest and not can_train),
        "cost_policy": (
            "observed_net_pnl_authoritative; observed fee columns diagnostic; "
            "no synthetic gross reconstruction"
        ),
        "model_results": (
            list(training.model_summaries)
            if can_train and config.run_backtest
            else []
        ),
    }

    monte_carlo: list[dict[str, Any]] = []
    if can_train and config.run_monte_carlo and not training.predictions.empty:
        monte_carlo = block_monte_carlo(
            training.predictions,
            iterations=config.monte_carlo_iterations,
            block_size=config.monte_carlo_block_size,
            seed=config.seed,
        )
    report["monte_carlo"] = {
        "requested": config.run_monte_carlo,
        "performed": bool(monte_carlo),
        "blocked_by_environment": bool(config.run_monte_carlo and not can_train),
        "temporal_dependence_preserved": True,
        "method": "contiguous_block_bootstrap",
        "results": monte_carlo,
    }
    report["qlib_training"] = qlib_gate(
        requested=config.run_qlib_training,
        environment_allowed=can_train,
    )

    if config.evaluate_paper_holdout and can_train:
        report["paper_holdout"] = evaluate_paper_holdout(
            master=master,
            paper=paper.loc[
                paper["paper_evaluation_set"].eq(
                    "paper_evaluation_set_v1_consumed"
                )
            ],
            seed=config.seed,
            embargo_seconds=config.embargo_seconds,
        )
    elif config.evaluate_paper_holdout:
        report["paper_holdout"] = {
            "status": "blocked",
            "reason": "canonical_training_environment_mismatch",
            "paper_rows_used_for_fit": 0,
            "paper_rows_used_for_calibration": 0,
            "paper_rows_used_for_threshold": 0,
            "model_results": [],
        }
    else:
        report["paper_holdout"] = {
            "status": "not_requested",
            "paper_rows_used_for_fit": 0,
            "paper_rows_used_for_calibration": 0,
            "paper_rows_used_for_threshold": 0,
        }

    rankings = build_candidate_rankings(
        training.model_summaries if config.run_backtest else (),
        monte_carlo,
        maximum_negative_pnl_probability=config.maximum_negative_pnl_probability,
        leakage_detected=False,
        paper_rows_used_for_fit=0,
        paper_rows_used_for_threshold=0,
    )
    report.update(rankings)
    report["research_decision"] = RESEARCH_DECISION
    report["promotion_eligible"] = False
    report["model_promotion"] = False
    report["model_promotion_performed"] = False
    report["registry_write"] = False
    report["status"], report["reason"] = _final_status(
        report,
        config,
        environment_allowed=can_train,
    )
    if config.write_research_artifacts:
        report["outputs_written"] = write_research_outputs(
            paths=paths,
            master=master,
            paper=paper,
            predictions=training.predictions,
            report=report,
        )
        report["write_performed"] = True
    return PipelineResult(master, paper, training.predictions, report)


def attach_point_in_time_5m(
    trades: pd.DataFrame,
    features: pd.DataFrame,
    dataset_name: str,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    output = trades.copy()
    output["feature_timestamp_utc"] = pd.Series(
        pd.NaT, index=output.index, dtype="datetime64[ns, UTC]"
    )
    output["feature_available_at_utc"] = pd.Series(
        pd.NaT, index=output.index, dtype="datetime64[ns, UTC]"
    )
    output["feature_age_seconds"] = np.nan
    output["market_regime"] = "unknown"
    output["volatility_regime"] = "unknown"
    for source, target in zip(
        MARKET_FEATURE_COLUMNS, POINT_IN_TIME_FEATURE_COLUMNS, strict=True
    ):
        output[target] = np.nan
    blockers: list[dict[str, Any]] = []
    eligible_indices = output.index[output["row_status"].eq("eligible_for_alignment")]
    for symbol in sorted(output.loc[eligible_indices, "symbol"].dropna().unique()):
        left_indices = output.index[
            output.index.isin(eligible_indices) & output["symbol"].eq(symbol)
        ]
        left = output.loc[left_indices, ["open_time_utc"]].copy()
        left["_source_index"] = left.index
        right = features.loc[features["symbol"].eq(symbol)].copy()
        if right.empty:
            continue
        merged = pd.merge_asof(
            left.sort_values("open_time_utc"),
            right.sort_values("available_at_utc"),
            left_on="open_time_utc",
            right_on="available_at_utc",
            direction="backward",
            allow_exact_matches=True,
        )
        for _, row in merged.iterrows():
            index = int(row["_source_index"])
            output.at[index, "feature_timestamp_utc"] = row["candle_timestamp_utc"]
            output.at[index, "feature_available_at_utc"] = row["available_at_utc"]
            output.at[index, "market_regime"] = row.get("market_regime", "unknown")
            output.at[index, "volatility_regime"] = row.get(
                "volatility_regime", "unknown"
            )
            if pd.notna(row["available_at_utc"]):
                output.at[index, "feature_age_seconds"] = float(
                    (row["open_time_utc"] - row["available_at_utc"]).total_seconds()
                )
            for source, target in zip(
                MARKET_FEATURE_COLUMNS, POINT_IN_TIME_FEATURE_COLUMNS, strict=True
            ):
                output.at[index, target] = row[source]
    _attach_known_numeric_features(output)
    for index in eligible_indices:
        row = output.loc[index]
        reasons: list[str] = []
        if pd.isna(row["feature_available_at_utc"]):
            reasons.append("missing_closed_5m_feature")
        else:
            age = float(row["feature_age_seconds"])
            if age < 0:
                reasons.append("feature_not_available_before_entry")
            if age >= TIMEFRAME_SECONDS:
                reasons.append("five_minute_candle_gap_no_forward_fill")
        missing_features = [
            column
            for column in MODEL_FEATURE_COLUMNS
            if not np.isfinite(float(row[column]))
        ]
        if missing_features:
            reasons.append("missing_numeric_5m_features_no_imputation")
        if reasons:
            existing = tuple(row["validation_block_reasons"])
            output.at[index, "validation_block_reasons"] = tuple(
                sorted(set((*existing, *reasons)))
            )
            output.at[index, "row_status"] = "blocked"
            for reason in reasons:
                blockers.append(
                    {
                        "dataset": dataset_name,
                        "source_row_number": int(row["source_row_number"]),
                        "trade_id": str(row["trade_id"]),
                        "reason": reason,
                    }
                )
        else:
            output.at[index, "row_status"] = "ready"
    return output, blockers


def mark_paper_evaluation_sets(paper: pd.DataFrame) -> pd.DataFrame:
    output = paper.copy()
    if "paper_evaluation_set" not in output:
        output["paper_evaluation_set"] = "not_eligible"
    if output.empty:
        return output
    watermark = pd.Timestamp(PAPER_V1_WATERMARK_UTC)
    ready = output["row_status"].eq("ready")
    output.loc[
        ready & output["close_time_utc"].le(watermark), "paper_evaluation_set"
    ] = "paper_evaluation_set_v1_consumed"
    output.loc[
        ready & output["close_time_utc"].gt(watermark), "paper_evaluation_set"
    ] = "prospective_holdout_v2"
    return output


def reconcile_master_rows(*, canonical_rows: int, expected_rows: int) -> dict[str, Any]:
    delta = int(canonical_rows - expected_rows)
    return {
        "expected_rows": int(expected_rows),
        "canonical_rows": int(canonical_rows),
        "row_count_delta": delta,
        "status": "matched" if delta == 0 else "mismatch_explicit",
        "all_canonical_rows_retained": True,
        "silently_discarded_row_count": 0,
        "unresolved_delta_row_count": abs(delta),
        "unresolved_delta_scope": (
            "none"
            if delta == 0
            else "aggregate_count_contract_without_row_identity_evidence"
        ),
        "training_rows_selected_only_by_row_level_validation": True,
    }


def _attach_known_numeric_features(frame: pd.DataFrame) -> None:
    frame["meta_symbol_btcusdt"] = frame["symbol"].eq("BTCUSDT").astype(float)
    frame["meta_symbol_ethusdt"] = frame["symbol"].eq("ETHUSDT").astype(float)
    frame["meta_side_long"] = frame["side"].eq("long").astype(float)
    frame["meta_side_short"] = frame["side"].eq("short").astype(float)
    frame["entry_hour_utc"] = frame["open_time_utc"].dt.hour.astype(float)
    frame["entry_day_of_week"] = frame["open_time_utc"].dt.dayofweek.astype(float)
    for regime in ("trend_up", "trend_down", "range", "unknown"):
        frame[f"market_regime_{regime}"] = frame["market_regime"].eq(regime).astype(float)
    for regime in ("low", "normal", "high", "unknown"):
        frame[f"volatility_regime_{regime}"] = (
            frame["volatility_regime"].eq(regime).astype(float)
        )


def _load_paper(
    paths: PipelinePaths,
    features: pd.DataFrame,
    *,
    empty_template: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    if not paths.paper_snapshot_path.is_file():
        return (
            empty_template,
            {"status": "blocked", "reason": "paper_snapshot_missing"},
            [{"dataset": "paper", "reason": "paper_snapshot_missing"}],
        )
    profile = load_source_profile(paths.source_profile_path)
    raw, metadata = build_paper_trade_snapshot(
        project_root=paths.project_root,
        source_path=paths.paper_snapshot_path,
        profile=profile,
        authoritative_snapshot=True,
    )
    if raw.empty or metadata.get("status") != "ok":
        return (
            empty_template,
            metadata,
            [{"dataset": "paper", "reason": metadata.get("reason")}],
        )
    normalized, blockers = normalize_paper(raw)
    aligned, alignment_blockers = attach_point_in_time_5m(
        normalized, features, "paper"
    )
    return aligned, metadata, [*blockers, *alignment_blockers]


def _base_report(paths: PipelinePaths, config: PipelineConfig) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": NO_CANDIDATE_DECISION,
        "research_decision": RESEARCH_DECISION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "master_path": str(paths.master_path),
        "market_features_path": str(paths.market_features_path),
        "paper_snapshot_path": str(paths.paper_snapshot_path),
        "write_requested": bool(config.write_research_artifacts),
        "write_performed": False,
        "outputs_written": [],
        "five_minute_contract": {
            "timeframe": "5m",
            "timestamp_semantics": "candle_open",
            "available_at_rule": "candle_timestamp_utc_plus_5_minutes",
            "join_rule": "latest_available_at_lte_trade_open",
            "maximum_feature_age_seconds_exclusive": TIMEFRAME_SECONDS,
            "forward_fill_across_gaps": False,
            "imputation": False,
        },
        "one_minute_gate": {
            "status": "blocked",
            "reason": "confirmed_zero_percent_paper_coverage",
            "used_for_training": False,
            "used_for_holdout": False,
        },
        "stage_blockers": [],
        "validation_errors": [],
        "row_blockers": [],
        "diagnostic_ranking": [],
        "eligible_candidate_ranking": [],
        "selected_candidate": None,
        "promotion_eligible": False,
        **safety_flags(),
        "safety_flags": safety_flags(),
    }


def _financial_execution_requested(config: PipelineConfig) -> bool:
    return any(
        (
            config.run_supervised_training,
            config.run_qlib_training,
            config.run_walkforward,
            config.run_backtest,
            config.run_monte_carlo,
            config.evaluate_paper_holdout,
        )
    )


def _empty_training() -> TrainingResult:
    return TrainingResult(pd.DataFrame(), (), (), {}, ())


def _blocker_counts(blockers: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for blocker in blockers:
        reason = str(blocker.get("reason", "unknown"))
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _final_status(
    report: dict[str, Any],
    config: PipelineConfig,
    *,
    environment_allowed: bool,
) -> tuple[str, str]:
    if report["master_ready_row_count"] == 0:
        return "blocked", "no_master_rows_ready_for_research"
    if _financial_execution_requested(config) and not environment_allowed:
        return "blocked", "canonical_training_environment_mismatch"
    if config.run_supervised_training and not report["supervised_training"]["performed"]:
        return "blocked", "supervised_training_not_technically_runnable"
    if (
        report["stage_blockers"]
        or report["master_row_reconciliation"]["status"] != "matched"
        or report["qlib_training"].get("status") == "blocked"
    ):
        return "warning", "pipeline_completed_with_controlled_research_blockers"
    return "ok", "research_diagnostics_completed_no_write"
