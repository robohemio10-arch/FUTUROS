"""Native Qlib economic challenger for closed Paper outcomes.

The module is research-only and fail-closed. It trains a real Qlib contrib
``LinearModel`` on strictly past Paper outcomes, calibrates an economic selection
threshold on a chronological validation slice, and applies the frozen threshold to
the next forward fold. The resulting out-of-sample selector is evaluated by the
canonical execution-cost walk-forward gate.

No fallback model is allowed. No runtime, registry, risk or order state is changed.
"""

from __future__ import annotations

import importlib
import math
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Protocol

import numpy as np
import pandas as pd

from .economic_walkforward_cost_robustness import (
    DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    FOLD_COUNT,
    INITIAL_CONTEXT_FRACTION,
    MIN_INITIAL_CONTEXT_TRADES,
    MIN_TREATMENT_PROFIT_FACTOR,
    build_paper_autolearning_economic_walkforward_cost_robustness_v1,
)
from .microbatch_builder import build_microbatch_row

SCHEMA_VERSION = "paper_autolearning_qlib_native_economic_challenger_v1"
DEFAULT_OUTCOME_PATH = Path("data/feedback/outcome_events.parquet")
SELECTOR_FIELD = "paper_candidate_filter_decision"
SCORE_FIELD = "qlib_native_economic_score"
THRESHOLD_FIELD = "qlib_native_economic_threshold"
FOLD_FIELD = "qlib_native_economic_fold"
OOS_FIELD = "qlib_native_economic_oos"

MIN_TOTAL_TRADES = 300
CALIBRATION_FRACTION = 0.25
MIN_FIT_TRADES = 90
MIN_CALIBRATION_TRADES = 30
MIN_CALIBRATION_SELECTED_TRADES = 10
TARGET_CLIP_LOWER_QUANTILE = 0.025
TARGET_CLIP_UPPER_QUANTILE = 0.975
SCORE_QUANTILES = (0.50, 0.60, 0.70, 0.80)
MIN_FEATURE_COUNT = 2

