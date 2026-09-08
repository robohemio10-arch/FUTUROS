"""Qlib-native market-context economic challenger for closed Paper outcomes.

Research-only and fail-closed. The challenger rematerializes operational market
features from raw OHLCV, aligns only fully closed 5m candles to each trade entry,
trains a native ``qlib.contrib.model.gbdt.LGBModel`` on economically normalized
stressed returns, calibrates a selection threshold on past-only outcomes, and applies
that frozen threshold to three chronological forward folds.

The model never receives future returns, realized PnL, fees, funding, notional,
leverage, quantity, close time, or any post-entry field as predictive features. No
runtime, registry, active model, risk, signal, private exchange, or order state is
changed.
"""

from __future__ import annotations

import importlib
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Protocol

import numpy as np
import pandas as pd

from smartcrypto.data.feature_builder import build_market_feature_frame
from smartcrypto.execution.freqtrade_contract import internal_symbol

from .economic_walkforward_cost_robustness import (
    DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    FOLD_COUNT,
    INITIAL_CONTEXT_FRACTION,
    MIN_INITIAL_CONTEXT_TRADES,
    MIN_TREATMENT_PROFIT_FACTOR,
    build_paper_autolearning_economic_walkforward_cost_robustness_v1,
)

SCHEMA_VERSION = "paper_autolearning_qlib_market_context_economic_challenger_v1"
DEFAULT_OUTCOME_PATH = Path("data/feedback/outcome_events.parquet")
DEFAULT_MARKET_FEATURES_PATH = Path("data/features/market_features_60d.parquet")
TIMEFRAME = "5m"
TIMEFRAME_SECONDS = 300

SELECTOR_FIELD = "paper_candidate_filter_decision"
SCORE_FIELD = "qlib_market_context_economic_score"
THRESHOLD_FIELD = "qlib_market_context_economic_threshold"
FOLD_FIELD = "qlib_market_context_economic_fold"
OOS_FIELD = "qlib_market_context_economic_oos"

MIN_TOTAL_TRADES = 300
MIN_PIT_ALIGNMENT_COVERAGE = 0.99
MIN_TARGET_NOTIONAL_COVERAGE = 0.95
CALIBRATION_FRACTION = 0.25
MIN_FIT_TRADES = 90
MIN_CALIBRATION_TRADES = 30
MIN_CALIBRATION_SELECTED_TRADES = 10
MIN_FEATURE_COUNT = 8
OUTCOME_AVAILABILITY_EMBARGO_SECONDS = 300
TARGET_CLIP_LOWER_QUANTILE = 0.025
TARGET_CLIP_UPPER_QUANTILE = 0.975
SCORE_QUANTILES = (0.50, 0.60, 0.70, 0.80, 0.85)
RANDOM_SEED = 42
MODEL_VALIDATION_FRACTION = 0.20
MIN_MODEL_VALIDATION_TRADES = 20
MIN_MODEL_TRAIN_TRADES = 70

PIT_REQUIRED_MARKET_COLUMNS = (
    "ret_30",
    "dist_ema200",
    "macd_hist",
    "atr_pct_14",
    "vol_120",
    "trend_score",
)

MARKET_SOURCE_COLUMNS = (
    "ret_1",
    "ret_3",
    "ret_5",
    "ret_10",
    "ret_15",
    "ret_30",
    "dist_ema20",
    "dist_ema50",
    "dist_ema200",
    "rsi_14",
    "macd_line",
    "macd_signal",
    "macd_hist",
    "atr_pct_14",
    "vol_30",
    "vol_120",
    "volume_rel_30",
    "volume_z_30",
    "hl_range",
    "body_range",
    "upper_wick",
    "lower_wick",
    "trend_score",
    "market_regime",
    "close",
)

MODEL_FEATURE_COLUMNS = (
    "feature_side_long",
    "feature_symbol_btcusdt",
    "feature_ret_1",
    "feature_ret_3",
    "feature_ret_5",
    "feature_ret_10",
    "feature_ret_15",
    "feature_ret_30",
    "feature_dist_ema20",
    "feature_dist_ema50",
    "feature_dist_ema200",
    "feature_rsi_14_scaled",
    "feature_macd_line_pct",
    "feature_macd_signal_pct",
    "feature_macd_hist_pct",
    "feature_atr_pct_14",
    "feature_vol_30",
    "feature_vol_120",
    "feature_volume_rel_30",
    "feature_volume_z_30",
    "feature_hl_range",
    "feature_body_range",
    "feature_upper_wick",
    "feature_lower_wick",
    "feature_trend_score_scaled",
    "feature_regime_trend_up",
    "feature_regime_trend_down",
    "feature_regime_high_vol",
    "feature_trend_alignment",
    "feature_ret_5_side",
    "feature_ret_30_side",
    "feature_dist_ema20_side",
    "feature_macd_hist_pct_side",
    "feature_hour_sin",
    "feature_hour_cos",
    "feature_weekday_sin",
    "feature_weekday_cos",
)


class Predictor(Protocol):
    def __call__(
        self,
        train_x: pd.DataFrame,
        train_y: pd.Series,
        calibration_x: pd.DataFrame,
        test_x: pd.DataFrame,
        *,
        fold_id: str,
    ) -> tuple[np.ndarray, np.ndarray]: ...


