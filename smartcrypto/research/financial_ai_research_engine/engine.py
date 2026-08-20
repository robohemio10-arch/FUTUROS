"""Financial AI research engine with purged walk-forward evaluation."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor, LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from smartcrypto.learning.walkforward.leakage_audit import audit_leakage
from smartcrypto.learning.walkforward.purged_split_engine import (
    build_walkforward_splits,
)
from smartcrypto.research.paper_edge_foundation.foundation import file_sha256

from .calibration import (
    financial_metrics,
    financial_probability_metrics,
    regression_metrics,
)
from .contracts import (
    DECISION,
    FINANCIAL_EV_SEMANTICS,
    REMAINING_EV_SEMANTICS,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    EngineConfig,
    FinancialCandidateEstimate,
    stable_hash,
    utc_now_iso,
)
from .dataset import build_financial_training_dataset
from .persistence import (
    resolve_estimates_path,
    resolve_report_path,
    write_estimates_idempotent,
    write_report,
)


MODEL_VERSION = "financial_ai_huber_logistic_walkforward_v1"
DEFAULT_DRIFT_REPORT = Path(
    "data/reports/ai_qlib_drift_regime_monitor_v1.json"
)


class BaselineMeanBySegment:
    """Hierarchical, deterministic mean-net-PnL baseline."""

    def __init__(self) -> None:
        self.global_mean = 0.0
        self.means: dict[
            tuple[str, ...], dict[tuple[str, ...], float]
        ] = {}

    def fit(self, frame: pd.DataFrame) -> "BaselineMeanBySegment":
        target = pd.to_numeric(
            frame["realized_net_pnl_usdt"], errors="raise"
        )
        self.global_mean = float(target.mean())
        for columns in (
            ("symbol", "side", "regime"),
            ("symbol", "side"),
            ("symbol",),
            ("side",),
            ("regime",),
        ):
            grouped = (
                frame.assign(__target=target)
                .groupby(list(columns), dropna=False)["__target"]
                .mean()
            )
            mapping: dict[tuple[str, ...], float] = {}
            for key, value in grouped.items():
                keys = key if isinstance(key, tuple) else (key,)
                mapping[tuple(str(item) for item in keys)] = float(value)
            self.means[columns] = mapping
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        predictions: list[float] = []
        for _, row in frame.iterrows():
            value = self.global_mean
            for columns in self.means:
                key = tuple(str(row.get(column)) for column in columns)
                if key in self.means[columns]:
                    value = self.means[columns][key]
                    break
            predictions.append(value)
        return np.asarray(predictions, dtype=float)


class FinancialAIResearchEngine:
    """Build diagnostic financial estimates without operational authority."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()

    def run(
        self,
        *,
        project_root: str | Path,
        paper_db: str | Path | None,
        feature_source: str | Path | None = None,
        qlib_source: str | Path | None = None,
        regime_source: str | Path | None = None,
        trader_master_source: str | Path | None = None,
        execution_cost_source: str | Path | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        root = Path(project_root).resolve()
        report_generated_at_utc = utc_now_iso()

        frame, dataset_report = build_financial_training_dataset(
            project_root=root,
            paper_db=paper_db,
            feature_source=feature_source,
            qlib_source=qlib_source,
            regime_source=regime_source,
            trader_master_source=trader_master_source,
            execution_cost_source=execution_cost_source,
        )
        drift = _read_drift_report(root)
        evaluation = self._evaluate(
            frame,
            dataset_report,
            drift,
            report_generated_at_utc=report_generated_at_utc,
        )
        estimates = evaluation.pop("estimate_records")
        blockers = list(evaluation["blockers"])

        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "report_generated_at_utc": report_generated_at_utc,
            "generated_at_utc": report_generated_at_utc,
            "status": evaluation["status"],
            "reason": evaluation["reason"],
            "decision": DECISION,
            "blockers": blockers,
            "sources": dataset_report.get("sources", {}),
            "dataset": dataset_report.get("dataset", {}),
            "lineage": dataset_report.get("lineage", {}),
            "feature_contract": dataset_report.get(
                "feature_contract", {}
            ),
            "targets": dataset_report.get("targets", {}),
            "cost_model": dataset_report.get("cost_model", {}),
            "walk_forward": evaluation["walk_forward"],
            "anti_leakage": evaluation["anti_leakage"],
            "regression_metrics": evaluation["regression_metrics"],
            "classification_metrics": evaluation[
                "classification_metrics"
            ],
            "calibration": evaluation["calibration"],
            "segment_metrics": evaluation["segment_metrics"],
            "qlib_comparison": evaluation["qlib_comparison"],
            "trader_master_comparison": dataset_report.get(
                "trader_master_comparison", {}
            ),
            "financial_evidence": evaluation["financial_evidence"],
            "candidate_estimates": {
                "estimate_count": len(estimates),
                "trusted_estimate_count": sum(
                    bool(row["financial_estimate_trusted"])
                    for row in estimates
                ),
                "candidate_ev_generated_count": sum(
                    row["candidate_ev"] is not None
                    for row in estimates
                ),
                "candidate_ev_blocked_count": sum(
                    row["candidate_ev"] is None
                    for row in estimates
                ),
                "candidate_linked_estimate_count": sum(
                    row.get("candidate_linkage_status") == "LINKED"
                    for row in estimates
                ),
                "branch2_compatible_estimate_count": sum(
                    bool(row.get("branch2_compatible"))
                    for row in estimates
                ),
                "records": estimates,
            },
            "remaining_position_estimates": evaluation[
                "remaining_position_estimates"
            ],
            "uncertainty": evaluation["uncertainty"],
            "drift": drift,
            "gates": evaluation["gates"],
            "safety": dict(SAFETY_FLAGS),
            **SAFETY_FLAGS,
            "write_requested": False,
            "write_performed": False,
            "write_report_performed": False,
            "write_estimates_performed": False,
            "estimates_appended": 0,
        }
        return _json_safe(report), estimates

    def _evaluate(
        self,
        frame: pd.DataFrame,
        dataset_report: Mapping[str, Any],
        drift: Mapping[str, Any],
        *,
        report_generated_at_utc: str,
    ) -> dict[str, Any]:
        empty = _empty_evaluation()
        if frame.empty:
            gates = _base_gates(dataset_report, drift)
            blockers = _gate_blockers(gates)
            empty.update(
                {
                    "status": "BLOCKED",
                    "reason": str(
                        dataset_report.get(
                            "reason", "SOURCE_MISSING"
                        )
                    ),
                    "gates": gates,
                    "blockers": blockers,
                }
            )
            return empty

        trainable = (
            frame.loc[frame["trainable"]]
            .copy()
            .reset_index(drop=True)
        )
        config = self.config
        features = _complete_features(
            trainable,
            list(
                dataset_report.get("feature_contract", {}).get(
                    "feature_columns", []
                )
            ),
        )
        sample_sufficient = _sample_sufficient(
            trainable, config
        )

        if not sample_sufficient or not features:
            gates = _base_gates(dataset_report, drift)
            gates.update(
                {
                    "train_sample_sufficient": sample_sufficient,
                    "oos_sample_sufficient": False,
                    "walk_forward_valid": False,
                    "anti_leakage_valid": False,
                    "candidate_ev_ready": False,
                    "remaining_position_ev_ready": False,
                    "financial_ai_research_ready": False,
                }
            )
            blockers = _gate_blockers(gates)
            empty.update(
                {
                    "status": "PARTIAL",
                    "reason": (
                        "INSUFFICIENT_SAMPLE"
                        if not sample_sufficient
                        else "NO_COMPLETE_FEATURE_COLUMNS"
                    ),
                    "gates": gates,
                    "blockers": blockers,
                    "remaining_position_estimates": _remaining_contract(),
                }
            )
            return empty

        splits = build_walkforward_splits(
            trainable,
            embargo_seconds=config.embargo_seconds,
        )
        evaluation = _run_walkforward(
            trainable,
            features,
            splits,
            config,
            dataset_report.get("feature_contract", {}),
        )
        gates = _build_gates(
            dataset_report,
            drift,
            evaluation,
            sample_sufficient,
            config,
        )
        blockers = _gate_blockers(gates)

        financial_evidence = _financial_evidence(
            dataset_report=dataset_report,
            drift=drift,
            evaluation=evaluation,
            config=config,
        )
        estimates = _candidate_estimates(
            evaluation,
            gates,
            dataset_report,
            financial_evidence=financial_evidence,
            estimate_generated_at_utc=report_generated_at_utc,
        )

        status = "OK" if gates["candidate_ev_ready"] else "PARTIAL"
        reason = (
            "FINANCIAL_AI_RESEARCH_EVIDENCE_READY"
            if gates["candidate_ev_ready"]
            else _blocked_reason(gates)
        )
        return {
            "status": status,
            "reason": reason,
            "blockers": blockers,
            "walk_forward": evaluation["walk_forward"],
            "anti_leakage": evaluation["anti_leakage"],
            "regression_metrics": evaluation["regression_metrics"],
            "classification_metrics": evaluation[
                "classification_metrics"
            ],
            "calibration": evaluation["classification_metrics"],
            "segment_metrics": evaluation["segment_metrics"],
            "qlib_comparison": evaluation["qlib_comparison"],
            "remaining_position_estimates": _remaining_contract(),
            "uncertainty": evaluation["uncertainty"],
            "financial_evidence": financial_evidence,
            "gates": gates,
            "estimate_records": estimates,
        }