# Deliberately remove one dummy from each binary category to avoid exact collinearity.
APPROVED_FEATURES = (
    "feature_side_long",
    "feature_symbol_btcusdt",
    "feature_entry_price",
    "feature_quantity",
    "feature_notional",
    "feature_leverage",
    "feature_paper_candidate_filter_called",
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


def build_qlib_native_economic_challenger_v1(
    *,
    project_root: str | Path,
    rows: Sequence[Mapping[str, Any]] | None = None,
    outcome_path: str | Path | None = None,
    additional_execution_stress_bps: float = DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    predictor: Predictor | None = None,
) -> dict[str, Any]:
    """Build and economically evaluate a chronological Qlib challenger.

    Supplying ``predictor`` is supported only for deterministic unit tests. Production
    research execution must leave it unset so a real Qlib contrib model is used.
    """

    root = Path(project_root).resolve()
    source = _resolve(root, outcome_path or DEFAULT_OUTCOME_PATH)
    input_rows = [dict(row) for row in rows] if rows is not None else _read_rows(source)
    normalized, invalid_time_count = _normalize_rows(input_rows)
    stress_bps = _validate_stress_bps(additional_execution_stress_bps)

    common = {
        "schema_version": SCHEMA_VERSION,
        "source_path": str(source),
        "input_row_count": len(input_rows),
        "valid_closed_outcome_count": len(normalized),
        "invalid_close_time_count": invalid_time_count,
        "selector_field": SELECTOR_FIELD,
        "additional_execution_stress_bps": stress_bps,
        "target": "authoritative_net_pnl_minus_incremental_execution_stress",
        "selection_method": "chronological_validation_profit_gate_then_forward_apply",
        "anti_leakage": True,
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

    preflight_blockers: list[str] = []
    if len(normalized) < MIN_TOTAL_TRADES:
        preflight_blockers.append("min_total_trades_not_met")
    if invalid_time_count:
        preflight_blockers.append("invalid_close_time_detected")
    fold_specs = _build_fold_specs(len(normalized))
    if len(fold_specs) != FOLD_COUNT:
        preflight_blockers.append("walkforward_fold_count_not_met")

    if preflight_blockers:
        return {
            **common,
            "status": "blocked",
            "reason": sorted(set(preflight_blockers))[0],
            "decision": "MANTER_EM_RESEARCH",
            "predictor_mode": "not_started",
            "native_qlib_used": False,
            "model": {},
            "folds": [],
            "economic_evaluation": None,
            "blockers": sorted(set(preflight_blockers)),
        }

    if predictor is not None:
        return _run_with_predictor(
            common=common,
            normalized=normalized,
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
        with _native_qlib_predictor_context() as (native_predictor, metadata):
            return _run_with_predictor(
                common=common,
                normalized=normalized,
                fold_specs=fold_specs,
                stress_bps=stress_bps,
                predictor=native_predictor,
                predictor_mode="native_qlib_contrib_linear_ols",
                native_qlib_used=True,
                model_metadata=metadata,
            )
    except Exception as exc:
        return {
            **common,
            "status": "blocked",
            "reason": "native_qlib_unavailable_or_failed",
            "decision": "MANTER_EM_RESEARCH",
            "predictor_mode": "native_qlib_contrib_linear_ols",
            "native_qlib_used": False,
            "model": {
                "framework": "qlib",
                "class": "qlib.contrib.model.linear.LinearModel",
                "estimator": "ols",
                "fallback_used": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            "folds": [],
            "economic_evaluation": None,
            "blockers": ["native_qlib_unavailable_or_failed"],
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
        train_context = [dict(row) for row in normalized[: spec.test_start]]
        test_rows = [dict(row) for row in normalized[spec.test_start : spec.test_end]]
        prepared, preparation_blockers = _prepare_fold(
            train_context=train_context,
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
                    "train_context_trade_count": len(train_context),
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
            test_scores = _finite_scores(test_scores, expected=len(prepared.test_rows))
        except Exception as exc:
            reason = f"{spec.fold_id}:predictor_failed:{type(exc).__name__}"
            blockers.append(reason)
            fold_reports.append(
                {
                    "fold_id": spec.fold_id,
                    "status": "blocked",
                    "reason": reason,
                    "train_context_trade_count": len(train_context),
                    "test_trade_count": len(test_rows),
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
            reason = f"{spec.fold_id}:calibration_no_profitable_threshold"
            blockers.append(reason)
            fold_reports.append(
                {
                    "fold_id": spec.fold_id,
                    "status": "blocked",
                    "reason": reason,
                    "train_context_trade_count": len(train_context),
                    "fit_trade_count": len(prepared.fit_rows),
                    "calibration_trade_count": len(prepared.calibration_rows),
                    "test_trade_count": len(prepared.test_rows),
                    "feature_columns": list(prepared.feature_columns),
                    "selected_trade_count": 0,
                    "calibration": calibration,
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
                "reason": "threshold_calibrated_on_past_only",
                "train_context_trade_count": len(train_context),
                "fit_trade_count": len(prepared.fit_rows),
                "calibration_trade_count": len(prepared.calibration_rows),
                "test_trade_count": len(prepared.test_rows),
                "feature_columns": list(prepared.feature_columns),
                "selected_trade_count": selected_count,
                "calibration": calibration,
                "chronological_boundary_valid": _chronological_boundary_valid(
                    prepared.fit_rows,
                    prepared.calibration_rows,
                    prepared.test_rows,
                ),
                "blockers": [],
            }
        )

    evaluation = build_paper_autolearning_economic_walkforward_cost_robustness_v1(
        project_root=Path(str(common["source_path"])).parent,
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
        "QLIB_NATIVE_ECONOMIC_EDGE_RESEARCH_ONLY"
        if native_certified_edge
        else (
            "TEST_DOUBLE_ECONOMIC_PATH_VALIDATED"
            if economically_robust
            else "MANTER_EM_RESEARCH"
        )
    )
    return {
        **dict(common),
        "status": "ok" if economically_robust else "blocked",
        "reason": (
            "qlib_native_walkforward_economic_edge_research_only"
            if native_certified_edge
            else (
                "test_double_walkforward_economic_path_validated"
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
    train_context: Sequence[Mapping[str, Any]],
    test_rows: Sequence[Mapping[str, Any]],
    stress_bps: float,
) -> tuple[PreparedFold | None, list[str]]:
    blockers: list[str] = []
    if len(train_context) < MIN_INITIAL_CONTEXT_TRADES:
        blockers.append("min_train_context_not_met")
    if not test_rows:
        blockers.append("empty_test_fold")
    if blockers:
        return None, blockers

    calibration_count = max(
        MIN_CALIBRATION_TRADES,
        math.floor(len(train_context) * CALIBRATION_FRACTION),
    )
    fit_count = len(train_context) - calibration_count
    if fit_count < MIN_FIT_TRADES:
        blockers.append("min_fit_trades_not_met")
    if calibration_count < MIN_CALIBRATION_TRADES:
        blockers.append("min_calibration_trades_not_met")
    if blockers:
        return None, blockers

    fit_rows = tuple(dict(row) for row in train_context[:fit_count])
    calibration_rows = tuple(dict(row) for row in train_context[fit_count:])
    heldout_rows = tuple(dict(row) for row in test_rows)
    if not _chronological_boundary_valid(fit_rows, calibration_rows, heldout_rows):
        return None, ["chronological_boundary_invalid"]

    fit_features = [_feature_row(row) for row in fit_rows]
    calibration_features = [_feature_row(row) for row in calibration_rows]
    test_features = [_feature_row(row) for row in heldout_rows]

    feature_columns: list[str] = []
    medians: dict[str, float] = {}
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    for column in APPROVED_FEATURES:
        fit_values = [_numeric(row.get(column)) for row in fit_features]
        finite = [value for value in fit_values if value is not None and math.isfinite(value)]
        if not finite:
            continue
        median = float(np.median(np.asarray(finite, dtype=float)))
        imputed = np.asarray(
            [median if value is None or not math.isfinite(value) else value for value in fit_values],
            dtype=float,
        )
        mean = float(np.mean(imputed))
        scale = float(np.std(imputed))
        if not math.isfinite(scale) or scale <= 1e-12:
            continue
        feature_columns.append(column)
        medians[column] = median
        means[column] = mean
        scales[column] = scale

    if len(feature_columns) < MIN_FEATURE_COUNT:
        return None, ["min_feature_count_not_met"]

    def matrix(feature_rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
        data: dict[str, list[float]] = {}
        for column in feature_columns:
            values: list[float] = []
            for row in feature_rows:
                raw = _numeric(row.get(column))
                value = medians[column] if raw is None or not math.isfinite(raw) else raw
                values.append((value - means[column]) / scales[column])
            data[column] = values
        return pd.DataFrame(data, columns=feature_columns, dtype=float)

    train_x = matrix(fit_features)
    calibration_x = matrix(calibration_features)
    test_x = matrix(test_features)
    target = np.asarray([_stressed_pnl(row, stress_bps) for row in fit_rows], dtype=float)
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
            fit_rows=fit_rows,
            calibration_rows=calibration_rows,
            test_rows=heldout_rows,
        ),
        [],
    )


def _select_calibration_threshold(
    *,
    rows: Sequence[Mapping[str, Any]],
    scores: np.ndarray,
    stress_bps: float,
) -> dict[str, Any]:
    """Select a past-only threshold that improves economic outcome.

    Calibration is deliberately aligned with the canonical treatment semantics:
    blocked trades contribute zero PnL and zero capital. A candidate threshold
    therefore must improve total stressed Net PnL and expectancy versus executing
    every calibration trade, not merely produce a profitable selected subset.

    The unrounded threshold is retained for membership decisions. Rounding a
    quantile boundary before application can change membership when scores are
    discrete or tied, which would violate calibration/test parity.
    """

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
def _native_qlib_predictor_context() -> Iterator[tuple[Predictor, dict[str, Any]]]:
    qlib = importlib.import_module("qlib")
    linear_module = importlib.import_module("qlib.contrib.model.linear")
    linear_model_class = getattr(linear_module, "LinearModel", None)
    if linear_model_class is None:
        raise RuntimeError("qlib_contrib_linear_model_missing")
    if not str(getattr(linear_model_class, "__module__", "")).startswith("qlib.contrib."):
        raise RuntimeError("model_not_from_qlib_contrib")

    previous_pickle = os.environ.get("MLFLOW_ALLOW_PICKLE_DESERIALIZATION")
    previous_tracking = os.environ.get("MLFLOW_TRACKING_URI")
    os.environ["MLFLOW_ALLOW_PICKLE_DESERIALIZATION"] = "false"

    with tempfile.TemporaryDirectory(prefix="futuros-qlib-native-economic-") as temp_dir:
        temp_root = Path(temp_dir)
        provider_dir = temp_root / "provider"
        provider_dir.mkdir(parents=True, exist_ok=True)
        mlflow_dir = temp_root / "mlruns"
        mlflow_dir.mkdir(parents=True, exist_ok=True)
        os.environ["MLFLOW_TRACKING_URI"] = mlflow_dir.as_uri()
        qlib.init(provider_uri=str(provider_dir), region="us", clear_mem_cache=True)

        def predict(
            train_x: pd.DataFrame,
            train_y: pd.Series,
            calibration_x: pd.DataFrame,
            test_x: pd.DataFrame,
            *,
            fold_id: str,
        ) -> tuple[np.ndarray, np.ndarray]:
            dataset = _InMemoryQlibDataset(
                train_x=train_x,
                train_y=train_y,
                calibration_x=calibration_x,
                test_x=test_x,
                fold_id=fold_id,
            )
            model = linear_model_class(estimator="ols", fit_intercept=True)
            model.fit(dataset)
            calibration_prediction = model.predict(dataset, segment="valid")
            test_prediction = model.predict(dataset, segment="test")
            return (
                np.asarray(calibration_prediction.to_numpy(), dtype=float).reshape(-1),
                np.asarray(test_prediction.to_numpy(), dtype=float).reshape(-1),
            )

        metadata = {
            "framework": "Microsoft Qlib",
            "qlib_version": str(getattr(qlib, "__version__", "unknown")),
            "module": str(getattr(linear_model_class, "__module__", "")),
            "class": str(getattr(linear_model_class, "__name__", "")),
            "estimator": "ols",
            "fit_intercept": True,
            "fallback_used": False,
            "provider_mode": "local_temporary_offline",
            "network_required": False,
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
        calibration_x: pd.DataFrame,
        test_x: pd.DataFrame,
        fold_id: str,
    ) -> None:
        self._frames = {
            "train": _qlib_frame(train_x, train_y, fold_id=fold_id, segment="train"),
            "valid": _qlib_frame(
                calibration_x,
                None,
                fold_id=fold_id,
                segment="valid",
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
    index = pd.MultiIndex.from_arrays([dates, instruments], names=["datetime", "instrument"])
    values = features.to_numpy(dtype=float)
    columns = [("feature", str(column)) for column in features.columns]
    if labels is not None:
        label_values = labels.to_numpy(dtype=float).reshape(-1, 1)
    else:
        label_values = np.zeros((count, 1), dtype=float)
    combined = np.column_stack([values, label_values])
    multi_columns = pd.MultiIndex.from_tuples(columns + [("label", "LABEL0")])
    return pd.DataFrame(combined, index=index, columns=multi_columns)


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
        specs.append(FoldSpec(fold_id=f"fold_{index + 1:02d}", test_start=start, test_end=end))
        start = end
    return specs


def _feature_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return build_microbatch_row(row)


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


def _normalize_rows(
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
        close_time = _parse_time(row.get("close_time_utc") or row.get("close_time"))
        if close_time is None:
            invalid_time_count += 1
            continue
        row["net_pnl"] = pnl
        row["__qlib_close_time"] = close_time
        normalized.append(row)
    normalized.sort(key=lambda item: item["__qlib_close_time"])
    return normalized, invalid_time_count


def _chronological_boundary_valid(
    fit_rows: Sequence[Mapping[str, Any]],
    calibration_rows: Sequence[Mapping[str, Any]],
    test_rows: Sequence[Mapping[str, Any]],
) -> bool:
    if not fit_rows or not calibration_rows or not test_rows:
        return False
    fit_end = fit_rows[-1].get("__qlib_close_time")
    calibration_start = calibration_rows[0].get("__qlib_close_time")
    calibration_end = calibration_rows[-1].get("__qlib_close_time")
    test_start = test_rows[0].get("__qlib_close_time")
    return bool(
        isinstance(fit_end, datetime)
        and isinstance(calibration_start, datetime)
        and isinstance(calibration_end, datetime)
        and isinstance(test_start, datetime)
        and fit_end <= calibration_start
        and calibration_end <= test_start
    )


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not str(key).startswith("__qlib_")}


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


def _validate_stress_bps(value: float) -> float:
    stress = float(value)
    if not math.isfinite(stress) or stress < 0:
        raise ValueError("additional_execution_stress_bps_must_be_finite_and_non_negative")
    return stress


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()