@dataclass(frozen=True)
class FoldSpec:
    fold_id: str
    test_start: int
    test_end: int


@dataclass(frozen=True)
class PreparedFold:
    feature_columns: tuple[str, ...]
    train_x: pd.DataFrame
    train_y: pd.Series
    calibration_x: pd.DataFrame
    test_x: pd.DataFrame
    fit_rows: tuple[dict[str, Any], ...]
    calibration_rows: tuple[dict[str, Any], ...]
    test_rows: tuple[dict[str, Any], ...]
    test_information_cutoff_utc: datetime
    calibration_information_cutoff_utc: datetime


def build_qlib_market_context_economic_challenger_v1(
    *,
    project_root: str | Path,
    rows: Sequence[Mapping[str, Any]] | None = None,
    market_rows: Sequence[Mapping[str, Any]] | pd.DataFrame | None = None,
    outcome_path: str | Path | None = None,
    market_features_path: str | Path | None = None,
    additional_execution_stress_bps: float = DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    predictor: Predictor | None = None,
) -> dict[str, Any]:
    """Build and evaluate the native Qlib market-context economic challenger."""

    root = Path(project_root).resolve()
    outcome_source = _resolve(root, outcome_path or DEFAULT_OUTCOME_PATH)
    market_source = _resolve(root, market_features_path or DEFAULT_MARKET_FEATURES_PATH)
    input_rows = [dict(row) for row in rows] if rows is not None else _read_rows(outcome_source)
    raw_market = _market_frame(market_rows, market_source)
    stress_bps = _validate_stress_bps(additional_execution_stress_bps)

    normalized, invalid_time_count = _normalize_outcomes(input_rows)
    try:
        rematerialized = _rematerialize_market_features(raw_market)
        aligned, alignment_report = _align_point_in_time_market_features(
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
    if alignment_report["coverage"] < MIN_PIT_ALIGNMENT_COVERAGE:
        preflight_blockers.append("point_in_time_market_feature_coverage_not_met")
    if alignment_report["target_notional_coverage"] < MIN_TARGET_NOTIONAL_COVERAGE:
        preflight_blockers.append("target_notional_coverage_not_met")
    if len(aligned) < MIN_TOTAL_TRADES:
        preflight_blockers.append("min_total_trades_not_met")
    fold_specs = _build_fold_specs(len(aligned))
    if len(fold_specs) != FOLD_COUNT:
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
                predictor_mode="native_qlib_contrib_lgb_regression",
                native_qlib_used=True,
                model_metadata=metadata,
            )
    except Exception as exc:
        return {
            **common,
            "status": "blocked",
            "reason": "native_qlib_lgb_unavailable_or_failed",
            "decision": "MANTER_EM_RESEARCH",
            "predictor_mode": "native_qlib_contrib_lgb_regression",
            "native_qlib_used": False,
            "model": {
                "framework": "Microsoft Qlib",
                "class": "qlib.contrib.model.gbdt.LGBModel",
                "loss": "mse",
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
    fold_specs: Sequence[FoldSpec],
    stress_bps: float,
    predictor: Predictor,
    predictor_mode: str,
    native_qlib_used: bool,
    model_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    scored_rows = [_public_row(row) for row in normalized]
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
            calibration_scores = _finite_scores(
                calibration_scores,
                expected=len(prepared.calibration_rows),
            )
            test_scores = _finite_scores(
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
        for offset, score in enumerate(test_scores):
            absolute_index = spec.test_start + offset
            selected = bool(score >= threshold)
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
                "reason": "causal_market_context_threshold_calibrated_on_past_only",
                "fit_trade_count": len(prepared.fit_rows),
                "calibration_trade_count": len(prepared.calibration_rows),
                "test_trade_count": len(prepared.test_rows),
                "feature_columns": list(prepared.feature_columns),
                "selected_trade_count": selected_count,
                "calibration": calibration,
                "test_information_cutoff_utc": prepared.test_information_cutoff_utc.isoformat(),
                "calibration_information_cutoff_utc": (
                    prepared.calibration_information_cutoff_utc.isoformat()
                ),
                "outcome_availability_embargo_seconds": OUTCOME_AVAILABILITY_EMBARGO_SECONDS,
                "chronological_boundary_valid": _causal_boundary_valid(prepared),
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
        blockers.extend(
            f"economic:{item}" for item in evaluation.get("blockers", [])
        )
    if not all(item.get("status") == "ok" for item in fold_reports):
        blockers.append("one_or_more_model_folds_blocked")

    unique_blockers = sorted(set(blockers))
    economically_robust = not unique_blockers and evaluation["status"] == "ok"
    native_certified_edge = economically_robust and native_qlib_used
    decision = (
        "QLIB_NATIVE_MARKET_CONTEXT_ECONOMIC_EDGE_RESEARCH_ONLY"
        if native_certified_edge
        else (
            "TEST_DOUBLE_MARKET_CONTEXT_ECONOMIC_PATH_VALIDATED"
            if economically_robust
            else "MANTER_EM_RESEARCH"
        )
    )
    return {
        **dict(common),
        "status": "ok" if economically_robust else "blocked",
        "reason": (
            "qlib_native_market_context_walkforward_economic_edge_research_only"
            if native_certified_edge
            else (
                "test_double_market_context_economic_path_validated"
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
) -> tuple[PreparedFold | None, list[str]]:
    blockers: list[str] = []
    if not test_rows:
        return None, ["empty_test_fold"]

    test_information_cutoff = min(row["__open_time"] for row in test_rows)
    test_label_cutoff = test_information_cutoff - timedelta(
        seconds=OUTCOME_AVAILABILITY_EMBARGO_SECONDS
    )
    context = [
        dict(row)
        for row in prior_rows
        if isinstance(row.get("__close_time"), datetime)
        and row["__close_time"] <= test_label_cutoff
    ]
    if len(context) < MIN_INITIAL_CONTEXT_TRADES:
        blockers.append("min_causally_available_train_context_not_met")
        return None, blockers

    calibration_count = max(
        MIN_CALIBRATION_TRADES,
        math.floor(len(context) * CALIBRATION_FRACTION),
    )
    provisional_fit = context[:-calibration_count]
    calibration_rows = context[-calibration_count:]
    if not provisional_fit or len(calibration_rows) < MIN_CALIBRATION_TRADES:
        return None, ["calibration_partition_not_available"]

    calibration_information_cutoff = min(
        row["__open_time"] for row in calibration_rows
    )
    calibration_label_cutoff = calibration_information_cutoff - timedelta(
        seconds=OUTCOME_AVAILABILITY_EMBARGO_SECONDS
    )
    fit_rows = [
        row
        for row in provisional_fit
        if row["__close_time"] <= calibration_label_cutoff
    ]
    if len(fit_rows) < MIN_FIT_TRADES:
        blockers.append("min_causally_available_fit_trades_not_met")
    if blockers:
        return None, blockers

    fit_features = [_model_feature_row(row) for row in fit_rows]
    calibration_features = [_model_feature_row(row) for row in calibration_rows]
    heldout_features = [_model_feature_row(row) for row in test_rows]

    feature_columns: list[str] = []
    medians: dict[str, float] = {}
    for column in MODEL_FEATURE_COLUMNS:
        fit_values = [_numeric(row.get(column)) for row in fit_features]
        finite = [value for value in fit_values if value is not None and math.isfinite(value)]
        if not finite:
            continue
        median = float(np.median(np.asarray(finite, dtype=float)))
        imputed = np.asarray(
            [
                median if value is None or not math.isfinite(value) else value
                for value in fit_values
            ],
            dtype=float,
        )
        if float(np.std(imputed)) <= 1e-12:
            continue
        feature_columns.append(column)
        medians[column] = median

    if len(feature_columns) < MIN_FEATURE_COUNT:
        return None, ["min_feature_count_not_met"]

    def matrix(feature_rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
        data: dict[str, list[float]] = {}
        for column in feature_columns:
            values: list[float] = []
            for row in feature_rows:
                raw = _numeric(row.get(column))
                values.append(
                    medians[column]
                    if raw is None or not math.isfinite(raw)
                    else raw
                )
            data[column] = values
        return pd.DataFrame(data, columns=feature_columns, dtype=float)

    train_x = matrix(fit_features)
    calibration_x = matrix(calibration_features)
    test_x = matrix(heldout_features)
    target = np.asarray(
        [_stressed_return_bps(row, stress_bps) for row in fit_rows],
        dtype=float,
    )
    if len(target) >= 20:
        lower = float(np.quantile(target, TARGET_CLIP_LOWER_QUANTILE))
        upper = float(np.quantile(target, TARGET_CLIP_UPPER_QUANTILE))
        target = np.clip(target, lower, upper)
    train_y = pd.Series(target, name="label", dtype=float)

    if not np.isfinite(train_x.to_numpy()).all() or not np.isfinite(target).all():
        return None, ["non_finite_training_matrix"]
    if not np.isfinite(calibration_x.to_numpy()).all():
        return None, ["non_finite_calibration_matrix"]
    if not np.isfinite(test_x.to_numpy()).all():
        return None, ["non_finite_test_matrix"]

    return (
        PreparedFold(
            feature_columns=tuple(feature_columns),
            train_x=train_x,
            train_y=train_y,
            calibration_x=calibration_x,
            test_x=test_x,
            fit_rows=tuple(fit_rows),
            calibration_rows=tuple(calibration_rows),
            test_rows=tuple(dict(row) for row in test_rows),
            test_information_cutoff_utc=test_information_cutoff,
            calibration_information_cutoff_utc=calibration_information_cutoff,
        ),
        [],
    )


def _select_calibration_threshold(
    *,
    rows: Sequence[Mapping[str, Any]],
    scores: np.ndarray,
    stress_bps: float,
) -> dict[str, Any]:
    baseline = _economic_metrics(rows, stress_bps)
    candidates: list[dict[str, Any]] = []
    for quantile in SCORE_QUANTILES:
        threshold = float(np.quantile(scores, quantile))
        selected = [
            row
            for row, score in zip(rows, scores, strict=True)
            if score >= threshold
        ]
        metrics = _economic_metrics(selected, stress_bps)
        delta_net_pnl = metrics["net_pnl"] - baseline["net_pnl"]
        expectancy_uplift = metrics["expectancy"] - baseline["expectancy"]
        passes = (
            metrics["trade_count"] >= MIN_CALIBRATION_SELECTED_TRADES
            and metrics["net_pnl"] > 0
            and metrics["expectancy"] > 0
            and metrics["profit_factor"] is not None
            and metrics["profit_factor"] >= MIN_TREATMENT_PROFIT_FACTOR
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
    baseline_public = {
        "trade_count": baseline["trade_count"],
        "stressed_net_pnl": baseline["net_pnl"],
        "stressed_expectancy": baseline["expectancy"],
        "stressed_profit_factor": baseline["profit_factor"],
    }
    if not passing:
        return {
            "status": "blocked",
            "reason": "no_economic_uplift_calibration_threshold",
            "threshold": None,
            "selected_quantile": None,
            "baseline": baseline_public,
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
        "reason": "economic_uplift_threshold_selected_on_past_calibration_only",
        "threshold": winner["threshold"],
        "selected_quantile": winner["quantile"],
        "selected_trade_count": winner["selected_trade_count"],
        "stressed_net_pnl": winner["stressed_net_pnl"],
        "stressed_expectancy": winner["stressed_expectancy"],
        "stressed_profit_factor": winner["stressed_profit_factor"],
        "delta_stressed_net_pnl": winner["delta_stressed_net_pnl"],
        "delta_stressed_expectancy": winner["delta_stressed_expectancy"],
        "baseline": baseline_public,
        "candidates": candidates,
    }


@contextmanager
def _native_qlib_lgb_predictor_context() -> Iterator[tuple[Predictor, dict[str, Any]]]:
    qlib = importlib.import_module("qlib")
    gbdt_module = importlib.import_module("qlib.contrib.model.gbdt")
    workflow_module = importlib.import_module("qlib.workflow")
    lgb_model_class = getattr(gbdt_module, "LGBModel", None)
    recorder = getattr(workflow_module, "R", None)
    if lgb_model_class is None:
        raise RuntimeError("qlib_contrib_lgbmodel_missing")
    if recorder is None:
        raise RuntimeError("qlib_workflow_recorder_missing")
    if not str(getattr(lgb_model_class, "__module__", "")).startswith("qlib.contrib."):
        raise RuntimeError("model_not_from_qlib_contrib")

    lightgbm = importlib.import_module("lightgbm")
    previous_pickle = os.environ.get("MLFLOW_ALLOW_PICKLE_DESERIALIZATION")
    previous_tracking = os.environ.get("MLFLOW_TRACKING_URI")
    os.environ["MLFLOW_ALLOW_PICKLE_DESERIALIZATION"] = "false"

    with tempfile.TemporaryDirectory(prefix="futuros-qlib-market-context-") as temp_dir:
        temp_root = Path(temp_dir)
        provider_dir = temp_root / "provider"
        provider_dir.mkdir(parents=True, exist_ok=True)
        mlflow_dir = temp_root / "mlruns"
        mlflow_dir.mkdir(parents=True, exist_ok=True)
        mlflow_uri = mlflow_dir.as_uri()
        os.environ["MLFLOW_TRACKING_URI"] = mlflow_uri
        qlib.init(provider_uri=str(provider_dir), region="us", clear_mem_cache=True)

        def predict(
            train_x: pd.DataFrame,
            train_y: pd.Series,
            calibration_x: pd.DataFrame,
            test_x: pd.DataFrame,
            *,
            fold_id: str,
        ) -> tuple[np.ndarray, np.ndarray]:
            validation_count = max(
                MIN_MODEL_VALIDATION_TRADES,
                math.floor(len(train_x) * MODEL_VALIDATION_FRACTION),
            )
            model_train_count = len(train_x) - validation_count
            if model_train_count < MIN_MODEL_TRAIN_TRADES:
                raise RuntimeError(
                    "native_lgb_internal_train_partition_not_met:"
                    f"{model_train_count}:{validation_count}"
                )

            dataset = _InMemoryQlibDataset(
                train_x=train_x.iloc[:model_train_count].reset_index(drop=True),
                train_y=train_y.iloc[:model_train_count].reset_index(drop=True),
                model_valid_x=train_x.iloc[model_train_count:].reset_index(drop=True),
                model_valid_y=train_y.iloc[model_train_count:].reset_index(drop=True),
                calibration_x=calibration_x,
                test_x=test_x,
                fold_id=fold_id,
            )
            model = lgb_model_class(
                loss="mse",
                early_stopping_rounds=20,
                num_boost_round=160,
                learning_rate=0.03,
                max_depth=3,
                num_leaves=15,
                min_data_in_leaf=25,
                feature_fraction=0.80,
                bagging_fraction=0.85,
                bagging_freq=1,
                lambda_l1=0.10,
                lambda_l2=0.50,
                seed=RANDOM_SEED,
                bagging_seed=RANDOM_SEED,
                feature_fraction_seed=RANDOM_SEED,
                data_random_seed=RANDOM_SEED,
                deterministic=True,
                force_col_wise=True,
                num_threads=1,
            )
            with recorder.start(
                experiment_name="futuros_market_context_economic_research",
                recorder_name=f"{fold_id}_lgb",
                uri=mlflow_uri,
            ):
                model.fit(dataset, verbose_eval=0)
            calibration_prediction = model.predict(dataset, segment="calibration")
            test_prediction = model.predict(dataset, segment="test")
            return (
                np.asarray(calibration_prediction.to_numpy(), dtype=float).reshape(-1),
                np.asarray(test_prediction.to_numpy(), dtype=float).reshape(-1),
            )

        metadata = {
            "framework": "Microsoft Qlib",
            "qlib_version": str(getattr(qlib, "__version__", "unknown")),
            "lightgbm_version": str(getattr(lightgbm, "__version__", "unknown")),
            "module": str(getattr(lgb_model_class, "__module__", "")),
            "class": str(getattr(lgb_model_class, "__name__", "")),
            "loss": "mse",
            "target": "stressed_net_pnl_per_notional_bps",
            "num_boost_round": 160,
            "max_depth": 3,
            "num_leaves": 15,
            "min_data_in_leaf": 25,
            "model_validation_fraction": MODEL_VALIDATION_FRACTION,
            "min_model_validation_trades": MIN_MODEL_VALIDATION_TRADES,
            "min_model_train_trades": MIN_MODEL_TRAIN_TRADES,
            "calibration_labels_used_for_model_fit_or_early_stopping": False,
            "fallback_used": False,
            "provider_mode": "local_temporary_offline",
            "network_required": False,
            "mlflow_mode": "temporary_local_research_only",
            "mlflow_pickle_deserialization": False,
        }
        try:
            yield predict, metadata
        finally:
            if previous_pickle is None:
                os.environ.pop("MLFLOW_ALLOW_PICKLE_DESERIALIZATION", None)
            else:
                os.environ["MLFLOW_ALLOW_PICKLE_DESERIALIZATION"] = previous_pickle
            if previous_tracking is None:
                os.environ.pop("MLFLOW_TRACKING_URI", None)
            else:
                os.environ["MLFLOW_TRACKING_URI"] = previous_tracking


class _InMemoryQlibDataset:
    def __init__(
        self,
        *,
        train_x: pd.DataFrame,
        train_y: pd.Series,
        model_valid_x: pd.DataFrame,
        model_valid_y: pd.Series,
        calibration_x: pd.DataFrame,
        test_x: pd.DataFrame,
        fold_id: str,
    ) -> None:
        # LGBModel inspects ``segments`` to decide which labeled datasets enter fit
        # and early stopping. Internal validation is carved only from the causally
        # available fit window. Economic calibration remains a separate unlabeled
        # segment and therefore cannot influence boosting rounds or fitted weights.
        self.segments = {"train": "train", "valid": "valid"}
        self._frames = {
            "train": _qlib_frame(train_x, train_y, fold_id=fold_id, segment="train"),
            "valid": _qlib_frame(
                model_valid_x,
                model_valid_y,
                fold_id=fold_id,
                segment="valid",
            ),
            "calibration": _qlib_frame(
                calibration_x,
                None,
                fold_id=fold_id,
                segment="calibration",
            ),
            "test": _qlib_frame(test_x, None, fold_id=fold_id, segment="test"),
        }

    def prepare(self, segment: Any, col_set: Any = None, data_key: Any = None) -> Any:
        del data_key
        if isinstance(segment, (list, tuple)):
            return [self.prepare(item, col_set=col_set) for item in segment]
        key = str(segment)
        if key not in self._frames:
            raise KeyError(key)
        frame = self._frames[key].copy()
        if col_set is None:
            return frame
        if isinstance(col_set, str):
            return frame[col_set]
        requested = set(col_set)
        mask = [column[0] in requested for column in frame.columns]
        return frame.loc[:, mask]


def _qlib_frame(
    features: pd.DataFrame,
    labels: pd.Series | None,
    *,
    fold_id: str,
    segment: str,
) -> pd.DataFrame:
    count = len(features)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    dates = [base + timedelta(seconds=index) for index in range(count)]
    instruments = [f"{fold_id}_{segment}_{index:06d}" for index in range(count)]
    index = pd.MultiIndex.from_arrays(
        [dates, instruments],
        names=["datetime", "instrument"],
    )
    values = features.to_numpy(dtype=float)
    columns = [("feature", str(column)) for column in features.columns]
    label_values = (
        labels.to_numpy(dtype=float).reshape(-1, 1)
        if labels is not None
        else np.zeros((count, 1), dtype=float)
    )
    combined = np.column_stack([values, label_values])
    multi_columns = pd.MultiIndex.from_tuples(columns + [("label", "LABEL0")])
    return pd.DataFrame(combined, index=index, columns=multi_columns)


def _rematerialize_market_features(raw_market: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "tf", "ts", "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(raw_market.columns))
    if missing:
        raise ValueError(f"market_raw_columns_missing:{missing}")
    market = raw_market.copy()
    market["tf"] = market["tf"].astype(str).str.casefold()
    market = market.loc[market["tf"].eq(TIMEFRAME)].copy()
    if market.empty:
        raise ValueError("no_5m_market_rows")
    features = build_market_feature_frame(market)
    features = features.loc[features["tf"].astype(str).str.casefold().eq(TIMEFRAME)].copy()
    features["symbol"] = features["symbol"].map(internal_symbol)
    features["ts"] = pd.to_datetime(features["ts"], utc=True, errors="coerce")
    features["available_at_utc"] = features["ts"] + pd.Timedelta(seconds=TIMEFRAME_SECONDS)
    return features.dropna(subset=["symbol", "ts", "available_at_utc"]).sort_values(
        ["symbol", "available_at_utc"],
        kind="mergesort",
    )


def _align_point_in_time_market_features(
    outcomes: Sequence[Mapping[str, Any]],
    market: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Vectorized point-in-time join from closed 5m candles to trade entries."""

    aligned = [dict(row) for row in outcomes]
    if not aligned:
        return [], {
            "status": "blocked",
            "timeframe": TIMEFRAME,
            "input_trade_count": 0,
            "ready_trade_count": 0,
            "coverage": 0.0,
            "target_notional_coverage": 0.0,
        }

    left = pd.DataFrame(
        {
            "source_index": np.arange(len(aligned), dtype=int),
            "symbol": [str(row["__symbol"]) for row in aligned],
            "open_time_utc": [row["__open_time"] for row in aligned],
        }
    )
    joined_parts: list[pd.DataFrame] = []
    right_columns = [
        "symbol",
        "ts",
        "available_at_utc",
        *MARKET_SOURCE_COLUMNS,
    ]
    for symbol, left_group in left.groupby("symbol", sort=False):
        right = market.loc[market["symbol"].eq(symbol), right_columns].copy()
        if right.empty:
            missing = left_group.copy()
            missing["ts"] = pd.NaT
            missing["available_at_utc"] = pd.NaT
            for column in MARKET_SOURCE_COLUMNS:
                missing[column] = np.nan
            joined_parts.append(missing)
            continue
        merged = pd.merge_asof(
            left_group.sort_values("open_time_utc", kind="mergesort"),
            right.drop(columns=["symbol"]).sort_values(
                "available_at_utc", kind="mergesort"
            ),
            left_on="open_time_utc",
            right_on="available_at_utc",
            direction="backward",
            allow_exact_matches=True,
        )
        joined_parts.append(merged)

    joined = (
        pd.concat(joined_parts, ignore_index=True, sort=False)
        .sort_values("source_index", kind="mergesort")
        .reset_index(drop=True)
    )

    ready_count = 0
    missing_feature_count = 0
    stale_feature_count = 0
    notional_ready_count = 0
    for record in joined.to_dict(orient="records"):
        index = int(record["source_index"])
        row = aligned[index]
        available_at = record.get("available_at_utc")
        feature_ts = record.get("ts")
        if pd.isna(available_at) or pd.isna(feature_ts):
            row["__market_ready"] = False
            missing_feature_count += 1
            continue
        age_seconds = float(
            (row["__open_time"] - pd.Timestamp(available_at).to_pydatetime()).total_seconds()
        )
        if age_seconds < 0 or age_seconds >= TIMEFRAME_SECONDS:
            row["__market_ready"] = False
            stale_feature_count += 1
            continue
        if any(pd.isna(record.get(column)) for column in PIT_REQUIRED_MARKET_COLUMNS):
            row["__market_ready"] = False
            missing_feature_count += 1
            continue

        row["__market_ready"] = True
        row["__market_feature_ts"] = pd.Timestamp(feature_ts).to_pydatetime()
        row["__market_feature_available_at"] = pd.Timestamp(available_at).to_pydatetime()
        row["__market_feature_age_seconds"] = age_seconds
        row["__market"] = {
            column: record.get(column)
            for column in MARKET_SOURCE_COLUMNS
        }
        ready_count += 1
        if _notional(row) is not None:
            notional_ready_count += 1

    ready = [row for row in aligned if row.get("__market_ready") is True]
    count = len(aligned)
    coverage = ready_count / count if count else 0.0
    target_notional_coverage = notional_ready_count / ready_count if ready_count else 0.0
    return ready, {
        "status": "ok" if ready_count else "blocked",
        "timeframe": TIMEFRAME,
        "timestamp_semantics": "candle_open",
        "available_at_rule": "candle_ts_plus_5_minutes_lte_trade_open",
        "maximum_feature_age_seconds_exclusive": TIMEFRAME_SECONDS,
        "forward_fill_across_gaps": False,
        "same_candle_lookahead_allowed": False,
        "input_trade_count": count,
        "ready_trade_count": ready_count,
        "missing_feature_count": missing_feature_count,
        "stale_or_gap_feature_count": stale_feature_count,
        "coverage": round(coverage, 10),
        "target_notional_coverage": round(target_notional_coverage, 10),
        "required_market_columns": list(PIT_REQUIRED_MARKET_COLUMNS),
    }


def _model_feature_row(row: Mapping[str, Any]) -> dict[str, float | None]:
    market = row.get("__market")
    if not isinstance(market, Mapping):
        return {}
    side_sign = 1.0 if str(row.get("side", "")).lower() == "long" else -1.0
    side_long = 1.0 if side_sign > 0 else 0.0
    symbol_btc = 1.0 if str(row.get("__symbol")) == "BTCUSDT" else 0.0
    close = _numeric(market.get("close"))
    trend_score = _numeric(market.get("trend_score"))
    trend_scaled = None if trend_score is None else trend_score / 5.0
    regime = str(market.get("market_regime") or "").lower()
    macd_line_pct = _ratio(_numeric(market.get("macd_line")), close)
    macd_signal_pct = _ratio(_numeric(market.get("macd_signal")), close)
    macd_hist_pct = _ratio(_numeric(market.get("macd_hist")), close)
    ret_5 = _numeric(market.get("ret_5"))
    ret_30 = _numeric(market.get("ret_30"))
    dist_ema20 = _numeric(market.get("dist_ema20"))

    open_time = row.get("__open_time")
    if isinstance(open_time, datetime):
        seconds_of_day = open_time.hour * 3600 + open_time.minute * 60 + open_time.second
        day_angle = 2.0 * math.pi * seconds_of_day / 86400.0
        weekday_angle = 2.0 * math.pi * open_time.weekday() / 7.0
        hour_sin = math.sin(day_angle)
        hour_cos = math.cos(day_angle)
        weekday_sin = math.sin(weekday_angle)
        weekday_cos = math.cos(weekday_angle)
    else:
        hour_sin = hour_cos = weekday_sin = weekday_cos = None

    return {
        "feature_side_long": side_long,
        "feature_symbol_btcusdt": symbol_btc,
        "feature_ret_1": _numeric(market.get("ret_1")),
        "feature_ret_3": _numeric(market.get("ret_3")),
        "feature_ret_5": ret_5,
        "feature_ret_10": _numeric(market.get("ret_10")),
        "feature_ret_15": _numeric(market.get("ret_15")),
        "feature_ret_30": ret_30,
        "feature_dist_ema20": dist_ema20,
        "feature_dist_ema50": _numeric(market.get("dist_ema50")),
        "feature_dist_ema200": _numeric(market.get("dist_ema200")),
        "feature_rsi_14_scaled": _scale(_numeric(market.get("rsi_14")), 100.0),
        "feature_macd_line_pct": macd_line_pct,
        "feature_macd_signal_pct": macd_signal_pct,
        "feature_macd_hist_pct": macd_hist_pct,
        "feature_atr_pct_14": _numeric(market.get("atr_pct_14")),
        "feature_vol_30": _numeric(market.get("vol_30")),
        "feature_vol_120": _numeric(market.get("vol_120")),
        "feature_volume_rel_30": _numeric(market.get("volume_rel_30")),
        "feature_volume_z_30": _numeric(market.get("volume_z_30")),
        "feature_hl_range": _numeric(market.get("hl_range")),
        "feature_body_range": _numeric(market.get("body_range")),
        "feature_upper_wick": _numeric(market.get("upper_wick")),
        "feature_lower_wick": _numeric(market.get("lower_wick")),
        "feature_trend_score_scaled": trend_scaled,
        "feature_regime_trend_up": 1.0 if regime.startswith("trend_up") else 0.0,
        "feature_regime_trend_down": 1.0 if regime.startswith("trend_down") else 0.0,
        "feature_regime_high_vol": 1.0 if regime.endswith("high_vol") else 0.0,
        "feature_trend_alignment": _multiply(trend_scaled, side_sign),
        "feature_ret_5_side": _multiply(ret_5, side_sign),
        "feature_ret_30_side": _multiply(ret_30, side_sign),
        "feature_dist_ema20_side": _multiply(dist_ema20, side_sign),
        "feature_macd_hist_pct_side": _multiply(macd_hist_pct, side_sign),
        "feature_hour_sin": hour_sin,
        "feature_hour_cos": hour_cos,
        "feature_weekday_sin": weekday_sin,
        "feature_weekday_cos": weekday_cos,
    }


def _normalize_outcomes(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    normalized: list[dict[str, Any]] = []
    invalid_time_count = 0
    for raw in rows:
        row = dict(raw)
        if row.get("is_closed") is False:
            continue
        pnl = _numeric(row.get("net_pnl"))
        if pnl is None or not math.isfinite(pnl):
            continue
        open_time = _parse_time(row.get("open_time_utc") or row.get("open_time"))
        close_time = _parse_time(row.get("close_time_utc") or row.get("close_time"))
        if open_time is None or close_time is None or close_time <= open_time:
            invalid_time_count += 1
            continue
        symbol = internal_symbol(str(row.get("symbol_norm") or row.get("symbol") or ""))
        if not symbol:
            continue
        row["net_pnl"] = pnl
        row["__open_time"] = open_time
        row["__close_time"] = close_time
        row["__symbol"] = symbol
        normalized.append(row)
    normalized.sort(key=lambda item: item["__close_time"])
    return normalized, invalid_time_count


def _build_fold_specs(count: int) -> list[FoldSpec]:
    if count <= 0:
        return []
    context_count = max(
        MIN_INITIAL_CONTEXT_TRADES,
        math.floor(count * INITIAL_CONTEXT_FRACTION),
    )
    if context_count >= count:
        return []
    evaluation_count = count - context_count
    base_size, remainder = divmod(evaluation_count, FOLD_COUNT)
    specs: list[FoldSpec] = []
    start = context_count
    for index in range(FOLD_COUNT):
        size = base_size + (1 if index < remainder else 0)
        end = start + size
        specs.append(
            FoldSpec(
                fold_id=f"fold_{index + 1:02d}",
                test_start=start,
                test_end=end,
            )
        )
        start = end
    return specs


def _causal_boundary_valid(prepared: PreparedFold) -> bool:
    embargo = timedelta(seconds=OUTCOME_AVAILABILITY_EMBARGO_SECONDS)
    fit_close_max = max(row["__close_time"] for row in prepared.fit_rows)
    calibration_open_min = min(row["__open_time"] for row in prepared.calibration_rows)
    calibration_close_max = max(row["__close_time"] for row in prepared.calibration_rows)
    test_open_min = min(row["__open_time"] for row in prepared.test_rows)
    return bool(
        fit_close_max <= calibration_open_min - embargo
        and calibration_close_max <= test_open_min - embargo
    )


def _economic_metrics(
    rows: Sequence[Mapping[str, Any]],
    stress_bps: float,
) -> dict[str, Any]:
    pnls = [_stressed_pnl(row, stress_bps) for row in rows]
    net = float(sum(pnls))
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = abs(sum(value for value in pnls if value < 0))
    if not pnls:
        profit_factor: float | None = None
    elif gross_loss == 0:
        profit_factor = math.inf if gross_profit > 0 else None
    else:
        profit_factor = gross_profit / gross_loss
    return {
        "trade_count": len(pnls),
        "net_pnl": round(net, 10),
        "expectancy": round(net / len(pnls), 10) if pnls else 0.0,
        "profit_factor": None if profit_factor is None else round(profit_factor, 10),
    }


def _stressed_return_bps(row: Mapping[str, Any], stress_bps: float) -> float:
    notional = _notional(row)
    if notional is None or notional <= 0:
        raise ValueError("target_notional_missing")
    return _stressed_pnl(row, stress_bps) / notional * 10_000.0


def _stressed_pnl(row: Mapping[str, Any], stress_bps: float) -> float:
    pnl = float(row["net_pnl"])
    notional = _notional(row)
    stress_cost = 0.0 if notional is None else notional * stress_bps / 10_000.0
    return pnl - stress_cost


def _notional(row: Mapping[str, Any]) -> float | None:
    direct = _numeric(row.get("notional"))
    if direct is not None and direct > 0:
        return direct
    entry = _numeric(row.get("entry_price"))
    quantity = _numeric(row.get("quantity"))
    if entry is None or quantity is None:
        return None
    product = abs(entry * quantity)
    return product if product > 0 and math.isfinite(product) else None


def _finite_scores(values: Any, *, expected: int) -> np.ndarray:
    array = np.asarray(values, dtype=float).reshape(-1)
    if len(array) != expected:
        raise ValueError(f"prediction_length_mismatch:{len(array)}:{expected}")
    if not np.isfinite(array).all():
        raise ValueError("non_finite_predictions")
    return array


def _market_frame(
    market_rows: Sequence[Mapping[str, Any]] | pd.DataFrame | None,
    path: Path,
) -> pd.DataFrame:
    if isinstance(market_rows, pd.DataFrame):
        return market_rows.copy()
    if market_rows is not None:
        return pd.DataFrame([dict(row) for row in market_rows])
    if not path.exists() or not path.is_file():
        return pd.DataFrame()
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"unsupported_market_format:{path.suffix.lower()}")


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    if path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    else:
        raise ValueError(f"unsupported_outcome_format:{path.suffix.lower()}")
    return frame.to_dict(orient="records")


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _scale(value: float | None, divisor: float) -> float | None:
    return None if value is None else value / divisor


def _multiply(value: float | None, multiplier: float) -> float | None:
    return None if value is None else value * multiplier


def _validate_stress_bps(value: float) -> float:
    stress = float(value)
    if not math.isfinite(stress) or stress < 0:
        raise ValueError("additional_execution_stress_bps_must_be_finite_and_non_negative")
    return stress


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not str(key).startswith("__")}


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


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
    return {
        "schema_version": SCHEMA_VERSION,
        "outcome_source_path": str(outcome_source),
        "market_features_source_path": str(market_source),
        "input_row_count": input_row_count,
        "valid_closed_outcome_count": valid_closed_outcome_count,
        "invalid_trade_time_count": invalid_time_count,
        "point_in_time_alignment": dict(alignment_report),
        "selector_field": SELECTOR_FIELD,
        "additional_execution_stress_bps": stress_bps,
        "target": "stressed_net_pnl_per_notional_bps",
        "selection_method": "causal_past_outcome_calibration_then_forward_apply",
        "outcome_availability_embargo_seconds": OUTCOME_AVAILABILITY_EMBARGO_SECONDS,
        "anti_leakage": True,
        "future_return_features_used": False,
        "realized_pnl_used_as_feature": False,
        "notional_used_as_predictive_feature": False,
        "leverage_used_as_predictive_feature": False,
        "fallback_allowed": False,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "promotion_allowed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "changes_risk": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "write_performed": False,
    }


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
