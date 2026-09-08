"""Research-only Qlib long-eligible economic challenger V2.

This candidate preserves the market-context feature rematerialization and causal
walk-forward machinery from V1, but changes the economic learning objective to
absolute stressed Net PnL and makes long eligibility an explicit structural
selection policy. Shorts remain available to model training as negative/positive
examples, but are never eligible for treatment selection in this V2 candidate.

The module is intentionally fail-closed and has no operational authority.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import numpy as np
import pandas as pd

from smartcrypto.learning.paper_autolearning.economic_walkforward_cost_robustness import (
    build_paper_autolearning_economic_walkforward_cost_robustness_v1,
)
from smartcrypto.learning.paper_autolearning import (
    qlib_market_context_economic_challenger as base,
)

SCHEMA_VERSION = "paper_autolearning_qlib_long_economic_policy_challenger_v2"
TARGET_NAME = "absolute_stressed_net_pnl"
ELIGIBILITY_POLICY = "long_only_research_candidate"
SCORE_QUANTILES = (0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.85)
SELECTOR_FIELD = "paper_candidate_filter_decision"
SCORE_FIELD = "qlib_long_economic_score"
THRESHOLD_FIELD = "qlib_long_economic_threshold"
FOLD_FIELD = "qlib_long_economic_fold"
OOS_FIELD = "qlib_long_economic_oos"


def build_qlib_long_economic_policy_challenger_v2(
    *,
    project_root: str | Path,
    rows: Sequence[Mapping[str, Any]] | None = None,
    market_rows: Sequence[Mapping[str, Any]] | pd.DataFrame | None = None,
    outcome_path: str | Path | None = None,
    market_features_path: str | Path | None = None,
    additional_execution_stress_bps: float = base.DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    predictor: base.Predictor | None = None,
) -> dict[str, Any]:
    """Build the fixed V2 long-eligible, absolute-PnL Qlib challenger."""

    root = Path(project_root).resolve()
    outcome_source = base._resolve(root, outcome_path or base.DEFAULT_OUTCOME_PATH)
    market_source = base._resolve(
        root,
        market_features_path or base.DEFAULT_MARKET_FEATURES_PATH,
    )
    input_rows = [dict(row) for row in rows] if rows is not None else base._read_rows(outcome_source)
    raw_market = base._market_frame(market_rows, market_source)
    stress_bps = base._validate_stress_bps(additional_execution_stress_bps)

    normalized, invalid_time_count = base._normalize_outcomes(input_rows)
    try:
        rematerialized = base._rematerialize_market_features(raw_market)
        aligned, alignment_report = base._align_point_in_time_market_features(
            normalized,
            rematerialized,
        )
    except Exception as exc:
        return _blocked_preflight_report(
            outcome_source=outcome_source,
            market_source=market_source,
            input_row_count=len(input_rows),
            valid_closed_outcome_count=len(normalized),
            invalid_time_count=invalid_time_count,
            stress_bps=stress_bps,
            reason="market_feature_rematerialization_or_alignment_failed",
            blockers=["market_feature_rematerialization_or_alignment_failed"],
            alignment_report={
                "status": "blocked",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )

    common = _common_report_fields(
        outcome_source=outcome_source,
        market_source=market_source,
        input_row_count=len(input_rows),
        valid_closed_outcome_count=len(normalized),
        invalid_time_count=invalid_time_count,
        stress_bps=stress_bps,
        alignment_report=alignment_report,
    )

    preflight_blockers: list[str] = []
    if invalid_time_count:
        preflight_blockers.append("invalid_trade_time_detected")
    if alignment_report["coverage"] < base.MIN_PIT_ALIGNMENT_COVERAGE:
        preflight_blockers.append("point_in_time_market_feature_coverage_not_met")
    if alignment_report["target_notional_coverage"] < base.MIN_TARGET_NOTIONAL_COVERAGE:
        preflight_blockers.append("target_notional_coverage_not_met")
    if len(aligned) < base.MIN_TOTAL_TRADES:
        preflight_blockers.append("min_total_trades_not_met")
    fold_specs = base._build_fold_specs(len(aligned))
    if len(fold_specs) != base.FOLD_COUNT:
        preflight_blockers.append("walkforward_fold_count_not_met")

    if preflight_blockers:
        unique_preflight = list(dict.fromkeys(preflight_blockers))
        return {
            **common,
            "status": "blocked",
            "reason": unique_preflight[0],
            "decision": "MANTER_EM_RESEARCH",
            "predictor_mode": "not_started",
            "native_qlib_used": False,
            "model": {},
            "folds": [],
            "economic_evaluation": None,
            "blockers": unique_preflight,
        }

    if predictor is not None:
        return _run_with_predictor(
            common=common,
            normalized=aligned,
            fold_specs=fold_specs,
            stress_bps=stress_bps,
            predictor=predictor,
            predictor_mode="injected_test_double",
            native_qlib_used=False,
            model_metadata={
                "framework": "test_double",
                "target": TARGET_NAME,
                "eligibility_policy": ELIGIBILITY_POLICY,
                "fallback_used": False,
            },
        )

    try:
        with _native_qlib_lgb_predictor_context() as (native_predictor, metadata):
            return _run_with_predictor(
                common=common,
                normalized=aligned,
                fold_specs=fold_specs,
                stress_bps=stress_bps,
                predictor=native_predictor,
                predictor_mode="native_qlib_contrib_lgb_regression_long_policy_v2",
                native_qlib_used=True,
                model_metadata=metadata,
            )
    except Exception as exc:
        return {
            **common,
            "status": "blocked",
            "reason": "native_qlib_lgb_unavailable_or_failed",
            "decision": "MANTER_EM_RESEARCH",
            "predictor_mode": "native_qlib_contrib_lgb_regression_long_policy_v2",
            "native_qlib_used": False,
            "model": {
                "framework": "Microsoft Qlib",
                "class": "qlib.contrib.model.gbdt.LGBModel",
                "loss": "mse",
                "target": TARGET_NAME,
                "eligibility_policy": ELIGIBILITY_POLICY,
                "fallback_used": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            "folds": [],
            "economic_evaluation": None,
            "blockers": ["native_qlib_lgb_unavailable_or_failed"],
        }


def _run_with_predictor(
    *,
    common: Mapping[str, Any],
    normalized: Sequence[Mapping[str, Any]],
    fold_specs: Sequence[base.FoldSpec],
    stress_bps: float,
    predictor: base.Predictor,
    predictor_mode: str,
    native_qlib_used: bool,
    model_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    scored_rows = [base._public_row(row) for row in normalized]
    for row in scored_rows:
        row[SELECTOR_FIELD] = False
        row[OOS_FIELD] = False
        row[SCORE_FIELD] = None
        row[THRESHOLD_FIELD] = None
        row[FOLD_FIELD] = None

    blockers: list[str] = []
    fold_reports: list[dict[str, Any]] = []

    for spec in fold_specs:
        test_rows = [dict(row) for row in normalized[spec.test_start : spec.test_end]]
        prepared, preparation_blockers = _prepare_fold(
            prior_rows=normalized[: spec.test_start],
            test_rows=test_rows,
            stress_bps=stress_bps,
        )
        if prepared is None:
            fold_blockers = [f"{spec.fold_id}:{item}" for item in preparation_blockers]
            blockers.extend(fold_blockers)
            fold_reports.append(
                {
                    "fold_id": spec.fold_id,
                    "status": "blocked",
                    "reason": fold_blockers[0],
                    "test_trade_count": len(test_rows),
                    "eligible_test_trade_count": sum(_eligible(row) for row in test_rows),
                    "feature_columns": [],
                    "selected_trade_count": 0,
                    "calibration": {},
                    "blockers": fold_blockers,
                }
            )
            continue

        try:
            calibration_scores, test_scores = predictor(
                prepared.train_x,
                prepared.train_y,
                prepared.calibration_x,
                prepared.test_x,
                fold_id=spec.fold_id,
            )
            calibration_scores = base._finite_scores(
                calibration_scores,
                expected=len(prepared.calibration_rows),
            )
            test_scores = base._finite_scores(
                test_scores,
                expected=len(prepared.test_rows),
            )
        except Exception as exc:
            reason = f"{spec.fold_id}:predictor_failed:{type(exc).__name__}"
            blockers.append(reason)
            fold_reports.append(
                {
                    "fold_id": spec.fold_id,
                    "status": "blocked",
                    "reason": reason,
                    "fit_trade_count": len(prepared.fit_rows),
                    "calibration_trade_count": len(prepared.calibration_rows),
                    "test_trade_count": len(prepared.test_rows),
                    "eligible_calibration_trade_count": sum(
                        _eligible(row) for row in prepared.calibration_rows
                    ),
                    "eligible_test_trade_count": sum(
                        _eligible(row) for row in prepared.test_rows
                    ),
                    "feature_columns": list(prepared.feature_columns),
                    "selected_trade_count": 0,
                    "calibration": {},
                    "predictor_error": str(exc),
                    "blockers": [reason],
                }
            )
            continue

        calibration = _select_calibration_threshold(
            rows=prepared.calibration_rows,
            scores=calibration_scores,
            stress_bps=stress_bps,
        )
        if calibration["status"] != "ok":
            reason = f"{spec.fold_id}:calibration_no_economic_uplift_threshold"
            blockers.append(reason)
            fold_reports.append(
                {
                    "fold_id": spec.fold_id,
                    "status": "blocked",
                    "reason": reason,
                    "fit_trade_count": len(prepared.fit_rows),
                    "calibration_trade_count": len(prepared.calibration_rows),
                    "test_trade_count": len(prepared.test_rows),
                    "eligible_calibration_trade_count": sum(
                        _eligible(row) for row in prepared.calibration_rows
                    ),
                    "eligible_test_trade_count": sum(
                        _eligible(row) for row in prepared.test_rows
                    ),
                    "feature_columns": list(prepared.feature_columns),
                    "selected_trade_count": 0,
                    "calibration": calibration,
                    "test_information_cutoff_utc": prepared.test_information_cutoff_utc.isoformat(),
                    "calibration_information_cutoff_utc": (
                        prepared.calibration_information_cutoff_utc.isoformat()
                    ),
                    "blockers": [reason],
                }
            )
            continue

        threshold = float(calibration["threshold"])
        selected_count = 0
        for offset, (row, score) in enumerate(
            zip(prepared.test_rows, test_scores, strict=True)
        ):
            absolute_index = spec.test_start + offset
            selected = bool(_eligible(row) and score >= threshold)
            selected_count += int(selected)
            scored_rows[absolute_index][SELECTOR_FIELD] = selected
            scored_rows[absolute_index][OOS_FIELD] = True
            scored_rows[absolute_index][SCORE_FIELD] = round(float(score), 12)
            scored_rows[absolute_index][THRESHOLD_FIELD] = round(threshold, 12)
            scored_rows[absolute_index][FOLD_FIELD] = spec.fold_id

        fold_reports.append(
            {
                "fold_id": spec.fold_id,
                "status": "ok",
                "reason": "causal_long_eligible_threshold_applied",
                "fit_trade_count": len(prepared.fit_rows),
                "calibration_trade_count": len(prepared.calibration_rows),
                "test_trade_count": len(prepared.test_rows),
                "eligible_calibration_trade_count": sum(
                    _eligible(row) for row in prepared.calibration_rows
                ),
                "eligible_test_trade_count": sum(
                    _eligible(row) for row in prepared.test_rows
                ),
                "feature_columns": list(prepared.feature_columns),
                "selected_trade_count": selected_count,
                "calibration": calibration,
                "test_information_cutoff_utc": prepared.test_information_cutoff_utc.isoformat(),
                "calibration_information_cutoff_utc": (
                    prepared.calibration_information_cutoff_utc.isoformat()
                ),
                "outcome_availability_embargo_seconds": base.OUTCOME_AVAILABILITY_EMBARGO_SECONDS,
                "chronological_boundary_valid": base._causal_boundary_valid(prepared),
                "blockers": [],
            }
        )

    evaluation = build_paper_autolearning_economic_walkforward_cost_robustness_v1(
        project_root=Path(str(common["outcome_source_path"])).parent,
        rows=scored_rows,
        selector_field=SELECTOR_FIELD,
        additional_execution_stress_bps=stress_bps,
    )
    if evaluation["status"] != "ok":
        blockers.extend(f"economic:{item}" for item in evaluation.get("blockers", []))
    if not all(item.get("status") == "ok" for item in fold_reports):
        blockers.append("one_or_more_model_folds_blocked")

    unique_blockers = sorted(set(blockers))
    economically_robust = not unique_blockers and evaluation["status"] == "ok"
    native_certified_edge = economically_robust and native_qlib_used
    decision = (
        "QLIB_NATIVE_LONG_ECONOMIC_POLICY_EDGE_RESEARCH_ONLY"
        if native_certified_edge
        else (
            "TEST_DOUBLE_LONG_ECONOMIC_POLICY_PATH_VALIDATED"
            if economically_robust
            else "MANTER_EM_RESEARCH"
        )
    )
    return {
        **dict(common),
        "status": "ok" if economically_robust else "blocked",
        "reason": (
            "qlib_native_long_economic_policy_walkforward_edge_research_only"
            if native_certified_edge
            else (
                "test_double_long_economic_policy_path_validated"
                if economically_robust
                else unique_blockers[0]
            )
        ),
        "decision": decision,
        "predictor_mode": predictor_mode,
        "native_qlib_used": native_qlib_used,
        "model": dict(model_metadata),
        "folds": fold_reports,
        "oos_scored_trade_count": sum(
            1 for row in scored_rows if row.get(OOS_FIELD) is True
        ),
        "oos_selected_trade_count": sum(
            1
            for row in scored_rows
            if row.get(OOS_FIELD) is True and row.get(SELECTOR_FIELD) is True
        ),
        "economic_evaluation": evaluation,
        "blockers": unique_blockers,
        "scored_rows": scored_rows,
    }


def _prepare_fold(
    *,
    prior_rows: Sequence[Mapping[str, Any]],
    test_rows: Sequence[Mapping[str, Any]],
    stress_bps: float,
) -> tuple[base.PreparedFold | None, list[str]]:
    prepared, blockers = base._prepare_fold(
        prior_rows=prior_rows,
        test_rows=test_rows,
        stress_bps=stress_bps,
    )
    if prepared is None:
        return None, blockers

    target = np.asarray(
        [base._stressed_pnl(row, stress_bps) for row in prepared.fit_rows],
        dtype=float,
    )
    if len(target) >= 20:
        lower = float(np.quantile(target, base.TARGET_CLIP_LOWER_QUANTILE))
        upper = float(np.quantile(target, base.TARGET_CLIP_UPPER_QUANTILE))
        target = np.clip(target, lower, upper)
    if not np.isfinite(target).all():
        return None, ["non_finite_absolute_stressed_pnl_target"]

    return (
        base.PreparedFold(
            feature_columns=prepared.feature_columns,
            train_x=prepared.train_x,
            train_y=pd.Series(target, name="label", dtype=float),
            calibration_x=prepared.calibration_x,
            test_x=prepared.test_x,
            fit_rows=prepared.fit_rows,
            calibration_rows=prepared.calibration_rows,
            test_rows=prepared.test_rows,
            test_information_cutoff_utc=prepared.test_information_cutoff_utc,
            calibration_information_cutoff_utc=(
                prepared.calibration_information_cutoff_utc
            ),
        ),
        [],
    )


def _select_calibration_threshold(
    *,
    rows: Sequence[Mapping[str, Any]],
    scores: np.ndarray,
    stress_bps: float,
) -> dict[str, Any]:
    baseline = base._economic_metrics(rows, stress_bps)
    eligibility = np.asarray([_eligible(row) for row in rows], dtype=bool)
    eligible_scores = scores[eligibility]
    candidates: list[dict[str, Any]] = []

    if len(eligible_scores) < base.MIN_CALIBRATION_SELECTED_TRADES:
        return {
            "status": "blocked",
            "reason": "eligible_calibration_trade_count_not_met",
            "threshold": None,
            "selected_quantile": None,
            "eligible_trade_count": int(len(eligible_scores)),
            "baseline": _public_economic_metrics(baseline),
            "candidates": [],
        }

    for quantile in SCORE_QUANTILES:
        threshold = float(np.quantile(eligible_scores, quantile))
        selected = [
            row
            for row, score, eligible in zip(rows, scores, eligibility, strict=True)
            if eligible and score >= threshold
        ]
        metrics = base._economic_metrics(selected, stress_bps)
        delta_net_pnl = metrics["net_pnl"] - baseline["net_pnl"]
        expectancy_uplift = metrics["expectancy"] - baseline["expectancy"]
        passes = (
            metrics["trade_count"] >= base.MIN_CALIBRATION_SELECTED_TRADES
            and metrics["net_pnl"] > 0
            and metrics["expectancy"] > 0
            and metrics["profit_factor"] is not None
            and metrics["profit_factor"] >= base.MIN_TREATMENT_PROFIT_FACTOR
            and delta_net_pnl > 0
            and expectancy_uplift > 0
        )
        candidates.append(
            {
                "quantile": quantile,
                "threshold": threshold,
                "selected_trade_count": metrics["trade_count"],
                "stressed_net_pnl": metrics["net_pnl"],
                "stressed_expectancy": metrics["expectancy"],
                "stressed_profit_factor": metrics["profit_factor"],
                "delta_stressed_net_pnl": round(delta_net_pnl, 10),
                "delta_stressed_expectancy": round(expectancy_uplift, 10),
                "passes": passes,
            }
        )

    passing = [candidate for candidate in candidates if candidate["passes"]]
    if not passing:
        return {
            "status": "blocked",
            "reason": "no_economic_uplift_calibration_threshold",
            "threshold": None,
            "selected_quantile": None,
            "eligible_trade_count": int(len(eligible_scores)),
            "baseline": _public_economic_metrics(baseline),
            "candidates": candidates,
        }

    winner = max(
        passing,
        key=lambda item: (
            float(item["delta_stressed_net_pnl"]),
            float(item["delta_stressed_expectancy"]),
            int(item["selected_trade_count"]),
            -float(item["quantile"]),
        ),
    )
    return {
        "status": "ok",
        "reason": "long_eligible_economic_uplift_threshold_selected_past_only",
        "threshold": winner["threshold"],
        "selected_quantile": winner["quantile"],
        "eligible_trade_count": int(len(eligible_scores)),
        "selected_trade_count": winner["selected_trade_count"],
        "stressed_net_pnl": winner["stressed_net_pnl"],
        "stressed_expectancy": winner["stressed_expectancy"],
        "stressed_profit_factor": winner["stressed_profit_factor"],
        "delta_stressed_net_pnl": winner["delta_stressed_net_pnl"],
        "delta_stressed_expectancy": winner["delta_stressed_expectancy"],
        "baseline": _public_economic_metrics(baseline),
        "candidates": candidates,
    }


def _public_economic_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "trade_count": metrics["trade_count"],
        "stressed_net_pnl": metrics["net_pnl"],
        "stressed_expectancy": metrics["expectancy"],
        "stressed_profit_factor": metrics["profit_factor"],
    }


def _eligible(row: Mapping[str, Any]) -> bool:
    return str(row.get("side") or "").strip().lower() == "long"


@contextmanager
def _native_qlib_lgb_predictor_context() -> Iterator[tuple[base.Predictor, dict[str, Any]]]:
    with base._native_qlib_lgb_predictor_context() as (predictor, metadata):
        updated = dict(metadata)
        updated.update(
            {
                "target": TARGET_NAME,
                "eligibility_policy": ELIGIBILITY_POLICY,
                "training_universe": "all_causally_available_paper_trades",
                "short_rows_used_for_training": True,
                "short_rows_eligible_for_selection": False,
                "calibration_quantiles_computed_on_eligible_rows_only": True,
            }
        )
        yield predictor, updated


def _common_report_fields(
    *,
    outcome_source: Path,
    market_source: Path,
    input_row_count: int,
    valid_closed_outcome_count: int,
    invalid_time_count: int,
    stress_bps: float,
    alignment_report: Mapping[str, Any],
) -> dict[str, Any]:
    common = base._common_report_fields(
        outcome_source=outcome_source,
        market_source=market_source,
        input_row_count=input_row_count,
        valid_closed_outcome_count=valid_closed_outcome_count,
        invalid_time_count=invalid_time_count,
        stress_bps=stress_bps,
        alignment_report=alignment_report,
    )
    common.update(
        {
            "schema_version": SCHEMA_VERSION,
            "selector_field": SELECTOR_FIELD,
            "target": TARGET_NAME,
            "eligibility_policy": ELIGIBILITY_POLICY,
            "selection_method": (
                "causal_absolute_pnl_model_then_long_eligible_past_only_"
                "economic_calibration_then_forward_apply"
            ),
            "notional_used_as_predictive_feature": False,
            "shorts_eligible_for_treatment": False,
            "shorts_available_for_model_training": True,
            "eligible_score_quantiles": list(SCORE_QUANTILES),
            "prospective_confirmation_required": True,
            "operational_authority": False,
            "promotion_allowed": False,
            "model_promotion_performed": False,
            "active_model_changed": False,
            "changes_risk": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "writes_runtime": False,
            "write_performed": False,
        }
    )
    return common


def _blocked_preflight_report(
    *,
    outcome_source: Path,
    market_source: Path,
    input_row_count: int,
    valid_closed_outcome_count: int,
    invalid_time_count: int,
    stress_bps: float,
    reason: str,
    blockers: list[str],
    alignment_report: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        **_common_report_fields(
            outcome_source=outcome_source,
            market_source=market_source,
            input_row_count=input_row_count,
            valid_closed_outcome_count=valid_closed_outcome_count,
            invalid_time_count=invalid_time_count,
            stress_bps=stress_bps,
            alignment_report=alignment_report,
        ),
        "status": "blocked",
        "reason": reason,
        "decision": "MANTER_EM_RESEARCH",
        "predictor_mode": "not_started",
        "native_qlib_used": False,
        "model": {},
        "folds": [],
        "economic_evaluation": None,
        "blockers": blockers,
    }