def build_financial_ai_research_engine_v1(
    *,
    project_root: str | Path,
    paper_db: str | Path | None,
    feature_source: str | Path | None = None,
    qlib_source: str | Path | None = None,
    regime_source: str | Path | None = None,
    trader_master_source: str | Path | None = None,
    execution_cost_source: str | Path | None = None,
    write_report_requested: bool = False,
    write_estimates_requested: bool = False,
    output_report: str | Path | None = None,
    output_estimates: str | Path | None = None,
    config: EngineConfig | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    report, estimates = FinancialAIResearchEngine(config).run(
        project_root=root,
        paper_db=paper_db,
        feature_source=feature_source,
        qlib_source=qlib_source,
        regime_source=regime_source,
        trader_master_source=trader_master_source,
        execution_cost_source=execution_cost_source,
    )
    report_path = resolve_report_path(root, output_report)
    estimates_path = resolve_estimates_path(root, output_estimates)
    report["output_paths"] = {
        "report": str(report_path),
        "estimates": str(estimates_path),
    }

    write_audit: dict[str, Any] = {
        "write_requested": bool(
            write_report_requested or write_estimates_requested
        ),
        "write_performed": False,
        "write_report_performed": False,
        "write_estimates_performed": False,
        "estimates_appended": 0,
    }
    report.update(write_audit)

    try:
        if write_estimates_requested:
            appended = write_estimates_idempotent(
                root, estimates_path, estimates
            )
            write_audit["estimates_appended"] = appended
            write_audit["write_estimates_performed"] = bool(
                appended
            )
            write_audit["write_performed"] = bool(appended)
            report.update(write_audit)

        if write_report_requested:
            # Build the success audit into the exact payload that will be
            # atomically persisted. Mutate the returned report only after the
            # write completes, so an exception preserves truthful failure state.
            persisted_write_audit = {
                **write_audit,
                "write_report_performed": True,
                "write_performed": True,
            }
            persisted_report = {
                **report,
                **persisted_write_audit,
            }
            write_report(root, report_path, persisted_report)
            write_audit.update(persisted_write_audit)
            report.update(write_audit)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        report["status"] = "BLOCKED"
        report["reason"] = f"WRITE_FAILED:{type(exc).__name__}"
        report["decision"] = DECISION
        report["error_detail"] = str(exc)[:256]
        report.update(write_audit)
        blockers = list(report.get("blockers", []))
        if "WRITE_FAILED" not in blockers:
            blockers.append("WRITE_FAILED")
        report["blockers"] = blockers

    return _json_safe(report)


def _run_walkforward(
    frame: pd.DataFrame,
    features: list[str],
    splits: list[dict[str, Any]],
    config: EngineConfig,
    feature_contract: Mapping[str, Any],
) -> dict[str, Any]:
    leakage = _anti_leakage_report(
        frame,
        splits,
        feature_contract=feature_contract,
        embargo_seconds=config.embargo_seconds,
    )

    predicted: list[float] = []
    probabilities: list[float] = []
    actual: list[float] = []
    labels: list[int] = []
    oos_rows: list[dict[str, Any]] = []
    fold_reports: list[dict[str, Any]] = []
    qlib_records: list[dict[str, Any]] = []
    residuals_by_fold: list[list[float]] = []

    for split in splits:
        train = frame.iloc[split["_train_indices"]].copy()
        validation = frame.iloc[
            split["_validation_indices"]
        ].copy()
        test = frame.iloc[split["_test_indices"]].copy()

        x_train = train[features].to_numpy(dtype=float)
        x_test = test[features].to_numpy(dtype=float)
        y_train = train["realized_net_pnl_usdt"].to_numpy(
            dtype=float
        )
        y_test = test["realized_net_pnl_usdt"].to_numpy(
            dtype=float
        )
        classifier_target = train[
            "positive_net_outcome"
        ].to_numpy(dtype=int)

        try:
            regression = make_pipeline(
                StandardScaler(),
                HuberRegressor(max_iter=500),
            )
            regression.fit(x_train, y_train)
            fold_predicted = regression.predict(x_test)

            classifier = make_pipeline(
                StandardScaler(),
                LogisticRegression(max_iter=500),
            )
            classifier.fit(x_train, classifier_target)
            fold_probability = classifier.predict_proba(
                x_test
            )[:, 1]
        except (
            ValueError,
            RuntimeError,
            FloatingPointError,
        ):
            continue

        baseline = (
            BaselineMeanBySegment()
            .fit(train)
            .predict(test)
        )
        residuals = (y_test - fold_predicted).tolist()
        residuals_by_fold.append(residuals)
        predicted.extend(fold_predicted.tolist())
        probabilities.extend(fold_probability.tolist())
        actual.extend(y_test.tolist())
        labels.extend(
            test["positive_net_outcome"]
            .astype(int)
            .tolist()
        )

        fold_id = str(split["split_id"])
        for position, (_, row) in enumerate(test.iterrows()):
            oos_rows.append(
                {
                    **row.to_dict(),
                    "fold_id": fold_id,
                    "predicted_ev": float(
                        fold_predicted[position]
                    ),
                    "financial_win_probability": float(
                        fold_probability[position]
                    ),
                }
            )

        fold_reports.append(
            {
                "fold_id": fold_id,
                "train_start": split["train_start_utc"],
                "train_end": split["train_end_utc"],
                "validation_start": split[
                    "validation_start_utc"
                ],
                "validation_end": split[
                    "validation_end_utc"
                ],
                "test_start": split["test_start_utc"],
                "test_end": split["test_end_utc"],
                "purged_row_count": split[
                    "purged_row_count"
                ],
                "embargoed_row_count": split[
                    "embargoed_row_count"
                ],
                "embargo_seconds": config.embargo_seconds,
                "train_rows": len(train),
                "validation_rows": len(validation),
                "test_rows": len(test),
                "baseline_mean_by_segment": regression_metrics(
                    baseline.tolist(), y_test.tolist()
                ),
            }
        )
        qlib_records.extend(
            _qlib_fold_comparison(
                train,
                test,
                fold_predicted,
                y_test,
                fold_id,
            )
        )

    regression = regression_metrics(predicted, actual)
    classification = financial_probability_metrics(
        probabilities, labels, actual
    )
    selected_pnl = [
        pnl
        for pnl, estimate in zip(
            actual, predicted, strict=True
        )
        if estimate > 0
    ]
    regression["positive_predicted_ev_financial_metrics"] = (
        financial_metrics(selected_pnl)
    )
    oos_frame = pd.DataFrame(oos_rows)

    return {
        "walk_forward": {
            "status": (
                "ok" if fold_reports else "BLOCKED"
            ),
            "split_count": len(fold_reports),
            "random_split_used": False,
            "shuffle_used": False,
            "purge_applied": bool(splits),
            "embargo_applied": bool(splits),
            "embargo_seconds": config.embargo_seconds,
            "oos_rows": len(actual),
            "folds": fold_reports,
        },
        "anti_leakage": leakage,
        "regression_metrics": regression,
        "classification_metrics": classification,
        "segment_metrics": _segment_metrics(oos_frame),
        "qlib_comparison": _aggregate_qlib_comparison(
            qlib_records
        ),
        "uncertainty": _uncertainty(residuals_by_fold),
        "oos_rows": oos_rows,
    }


def _anti_leakage_report(
    frame: pd.DataFrame,
    splits: list[dict[str, Any]],
    *,
    feature_contract: Mapping[str, Any],
    embargo_seconds: int,
) -> dict[str, Any]:
    if not splits:
        return {
            "leakage_status": "blocked",
            "temporal_overlap_count": 0,
            "train_validation_overlap_count": 0,
            "train_test_overlap_count": 0,
            "embargo_violation_count": 0,
            "label_interval_overlap_count": 0,
            "future_columns_in_features_count": 0,
            "target_columns_in_features_count": 0,
            "outcome_columns_in_features_count": 0,
            "duplicated_order_id_across_splits_count": 0,
            "reason": "walkforward_splits_missing",
        }

    audit_contract = {
        "feature_columns": list(
            feature_contract.get("feature_columns", [])
        ),
        "label_columns": list(
            feature_contract.get("label_columns", [])
        ),
        "outcome_columns": list(
            feature_contract.get("outcome_columns", [])
        ),
    }
    report = audit_leakage(
        frame,
        splits,
        feature_contract=audit_contract,
        embargo_seconds=embargo_seconds,
    )
    return {**report, "reason": "institutional_leakage_audit"}


def _qlib_fold_comparison(
    train: pd.DataFrame,
    test: pd.DataFrame,
    financial_predictions: np.ndarray,
    realized: np.ndarray,
    fold_id: str,
) -> list[dict[str, Any]]:
    train_score = pd.to_numeric(
        train["qlib_score"], errors="coerce"
    )
    test_score = pd.to_numeric(
        test["qlib_score"], errors="coerce"
    )
    if (
        train_score.isna().any()
        or test_score.isna().any()
        or train_score.std(ddof=0) == 0
    ):
        return []

    financial_train_proxy = train[
        "realized_net_pnl_usdt"
    ].to_numpy(dtype=float)
    financial_std = float(
        np.std(financial_train_proxy)
    )
    if financial_std == 0:
        return []

    qlib_normalized = (
        test_score.to_numpy(dtype=float)
        - float(train_score.mean())
    ) / float(train_score.std(ddof=0))
    financial_normalized = (
        financial_predictions
        - float(np.mean(financial_train_proxy))
    ) / financial_std

    return [
        {
            "fold_id": fold_id,
            "qlib_score": float(
                qlib_normalized[index]
            ),
            "financial_ev_score": float(
                financial_normalized[index]
            ),
            "combined_score": float(
                qlib_normalized[index]
                + financial_normalized[index]
            ),
            "realized_net_pnl_usdt": float(
                realized[index]
            ),
        }
        for index in range(len(realized))
    ]


def _aggregate_qlib_comparison(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not rows:
        return {
            "status": "QLIB_LINEAGE_UNVERIFIED",
            "sample_count": 0,
            "normalization_fit_on_train_only": True,
            "experiments": {},
        }

    frame = pd.DataFrame(rows)
    experiments: dict[str, Any] = {}
    for name, column in (
        ("QLIB_ORDINAL_ONLY", "qlib_score"),
        ("FINANCIAL_EV_ONLY", "financial_ev_score"),
        ("QLIB_PLUS_FINANCIAL_EV", "combined_score"),
    ):
        selected, selection_audit = _select_top_fraction_preserving_ties(
            frame,
            column=column,
            fraction=0.20,
        )
        correlation = frame[column].corr(
            frame["realized_net_pnl_usdt"],
            method="spearman",
        )
        experiments[name] = {
            "sample_count": int(len(selected)),
            **selection_audit,
            **financial_metrics(
                selected[
                    "realized_net_pnl_usdt"
                ].tolist()
            ),
            "ranking_correlation": (
                float(correlation)
                if pd.notna(correlation)
                else None
            ),
        }

    return {
        "status": "ok",
        "sample_count": len(frame),
        "normalization_fit_on_train_only": True,
        "experiments": experiments,
    }


def _select_top_fraction_preserving_ties(
    frame: pd.DataFrame,
    *,
    column: str,
    fraction: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if frame.empty:
        raise ValueError("top_fraction_frame_must_not_be_empty")
    if not 0.0 < fraction <= 1.0:
        raise ValueError("top_fraction_must_be_between_zero_and_one")
    if column not in frame.columns:
        raise ValueError(f"top_fraction_column_missing:{column}")

    scores = pd.to_numeric(frame[column], errors="coerce")
    numeric_scores = scores.to_numpy(dtype=float)
    if scores.isna().any() or not np.isfinite(numeric_scores).all():
        raise ValueError(f"top_fraction_score_must_be_finite:{column}")

    requested_count = max(
        1,
        int(math.ceil(len(frame) * fraction)),
    )
    ordered_scores = np.sort(numeric_scores)[::-1]
    cutoff_score = float(
        ordered_scores[requested_count - 1]
    )

    # Boolean selection preserves the original OOS temporal order used by
    # financial metrics while including every score tied at the cutoff.
    selected = frame.loc[scores >= cutoff_score].copy()
    selected_count = int(len(selected))

    return selected, {
        "requested_fraction": fraction,
        "requested_count": requested_count,
        "selected_count": selected_count,
        "effective_fraction": float(
            selected_count / len(frame)
        ),
        "cutoff_score": cutoff_score,
        "ties_preserved": True,
    }


def _segment_metrics(
    frame: pd.DataFrame,
) -> dict[str, Any]:
    if frame.empty:
        return {}

    output: dict[str, Any] = {}
    definitions = {
        "GLOBAL": [],
        "symbol": ["symbol"],
        "side": ["side"],
        "symbol_side": ["symbol", "side"],
        "regime": ["regime"],
    }
    for name, columns in definitions.items():
        groups = (
            [("GLOBAL", frame)]
            if not columns
            else frame.groupby(
                columns, dropna=False
            )
        )
        records: list[dict[str, Any]] = []
        for key, group in groups:
            label = (
                key if isinstance(key, tuple) else (key,)
            )
            actual = pd.to_numeric(
                group["realized_net_pnl_usdt"],
                errors="coerce",
            )
            predicted = pd.to_numeric(
                group["predicted_ev"],
                errors="coerce",
            )
            records.append(
                {
                    "segment": "|".join(
                        map(str, label)
                    ),
                    "status": (
                        "ok"
                        if len(group) >= 20
                        else "INSUFFICIENT_SAMPLE"
                    ),
                    "trade_count": int(len(group)),
                    "positive_rate": float(
                        (actual > 0).mean()
                    ),
                    **financial_metrics(
                        actual.tolist()
                    ),
                    "predicted_ev_mean": float(
                        predicted.mean()
                    ),
                    "realized_pnl_mean": float(
                        actual.mean()
                    ),
                    "prediction_error": float(
                        (predicted - actual)
                        .abs()
                        .mean()
                    ),
                }
            )
        output[name] = records
    return output


def _uncertainty(
    residuals_by_fold: Sequence[Sequence[float]],
) -> dict[str, Any]:
    flattened = [
        float(value)
        for fold in residuals_by_fold
        for value in fold
    ]
    if (
        len(residuals_by_fold) < 2
        or len(flattened) < 30
    ):
        return {
            "uncertainty_method": "fold_residual_dispersion",
            "confidence_level": 0.80,
            "sample_count": len(flattened),
            "lower_residual": None,
            "upper_residual": None,
            "uncertainty_status": "INSUFFICIENT_SAMPLE",
            "iid_bootstrap_used": False,
        }

    return {
        "uncertainty_method": "fold_residual_dispersion",
        "confidence_level": 0.80,
        "sample_count": len(flattened),
        "lower_residual": float(
            np.quantile(flattened, 0.10)
        ),
        "upper_residual": float(
            np.quantile(flattened, 0.90)
        ),
        "uncertainty_status": "AVAILABLE",
        "iid_bootstrap_used": False,
    }


def _candidate_estimates(
    evaluation: Mapping[str, Any],
    gates: Mapping[str, bool],
    dataset_report: Mapping[str, Any],
    *,
    financial_evidence: Mapping[str, Any],
    estimate_generated_at_utc: str,
) -> list[dict[str, Any]]:
    source_hash = str(
        financial_evidence["financial_ev_source_hash"]
    )
    uncertainty = evaluation["uncertainty"]
    trusted = bool(gates.get("candidate_ev_ready"))
    blockers = tuple(_gate_blockers(gates))
    primary_status = (
        "AVAILABLE"
        if trusted
        else _primary_candidate_blocker(blockers)
    )
    records: list[dict[str, Any]] = []

    for row in evaluation["oos_rows"]:
        point = (
            float(row["predicted_ev"])
            if trusted
            else None
        )
        lower = None
        upper = None
        if (
            point is not None
            and uncertainty["uncertainty_status"]
            == "AVAILABLE"
        ):
            lower = point + float(
                uncertainty["lower_residual"]
            )
            upper = point + float(
                uncertainty["upper_residual"]
            )

        candidate_id = _optional_text(
            row.get("candidate_id")
        )
        candidate_linkage_status = str(
            row.get(
                "candidate_linkage_status",
                "CANDIDATE_UNLINKED",
            )
        )
        observed_at = _iso(
            row["decision_timestamp_utc"]
        )
        point_in_time_consumable = bool(
            candidate_id
            and candidate_linkage_status == "LINKED"
            and pd.Timestamp(
                estimate_generated_at_utc
            )
            <= pd.Timestamp(observed_at)
        )
        branch2_compatible = bool(
            point_in_time_consumable
            and candidate_id is not None
        )

        estimate_subject_id = str(
            row.get("estimate_subject_id")
            or f"trade:{row['trade_id']}"
        )
        estimate_id = stable_hash(
            {
                "schema": (
                    "financial_ai_candidate_estimate_v1"
                ),
                "estimate_subject_id": estimate_subject_id,
                "candidate_id": candidate_id,
                "fold_id": row["fold_id"],
                "model_version": MODEL_VERSION,
                "financial_ev_source_hash": source_hash,
                "financial_ev_generated_at_utc": (
                    estimate_generated_at_utc
                ),
                "financial_ev_available_at_utc": (
                    estimate_generated_at_utc
                ),
            }
        )

        estimate = FinancialCandidateEstimate(
            estimate_id=estimate_id,
            estimate_subject_id=estimate_subject_id,
            candidate_id=candidate_id,
            candidate_linkage_status=(
                candidate_linkage_status
            ),
            observed_at_utc=observed_at,
            estimate_scope="HISTORICAL_OOS",
            point_in_time_consumable=(
                point_in_time_consumable
            ),
            branch2_compatible=branch2_compatible,
            candidate_ev=point,
            financial_ev_semantics=(
                FINANCIAL_EV_SEMANTICS
            ),
            financial_ev_generated_at_utc=(
                estimate_generated_at_utc
            ),
            financial_ev_available_at_utc=(
                estimate_generated_at_utc
            ),
            financial_ev_source_hash=source_hash,
            financial_model_version=MODEL_VERSION,
            financial_win_probability=(
                float(
                    row[
                        "financial_win_probability"
                    ]
                )
                if trusted
                else None
            ),
            candidate_ev_lower=lower,
            candidate_ev_upper=upper,
            uncertainty_status=str(
                uncertainty["uncertainty_status"]
            ),
            position_remaining_ev=None,
            remaining_position_ev_semantics=(
                REMAINING_EV_SEMANTICS
            ),
            remaining_position_ev_generated_at_utc=None,
            remaining_position_ev_available_at_utc=None,
            remaining_position_ev_source_hash=None,
            switching_cost_estimate=(
                float(row["switching_cost_estimate"])
                if pd.notna(
                    row.get(
                        "switching_cost_estimate"
                    )
                )
                else None
            ),
            switching_cost_status=(
                "AVAILABLE"
                if pd.notna(
                    row.get(
                        "switching_cost_estimate"
                    )
                )
                else "SOURCE_MISSING"
            ),
            financial_estimate_trusted=trusted,
            candidate_ev_status=primary_status,
            candidate_ev_blockers=blockers,
        )
        records.append(estimate.to_dict())

    return records


def _build_gates(
    dataset_report: Mapping[str, Any],
    drift: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    sample_sufficient: bool,
    config: EngineConfig,
) -> dict[str, bool]:
    gates = _base_gates(dataset_report, drift)
    regression = evaluation["regression_metrics"]
    classification = evaluation[
        "classification_metrics"
    ]
    active = regression.get(
        "positive_predicted_ev_financial_metrics", {}
    )
    pf = active.get("profit_factor")
    expectancy_ci_lower = active.get(
        "expectancy_ci_lower"
    )
    expectancy_ci_status = active.get(
        "expectancy_ci_status"
    )

    gates.update(
        {
            "train_sample_sufficient": sample_sufficient,
            "oos_sample_sufficient": int(
                regression.get("sample_count", 0)
            )
            >= config.minimum_oos_rows,
            "walk_forward_valid": (
                evaluation["walk_forward"]["status"]
                == "ok"
            ),
            "anti_leakage_valid": (
                evaluation["anti_leakage"].get(
                    "leakage_status"
                )
                == "ok"
            ),
            "regression_quality_gate": bool(
                pf is not None
                and pf >= config.minimum_profit_factor
                and expectancy_ci_status
                == "AVAILABLE"
                and expectancy_ci_lower is not None
                and expectancy_ci_lower > 0
            ),
            "classification_quality_gate": bool(
                classification.get("auc")
                is not None
                and classification["auc"]
                >= config.minimum_auc
                and classification.get("brier")
                is not None
                and classification["brier"]
                <= config.maximum_brier
                and classification.get("ece")
                is not None
                and classification["ece"]
                <= config.maximum_ece
            ),
            "calibration_gate": bool(
                classification.get("brier")
                is not None
                and classification["brier"]
                <= config.maximum_brier
                and classification.get("ece")
                is not None
                and classification["ece"]
                <= config.maximum_ece
            ),
            "monotonicity_gate": (
                regression.get("monotonicity")
                == "MONOTONIC_NON_DECREASING"
            ),
            "qlib_lineage_gate": (
                dataset_report.get("lineage", {})
                .get("qlib", {})
                .get("status")
                == "ok"
            ),
            "trader_master_linkage_gate": (
                dataset_report.get(
                    "trader_master_comparison", {}
                ).get("linked_trade_count", 0)
                > 0
            ),
        }
    )

    required = (
        "dataset_source_available",
        "dataset_lineage_valid",
        "feature_contract_valid",
        "financial_label_valid",
        "cost_model_complete",
        "walk_forward_valid",
        "anti_leakage_valid",
        "train_sample_sufficient",
        "oos_sample_sufficient",
        "regression_quality_gate",
        "classification_quality_gate",
        "calibration_gate",
        "monotonicity_gate",
        "drift_gate",
    )
    gates["candidate_ev_ready"] = all(
        gates[name] for name in required
    )
    gates["remaining_position_ev_ready"] = False
    gates["financial_ai_research_ready"] = gates[
        "candidate_ev_ready"
    ]
    return gates


def _base_gates(
    dataset_report: Mapping[str, Any],
    drift: Mapping[str, Any],
) -> dict[str, bool]:
    dataset = dataset_report.get("dataset", {})
    targets = dataset_report.get("targets", {})
    return {
        "dataset_source_available": int(
            dataset.get("total_rows", 0)
        )
        > 0,
        "dataset_lineage_valid": bool(
            dataset.get("total_rows", 0)
            and int(
                dataset.get(
                    "lineage_valid_rows", 0
                )
            )
            == int(dataset.get("total_rows", 0))
        ),
        "feature_contract_valid": bool(
            dataset_report.get(
                "feature_contract", {}
            ).get("valid")
        ),
        "financial_label_valid": bool(
            targets.get("financial_label_valid")
        )
        and targets.get("pnl_authority")
        == "FREQTRADE_CLOSE_PROFIT_ABS",
        "cost_model_complete": bool(
            dataset_report.get(
                "cost_model", {}
            ).get("cost_model_complete")
        ),
        "walk_forward_valid": False,
        "anti_leakage_valid": False,
        "train_sample_sufficient": False,
        "oos_sample_sufficient": False,
        "regression_quality_gate": False,
        "classification_quality_gate": False,
        "calibration_gate": False,
        "monotonicity_gate": False,
        "qlib_lineage_gate": False,
        "trader_master_linkage_gate": False,
        "drift_gate": (
            drift.get("overall_drift_status")
            == "ok"
        ),
        "candidate_ev_ready": False,
        "remaining_position_ev_ready": False,
        "financial_ai_research_ready": False,
    }


def _read_drift_report(
    root: Path,
) -> dict[str, Any]:
    path = (root / DEFAULT_DRIFT_REPORT).resolve()
    if not path.exists() or not path.is_file():
        return {
            "drift_source_status": "SOURCE_MISSING",
            "feature_drift_status": "SOURCE_MISSING",
            "qlib_drift_status": "SOURCE_MISSING",
            "regime_status": "SOURCE_MISSING",
            "overall_drift_status": "SOURCE_MISSING",
            "source_hash": None,
        }

    try:
        raw = path.read_bytes()
        payload = json.loads(
            raw.decode("utf-8-sig")
        )
        source_hash = file_sha256(path).lower()
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return {
            "drift_source_status": "SOURCE_UNVERIFIED",
            "feature_drift_status": "SOURCE_UNVERIFIED",
            "qlib_drift_status": "SOURCE_UNVERIFIED",
            "regime_status": "SOURCE_UNVERIFIED",
            "overall_drift_status": "SOURCE_UNVERIFIED",
            "source_hash": None,
        }

    summary = (
        payload.get("drift_summary", {})
        if isinstance(payload, dict)
        else {}
    )
    critical = bool(
        summary.get("critical_drift_detected")
    )
    return {
        "drift_source_status": "ok",
        "feature_drift_status": _section_status(
            payload, "feature_drift_section"
        ),
        "qlib_drift_status": _section_status(
            payload,
            "qlib_performance_drift_section",
        ),
        "regime_status": str(
            payload.get("regime_summary", {}).get(
                "overall_regime",
                "SOURCE_UNVERIFIED",
            )
        ),
        "overall_drift_status": (
            "BLOCKED_DRIFT"
            if critical
            else "ok"
        ),
        "critical_drift_detected": critical,
        "source_path": str(path),
        "source_hash": source_hash,
    }


def _section_status(
    payload: Mapping[str, Any],
    name: str,
) -> str:
    section = payload.get(name)
    return (
        str(
            section.get(
                "status", "SOURCE_UNVERIFIED"
            )
        )
        if isinstance(section, Mapping)
        else "SOURCE_UNVERIFIED"
    )


def _sample_sufficient(
    frame: pd.DataFrame,
    config: EngineConfig,
) -> bool:
    positives = int(
        frame["positive_net_outcome"].eq(1).sum()
    )
    negatives = int(
        frame["positive_net_outcome"].eq(0).sum()
    )
    return bool(
        len(frame) >= config.minimum_train_rows
        and positives >= config.minimum_positive_rows
        and negatives >= config.minimum_negative_rows
    )


def _complete_features(
    frame: pd.DataFrame,
    candidates: list[str],
) -> list[str]:
    complete: list[str] = []
    for column in sorted(candidates):
        if column not in frame.columns:
            continue
        numeric = pd.to_numeric(
            frame[column], errors="coerce"
        ).replace([np.inf, -np.inf], np.nan)
        if numeric.notna().all() and numeric.nunique() > 1:
            complete.append(column)
    return complete


def _remaining_contract() -> dict[str, Any]:
    return {
        "status": "INSUFFICIENT_TRAINING_EVIDENCE",
        "remaining_position_ev_ready": False,
        "longitudinal_intermediate_dataset_available": False,
        "candidate_ev_reused_as_remaining_ev": False,
        "record_count": 0,
        "records": [],
    }


def _empty_evaluation() -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "reason": "SOURCE_MISSING",
        "blockers": ["SOURCE_MISSING"],
        "walk_forward": {
            "status": "BLOCKED",
            "split_count": 0,
            "random_split_used": False,
            "shuffle_used": False,
            "purge_applied": False,
            "embargo_applied": False,
            "oos_rows": 0,
            "folds": [],
        },
        "anti_leakage": {
            "leakage_status": "blocked",
            "reason": "walkforward_splits_missing",
        },
        "regression_metrics": regression_metrics([], []),
        "classification_metrics": (
            financial_probability_metrics([], [], [])
        ),
        "calibration": financial_probability_metrics(
            [], [], []
        ),
        "segment_metrics": {},
        "qlib_comparison": {
            "status": "QLIB_LINEAGE_UNVERIFIED",
            "sample_count": 0,
        },
        "remaining_position_estimates": (
            _remaining_contract()
        ),
        "uncertainty": {
            "uncertainty_method": (
                "fold_residual_dispersion"
            ),
            "uncertainty_status": (
                "INSUFFICIENT_SAMPLE"
            ),
            "sample_count": 0,
        },
        "financial_evidence": {
            "financial_ev_source_hash": None,
            "dataset_hash": None,
        },
        "gates": {},
        "estimate_records": [],
    }


def _financial_evidence(
    *,
    dataset_report: Mapping[str, Any],
    drift: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    config: EngineConfig,
) -> dict[str, Any]:
    sources = dataset_report.get("sources", {})
    source_hashes = {
        "paper": (
            sources.get("paper_db", {}).get(
                "sha256_before"
            )
            or sources.get("paper_db", {}).get(
                "sha256"
            )
        ),
        "feature": sources.get(
            "feature_source", {}
        ).get("sha256"),
        "qlib": sources.get(
            "qlib_source", {}
        ).get("sha256"),
        "regime": sources.get(
            "regime_source", {}
        ).get("sha256"),
        "execution_cost": sources.get(
            "execution_cost_source", {}
        ).get("sha256"),
    }
    split_definition = [
        {
            key: fold.get(key)
            for key in (
                "fold_id",
                "train_start",
                "train_end",
                "validation_start",
                "validation_end",
                "test_start",
                "test_end",
                "purged_row_count",
                "embargoed_row_count",
                "embargo_seconds",
                "train_rows",
                "validation_rows",
                "test_rows",
            )
        }
        for fold in evaluation.get(
            "walk_forward", {}
        ).get("folds", [])
    ]
    payload = {
        "schema": "financial_ai_evidence_v1",
        "dataset_hash": dataset_report.get(
            "dataset", {}
        ).get("dataset_hash"),
        "source_hashes": source_hashes,
        "drift_source_hash": drift.get(
            "source_hash"
        ),
        "drift_status": drift.get(
            "overall_drift_status"
        ),
        "feature_contract_hash": (
            dataset_report.get(
                "feature_contract", {}
            ).get("feature_contract_hash")
        ),
        "model_config": asdict(config),
        "split_definition": split_definition,
        "model_version": MODEL_VERSION,
    }
    evidence_hash = stable_hash(payload)
    return {
        **payload,
        "financial_ev_source_hash": evidence_hash,
        "model_config_hash": stable_hash(
            {"model_config": asdict(config)}
        ),
        "walk_forward_split_hash": stable_hash(
            {"split_definition": split_definition}
        ),
    }


def _gate_blockers(
    gates: Mapping[str, bool],
) -> list[str]:
    mapping = (
        ("dataset_source_available", "SOURCE_MISSING"),
        (
            "dataset_lineage_valid",
            "LINEAGE_UNVERIFIED",
        ),
        (
            "feature_contract_valid",
            "FEATURE_CONTRACT_INVALID",
        ),
        (
            "financial_label_valid",
            "FINANCIAL_LABEL_INVALID",
        ),
        (
            "cost_model_complete",
            "COST_MODEL_INCOMPLETE",
        ),
        (
            "walk_forward_valid",
            "WALK_FORWARD_INVALID",
        ),
        (
            "anti_leakage_valid",
            "ANTI_LEAKAGE_BLOCKED",
        ),
        (
            "train_sample_sufficient",
            "INSUFFICIENT_TRAIN_SAMPLE",
        ),
        (
            "oos_sample_sufficient",
            "INSUFFICIENT_OOS_SAMPLE",
        ),
        ("drift_gate", "BLOCKED_DRIFT"),
        (
            "regression_quality_gate",
            "REGRESSION_QUALITY_FAILED",
        ),
        (
            "classification_quality_gate",
            "CLASSIFICATION_QUALITY_FAILED",
        ),
        (
            "calibration_gate",
            "CALIBRATION_FAILED",
        ),
        (
            "monotonicity_gate",
            "NON_MONOTONIC",
        ),
    )
    return [
        reason
        for gate, reason in mapping
        if gate in gates and not gates.get(gate, False)
    ]


def _primary_candidate_blocker(
    blockers: Sequence[str],
) -> str:
    priority = (
        "SOURCE_MISSING",
        "LINEAGE_UNVERIFIED",
        "FINANCIAL_LABEL_INVALID",
        "FEATURE_CONTRACT_INVALID",
        "ANTI_LEAKAGE_BLOCKED",
        "INSUFFICIENT_TRAIN_SAMPLE",
        "INSUFFICIENT_OOS_SAMPLE",
        "COST_MODEL_INCOMPLETE",
        "BLOCKED_DRIFT",
        "REGRESSION_QUALITY_FAILED",
        "CLASSIFICATION_QUALITY_FAILED",
        "CALIBRATION_FAILED",
        "NON_MONOTONIC",
    )
    return next(
        (
            blocker
            for blocker in priority
            if blocker in blockers
        ),
        "QUALITY_GATE_FAILED",
    )


def _blocked_reason(
    gates: Mapping[str, bool],
) -> str:
    priority = (
        (
            "dataset_source_available",
            "SOURCE_MISSING",
        ),
        (
            "dataset_lineage_valid",
            "LINEAGE_UNVERIFIED",
        ),
        (
            "financial_label_valid",
            "FINANCIAL_LABEL_INVALID",
        ),
        (
            "train_sample_sufficient",
            "INSUFFICIENT_SAMPLE",
        ),
        (
            "walk_forward_valid",
            "WALK_FORWARD_INVALID",
        ),
        (
            "anti_leakage_valid",
            "ANTI_LEAKAGE_BLOCKED",
        ),
        (
            "cost_model_complete",
            "COST_MODEL_INCOMPLETE",
        ),
        ("drift_gate", "BLOCKED_DRIFT"),
        (
            "regression_quality_gate",
            "QUALITY_GATE_FAILED",
        ),
        (
            "classification_quality_gate",
            "QUALITY_GATE_FAILED",
        ),
        (
            "calibration_gate",
            "QUALITY_GATE_FAILED",
        ),
        (
            "monotonicity_gate",
            "QUALITY_GATE_FAILED",
        ),
    )
    return next(
        reason
        for gate, reason in priority
        if not gates.get(gate, False)
    )


def _optional_text(value: Any) -> str | None:
    if value is None or value is pd.NA:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        return None
    text = str(value).strip()
    return text or None


def _iso(value: Any) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    if isinstance(value, float):
        return (
            value if math.isfinite(value) else None
        )
    if isinstance(value, pd.Timestamp):
        return _iso(value)
    if value is pd.NA or value is pd.NaT:
        return None
    return value
