"""Research-only point-in-time PnL ablation for market-intelligence feature blocks."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from smartcrypto.learning.feature_contracts.dataset_manifest import frame_hash
from smartcrypto.learning.walkforward.purged_split_engine import (
    build_walkforward_splits,
    public_split,
)

SCHEMA_VERSION = "market_intelligence_pnl_ablation_v1"
DATASET_FILE = "official_trades_master_qlib_dataset_v1.parquet"
FEATURE_CONTRACT_FILE = "official_trades_master_qlib_feature_contract_v1.json"
DATASET_MANIFEST_FILE = "official_trades_master_qlib_dataset_manifest_v1.json"
SPLIT_MANIFEST_FILE = "official_trades_master_qlib_split_manifest_v1.json"

TARGET_COLUMN = "label_economic_net_pnl"
MIN_CALIBRATION_SELECTED = 20
THRESHOLD_QUANTILES = (0.0, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80)
RANDOM_SEED = 42

FEATURE_BLOCKS: dict[str, tuple[str, ...]] = {
    "structural_context": (
        "feature_side_long",
        "feature_side_short",
        "feature_symbol_btcusdt",
        "feature_symbol_ethusdt",
        "feature_open_hour_sin",
        "feature_open_hour_cos",
        "feature_open_dow_sin",
        "feature_open_dow_cos",
    ),
    "momentum_trend": (
        "feature_ret_close_1m",
        "feature_ret_close_5m",
        "feature_ret_close_10m",
        "feature_ret_close_30m",
        "feature_dist_sma_20_pct",
        "feature_rsi_14",
    ),
    "volatility_range": (
        "feature_high_low_range_pct_5m",
        "feature_high_low_range_pct_10m",
        "feature_high_low_range_pct_30m",
        "feature_pre_entry_volatility_20",
    ),
    "volume_activity": (
        "feature_volume_sum_5m",
        "feature_volume_sum_10m",
        "feature_volume_sum_30m",
    ),
}

EXPECTED_FEATURES = tuple(
    feature
    for block in FEATURE_BLOCKS.values()
    for feature in block
)

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_leverage": False,
    "changes_stake": False,
    "changes_strategy": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_data": False,
}


class AblationError(RuntimeError):
    """Fail-closed validation error for the research ablation."""


class Regressor(Protocol):
    def fit(self, x: Any, y: Any) -> Any: ...
    def predict(self, x: Any) -> Any: ...


ModelFactory = Callable[[], Regressor]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AblationError(f"invalid_json:{path.name}") from exc
    if not isinstance(value, dict):
        raise AblationError(f"json_object_required:{path.name}")
    return value


def _resolve_path(root: Path, explicit: str | Path | None, filename: str) -> Path:
    if explicit is not None:
        candidate = Path(explicit)
        path = candidate if candidate.is_absolute() else root / candidate
        path = path.resolve()
        if not path.is_file():
            raise AblationError(f"artifact_not_found:{filename}:{path}")
        return path

    direct = (root / filename).resolve()
    if direct.is_file():
        return direct

    data_root = root / "data"
    search_root = data_root if data_root.is_dir() else root
    matches = sorted(
        {path.resolve() for path in search_root.rglob(filename) if path.is_file()},
        key=str,
    )
    if not matches:
        raise AblationError(f"artifact_not_found:{filename}")
    if len(matches) != 1:
        raise AblationError(
            f"artifact_ambiguous:{filename}:count={len(matches)}"
        )
    return matches[0]


def _default_model_factory() -> Regressor:
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
    except ImportError as exc:
        raise AblationError("scikit_learn_unavailable") from exc

    return HistGradientBoostingRegressor(
        loss="squared_error",
        learning_rate=0.05,
        max_iter=160,
        max_leaf_nodes=15,
        min_samples_leaf=20,
        l2_regularization=1.0,
        random_state=RANDOM_SEED,
    )


def _validate_feature_partition(feature_columns: Sequence[str]) -> None:
    observed = tuple(str(value) for value in feature_columns)
    if len(observed) != len(set(observed)):
        raise AblationError("feature_contract_contains_duplicates")
    if set(observed) != set(EXPECTED_FEATURES):
        missing = sorted(set(EXPECTED_FEATURES) - set(observed))
        unexpected = sorted(set(observed) - set(EXPECTED_FEATURES))
        raise AblationError(
            "feature_contract_block_partition_mismatch:"
            f"missing={missing}:unexpected={unexpected}"
        )
    flattened = [feature for values in FEATURE_BLOCKS.values() for feature in values]
    if len(flattened) != len(set(flattened)):
        raise AblationError("feature_block_overlap_detected")


def _load_bundle(
    *,
    project_root: str | Path,
    dataset_path: str | Path | None,
    feature_contract_path: str | Path | None,
    dataset_manifest_path: str | Path | None,
    split_manifest_path: str | Path | None,
) -> tuple[
    pd.DataFrame,
    tuple[str, ...],
    list[dict[str, Any]],
    dict[str, Any],
]:
    root = Path(project_root).resolve()
    dataset_file = _resolve_path(root, dataset_path, DATASET_FILE)
    feature_contract_file = _resolve_path(
        root, feature_contract_path, FEATURE_CONTRACT_FILE
    )
    dataset_manifest_file = _resolve_path(
        root, dataset_manifest_path, DATASET_MANIFEST_FILE
    )
    split_manifest_file = _resolve_path(
        root, split_manifest_path, SPLIT_MANIFEST_FILE
    )

    try:
        dataset = pd.read_parquet(dataset_file)
    except (OSError, ValueError, ImportError) as exc:
        raise AblationError("dataset_unreadable") from exc

    contract = _read_json(feature_contract_file)
    manifest = _read_json(dataset_manifest_file)
    split_manifest = _read_json(split_manifest_file)

    if contract.get("validation_status") != "ok":
        raise AblationError("feature_contract_not_validated")
    if manifest.get("validation_status") != "ok":
        raise AblationError("dataset_manifest_not_validated")
    if split_manifest.get("validation_status") != "ok":
        raise AblationError("split_manifest_not_validated")
    if split_manifest.get("leakage_status") != "ok":
        raise AblationError("split_manifest_leakage_blocked")

    feature_columns = tuple(
        str(value) for value in contract.get("feature_columns", [])
    )
    _validate_feature_partition(feature_columns)

    if TARGET_COLUMN not in dataset.columns:
        raise AblationError(f"dataset_missing_target:{TARGET_COLUMN}")
    missing = [column for column in feature_columns if column not in dataset.columns]
    if missing:
        raise AblationError("dataset_missing_features:" + ",".join(missing))

    if int(manifest.get("row_count", -1)) != len(dataset):
        raise AblationError("dataset_row_count_manifest_mismatch")
    if list(manifest.get("feature_order", [])) != list(feature_columns):
        raise AblationError("dataset_feature_order_manifest_mismatch")

    contract_hash = contract.get("contract_hash")
    dataset_hash = frame_hash(dataset)
    if manifest.get("feature_contract_hash") != contract_hash:
        raise AblationError("feature_contract_hash_manifest_mismatch")
    if manifest.get("dataset_hash") != dataset_hash:
        raise AblationError("dataset_hash_manifest_mismatch")
    if split_manifest.get("feature_contract_hash") != contract_hash:
        raise AblationError("feature_contract_hash_split_mismatch")
    if split_manifest.get("dataset_hash") != dataset_hash:
        raise AblationError("dataset_hash_split_mismatch")

    expected_dataset_sha = manifest.get("materialized_dataset_sha256")
    if isinstance(expected_dataset_sha, str):
        if _sha256(dataset_file) != expected_dataset_sha:
            raise AblationError("materialized_dataset_sha256_mismatch")

    numeric = dataset[[*feature_columns, TARGET_COLUMN]].apply(
        pd.to_numeric, errors="coerce"
    )
    if numeric.isna().any().any():
        raise AblationError("feature_or_target_nan")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise AblationError("feature_or_target_non_finite")

    for column in ("open_time_utc", "close_time_utc", "feature_cutoff_utc"):
        if column not in dataset.columns:
            raise AblationError(f"dataset_missing_timestamp:{column}")
        dataset[column] = pd.to_datetime(
            dataset[column], utc=True, errors="coerce"
        )
        if dataset[column].isna().any():
            raise AblationError(f"dataset_invalid_timestamp:{column}")

    if (dataset["feature_cutoff_utc"] > dataset["open_time_utc"]).any():
        raise AblationError("feature_cutoff_after_trade_open")

    # Preserve the exact materialized row order pinned by dataset_hash.
    # The canonical WQ6 producer already writes this frame in deterministic
    # point-in-time order; changing the order here would invalidate split lineage.
    dataset = dataset.reset_index(drop=True)

    embargo_seconds = int(split_manifest.get("embargo_seconds", -1))
    if embargo_seconds < 0:
        raise AblationError("split_manifest_embargo_invalid")

    splits = build_walkforward_splits(
        dataset,
        embargo_seconds=embargo_seconds,
    )
    public = [public_split(split) for split in splits]
    if public != split_manifest.get("splits"):
        raise AblationError("split_manifest_reconstruction_mismatch")

    if not splits:
        raise AblationError("walkforward_splits_empty")

    source = {
        "dataset_path": str(dataset_file),
        "dataset_sha256": _sha256(dataset_file),
        "feature_contract_path": str(feature_contract_file),
        "feature_contract_sha256": _sha256(feature_contract_file),
        "dataset_manifest_path": str(dataset_manifest_file),
        "dataset_manifest_sha256": _sha256(dataset_manifest_file),
        "split_manifest_path": str(split_manifest_file),
        "split_manifest_sha256": _sha256(split_manifest_file),
        "dataset_hash": dataset_hash,
        "feature_contract_hash": contract_hash,
        "split_manifest_hash": split_manifest.get("split_manifest_hash"),
        "row_count": int(len(dataset)),
        "split_count": len(splits),
        "embargo_seconds": embargo_seconds,
    }
    return dataset, feature_columns, splits, source


def _metrics(pnl: Sequence[float]) -> dict[str, Any]:
    values = np.asarray(list(pnl), dtype=float)
    if values.size == 0:
        return {
            "trade_count": 0,
            "net_pnl": 0.0,
            "expectancy": None,
            "profit_factor": None,
            "win_rate": None,
            "max_drawdown": None,
        }
    if not np.isfinite(values).all():
        raise AblationError("metric_pnl_non_finite")

    positive = values[values > 0]
    negative = values[values < 0]
    gross_profit = float(positive.sum())
    gross_loss_abs = float(abs(negative.sum()))
    profit_factor = (
        gross_profit / gross_loss_abs if gross_loss_abs > 0.0 else None
    )
    equity = np.cumsum(values)
    peak = np.maximum.accumulate(equity)
    max_drawdown = abs(float(np.min(equity - peak)))
    return {
        "trade_count": int(values.size),
        "net_pnl": float(values.sum()),
        "expectancy": float(values.mean()),
        "profit_factor": profit_factor,
        "win_rate": float(np.mean(values > 0.0)),
        "max_drawdown": max_drawdown,
    }


def _threshold_candidates(scores: np.ndarray) -> list[float]:
    if scores.size == 0 or not np.isfinite(scores).all():
        raise AblationError("calibration_scores_invalid")
    raw = [float(np.quantile(scores, quantile)) for quantile in THRESHOLD_QUANTILES]
    return sorted(set(raw))


def _calibrate_threshold(
    scores: np.ndarray,
    pnl: np.ndarray,
) -> dict[str, Any]:
    if len(scores) != len(pnl) or len(scores) == 0:
        raise AblationError("calibration_shape_invalid")
    minimum = min(MIN_CALIBRATION_SELECTED, len(scores))
    candidates: list[dict[str, Any]] = []
    for threshold in _threshold_candidates(scores):
        selected = scores >= threshold
        count = int(selected.sum())
        if count < minimum:
            continue
        selected_pnl = pnl[selected]
        metrics = _metrics(selected_pnl)
        candidates.append(
            {
                "threshold": threshold,
                "selected_trade_count": count,
                "coverage": float(count / len(scores)),
                "net_pnl": metrics["net_pnl"],
                "expectancy": metrics["expectancy"],
                "profit_factor": metrics["profit_factor"],
            }
        )
    if not candidates:
        raise AblationError("no_valid_calibration_threshold")

    best = max(
        candidates,
        key=lambda item: (
            float(item["net_pnl"]),
            float(item["expectancy"] or 0.0),
            float(item["coverage"]),
            -float(item["threshold"]),
        ),
    )
    return {
        "status": "ok",
        "threshold": float(best["threshold"]),
        "selected_trade_count": int(best["selected_trade_count"]),
        "coverage": float(best["coverage"]),
        "net_pnl": float(best["net_pnl"]),
        "expectancy": best["expectancy"],
        "candidate_count": len(candidates),
        "threshold_source": "past_validation_only",
    }


def _fit_predict(
    *,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    test: pd.DataFrame,
    features: Sequence[str],
    model_factory: ModelFactory,
) -> tuple[np.ndarray, np.ndarray]:
    if not features:
        raise AblationError("empty_model_feature_set")
    model = model_factory()
    train_x = train[list(features)].to_numpy(dtype=float)
    validation_x = validation[list(features)].to_numpy(dtype=float)
    test_x = test[list(features)].to_numpy(dtype=float)
    train_y = train[TARGET_COLUMN].to_numpy(dtype=float)

    model.fit(train_x, train_y)
    validation_scores = np.asarray(model.predict(validation_x), dtype=float)
    test_scores = np.asarray(model.predict(test_x), dtype=float)
    if validation_scores.shape != (len(validation),):
        raise AblationError("validation_prediction_shape_invalid")
    if test_scores.shape != (len(test),):
        raise AblationError("test_prediction_shape_invalid")
    if not np.isfinite(validation_scores).all() or not np.isfinite(test_scores).all():
        raise AblationError("prediction_non_finite")
    return validation_scores, test_scores


def _evaluate_variant(
    *,
    dataset: pd.DataFrame,
    splits: Sequence[Mapping[str, Any]],
    features: Sequence[str],
    model_factory: ModelFactory,
) -> dict[str, Any]:
    fold_reports: list[dict[str, Any]] = []
    aggregate_control: list[float] = []
    aggregate_treatment: list[float] = []
    aggregate_selected_rows: list[pd.DataFrame] = []
    aggregate_test_rows: list[pd.DataFrame] = []

    for split in splits:
        train_idx = list(split["_train_indices"])
        validation_idx = list(split["_validation_indices"])
        test_idx = list(split["_test_indices"])
        train = dataset.iloc[train_idx]
        validation = dataset.iloc[validation_idx]
        test = dataset.iloc[test_idx]

        validation_scores, test_scores = _fit_predict(
            train=train,
            validation=validation,
            test=test,
            features=features,
            model_factory=model_factory,
        )
        validation_pnl = validation[TARGET_COLUMN].to_numpy(dtype=float)
        calibration = _calibrate_threshold(validation_scores, validation_pnl)
        threshold = float(calibration["threshold"])
        selected = test_scores >= threshold

        control_pnl = test[TARGET_COLUMN].to_numpy(dtype=float)
        treatment_pnl = control_pnl[selected]
        aggregate_control.extend(control_pnl.tolist())
        aggregate_treatment.extend(treatment_pnl.tolist())
        test_copy = test.copy()
        test_copy["_score"] = test_scores
        test_copy["_selected"] = selected
        aggregate_test_rows.append(test_copy)
        aggregate_selected_rows.append(test_copy.loc[selected])

        fold_reports.append(
            {
                "split_id": split["split_id"],
                "feature_count": len(features),
                "train_trade_count": len(train),
                "validation_trade_count": len(validation),
                "test_trade_count": len(test),
                "threshold": threshold,
                "threshold_source": "past_validation_only",
                "validation_selected_trade_count": calibration[
                    "selected_trade_count"
                ],
                "test_selected_trade_count": int(selected.sum()),
                "test_coverage": float(selected.mean()),
                "control": _metrics(control_pnl),
                "treatment": _metrics(treatment_pnl),
            }
        )

    control = _metrics(aggregate_control)
    treatment = _metrics(aggregate_treatment)
    coverage = (
        treatment["trade_count"] / control["trade_count"]
        if control["trade_count"]
        else None
    )

    all_test = pd.concat(aggregate_test_rows, ignore_index=True)
    selected_test = pd.concat(aggregate_selected_rows, ignore_index=True)
    all_test["_duration_seconds"] = (
        all_test["close_time_utc"] - all_test["open_time_utc"]
    ).dt.total_seconds()
    selected_test["_duration_seconds"] = (
        selected_test["close_time_utc"] - selected_test["open_time_utc"]
    ).dt.total_seconds()

    priority_control = all_test.loc[
        all_test["_duration_seconds"].lt(15 * 60), TARGET_COLUMN
    ].astype(float)
    priority_treatment = selected_test.loc[
        selected_test["_duration_seconds"].lt(15 * 60), TARGET_COLUMN
    ].astype(float)

    return {
        "feature_count": len(features),
        "features": list(features),
        "fold_count": len(fold_reports),
        "folds": fold_reports,
        "control": control,
        "treatment": treatment,
        "coverage": coverage,
        "delta_net_pnl_vs_control": (
            float(treatment["net_pnl"]) - float(control["net_pnl"])
        ),
        "delta_expectancy_vs_control": (
            None
            if treatment["expectancy"] is None or control["expectancy"] is None
            else float(treatment["expectancy"]) - float(control["expectancy"])
        ),
        "priority_segment_duration_lt_15m": {
            "diagnostic_only": True,
            "used_for_training": False,
            "used_for_threshold_calibration": False,
            "control": _metrics(priority_control.tolist()),
            "treatment": _metrics(priority_treatment.tolist()),
        },
    }


def build_market_intelligence_pnl_ablation_v1(
    *,
    project_root: str | Path,
    dataset_path: str | Path | None = None,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    split_manifest_path: str | Path | None = None,
    model_factory: ModelFactory | None = None,
) -> dict[str, Any]:
    """Run deterministic leave-one-block-out OOS economic ablation."""

    try:
        dataset, feature_columns, splits, source = _load_bundle(
            project_root=project_root,
            dataset_path=dataset_path,
            feature_contract_path=feature_contract_path,
            dataset_manifest_path=dataset_manifest_path,
            split_manifest_path=split_manifest_path,
        )
        factory = model_factory or _default_model_factory

        full = _evaluate_variant(
            dataset=dataset,
            splits=splits,
            features=feature_columns,
            model_factory=factory,
        )

        ablations: dict[str, dict[str, Any]] = {}
        contributions: list[dict[str, Any]] = []
        for block_name, block_features in FEATURE_BLOCKS.items():
            remaining = tuple(
                feature
                for feature in feature_columns
                if feature not in set(block_features)
            )
            result = _evaluate_variant(
                dataset=dataset,
                splits=splits,
                features=remaining,
                model_factory=factory,
            )
            ablations[block_name] = result

            full_net = float(full["treatment"]["net_pnl"])
            ablated_net = float(result["treatment"]["net_pnl"])
            contribution = full_net - ablated_net
            full_expectancy = full["treatment"]["expectancy"]
            ablated_expectancy = result["treatment"]["expectancy"]
            expectancy_contribution = (
                None
                if full_expectancy is None or ablated_expectancy is None
                else float(full_expectancy) - float(ablated_expectancy)
            )
            contributions.append(
                {
                    "feature_block": block_name,
                    "removed_features": list(block_features),
                    "net_pnl_contribution": contribution,
                    "expectancy_contribution": expectancy_contribution,
                    "classification": (
                        "positive"
                        if contribution > 1e-9
                        else "negative"
                        if contribution < -1e-9
                        else "neutral"
                    ),
                }
            )

        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "reason": "market_intelligence_leave_one_block_out_ablation_complete",
            "decision": "ABLATION_READY_RESEARCH_ONLY",
            "source": source,
            "target": TARGET_COLUMN,
            "feature_block_count": len(FEATURE_BLOCKS),
            "feature_blocks": {
                key: list(value) for key, value in FEATURE_BLOCKS.items()
            },
            "model": {
                "family": (
                    "injected_test_double"
                    if model_factory is not None
                    else "sklearn_hist_gradient_boosting_regressor"
                ),
                "random_seed": RANDOM_SEED,
                "threshold_calibration": "past_validation_only",
                "final_oos_threshold_tuning": False,
            },
            "full_model": full,
            "ablations": ablations,
            "feature_block_contributions": contributions,
            "priority_segment": {
                "dimension": "duration_bucket",
                "bucket": "<15m",
                "diagnostic_only": True,
                "future_duration_used_as_feature": False,
            },
            "training_performed": True,
            "training_scope": "research_only_walkforward_ablation",
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }
    except (
        AblationError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ImportError,
    ) as exc:
        reason = str(exc) if isinstance(exc, AblationError) else (
            f"ablation_failed:{type(exc).__name__}"
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "decision": "ABLATION_BLOCKED",
            "training_performed": False,
            **SAFETY_FLAGS,
            "safety_flags": dict(SAFETY_FLAGS),
        }


__all__ = [
    "AblationError",
    "EXPECTED_FEATURES",
    "FEATURE_BLOCKS",
    "SCHEMA_VERSION",
    "build_market_intelligence_pnl_ablation_v1",
]
