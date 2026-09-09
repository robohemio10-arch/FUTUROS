"""Prospective Paper confirmation for the frozen Qlib economic policy V2.

The policy is reconstructed only from pre-boundary Paper outcomes and point-in-time
market features. A production-grade prospective freeze is a two-phase operation:
code is first certified by CI, then an immutable freeze is materialized with the
exact certified implementation commit, CI run and CI completion timestamp. The
prospective boundary starts at freeze materialization, never at an earlier historical
constant. Prospective outcomes never enter fit, calibration, threshold selection,
early stopping or feature imputation. This component is research-only and has no
operational authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smartcrypto.learning.paper_autolearning import (
    qlib_long_economic_policy_challenger_v2 as v2,
)
from smartcrypto.learning.paper_autolearning import (
    qlib_market_context_economic_challenger as base,
)

SCHEMA_VERSION = "paper_autolearning_qlib_v2_prospective_paper_confirmation_v1"
PROVENANCE_SCHEMA_VERSION = "qlib_v2_certified_freeze_provenance_v1"
CERTIFIED_DEV_COMMIT = "9639e7f49cd5155eed2766df506d380acf91f8df"
POLICY_VERSION = v2.SCHEMA_VERSION
SELECTOR_FIELD = "qlib_v2_prospective_selected"
SCORE_FIELD = "qlib_v2_prospective_score"
THRESHOLD_FIELD = "qlib_v2_frozen_threshold"
DEFAULT_FREEZE_SPEC_PATH = Path(
    "data/reports/qlib_v2/qlib_v2_prospective_freeze_spec_v1.json"
)
DEFAULT_REPORT_PATH = Path(
    "data/reports/qlib_v2/qlib_v2_prospective_paper_confirmation_v1.json"
)

_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "promotion_allowed": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "changes_risk": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "writes_runtime": False,
}


def build_qlib_v2_prospective_paper_confirmation_v1(
    *,
    project_root: str | Path,
    rows: Sequence[Mapping[str, Any]] | None = None,
    market_rows: Sequence[Mapping[str, Any]] | pd.DataFrame | None = None,
    outcome_path: str | Path | None = None,
    market_features_path: str | Path | None = None,
    prospective_start_utc: str | datetime | None = None,
    certified_implementation_commit: str | None = None,
    certified_ci_run_id: int | None = None,
    certified_ci_completed_at_utc: str | datetime | None = None,
    freeze_materialized_at_utc: str | datetime | None = None,
    additional_execution_stress_bps: float = base.DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    expected_freeze_spec: Mapping[str, Any] | None = None,
    predictor: base.Predictor | None = None,
) -> dict[str, Any]:
    """Freeze V2 on pre-boundary data and score later closed Paper trades.

    When an immutable freeze already exists, its provenance is authoritative. For a
    new native-Qlib freeze, complete certified provenance is mandatory. Injected test
    doubles may omit provenance so unit tests can exercise economic invariants without
    pretending to be a production certification.
    """

    root = Path(project_root).resolve()
    stress_bps = base._validate_stress_bps(additional_execution_stress_bps)
    outcome_source = base._resolve(root, outcome_path or base.DEFAULT_OUTCOME_PATH)
    market_source = base._resolve(root, market_features_path or base.DEFAULT_MARKET_FEATURES_PATH)

    provenance, provenance_blockers = _resolve_provenance(
        prospective_start_utc=prospective_start_utc,
        certified_implementation_commit=certified_implementation_commit,
        certified_ci_run_id=certified_ci_run_id,
        certified_ci_completed_at_utc=certified_ci_completed_at_utc,
        freeze_materialized_at_utc=freeze_materialized_at_utc,
        expected_freeze_spec=expected_freeze_spec,
    )
    if provenance is None:
        return _blocked_without_boundary_report(
            stress_bps=stress_bps,
            outcome_source=outcome_source,
            market_source=market_source,
            reason=provenance_blockers[0],
            blockers=provenance_blockers,
        )

    boundary = provenance["prospective_start_utc"]
    assert isinstance(boundary, datetime)

    input_rows = (
        [dict(row) for row in rows]
        if rows is not None
        else base._read_rows(outcome_source)
    )
    raw_market = base._market_frame(market_rows, market_source)

    normalized, invalid_time_count = base._normalize_outcomes(input_rows)
    duplicates = _duplicate_trade_ids(normalized)
    if invalid_time_count or duplicates:
        blockers: list[str] = []
        if invalid_time_count:
            blockers.append("invalid_trade_time_detected")
        if duplicates:
            blockers.append("duplicate_trade_id_detected")
        return _blocked_report(
            boundary=boundary,
            provenance=provenance,
            stress_bps=stress_bps,
            outcome_source=outcome_source,
            market_source=market_source,
            input_row_count=len(input_rows),
            valid_closed_outcome_count=len(normalized),
            reason=blockers[0],
            blockers=blockers,
            diagnostics={"duplicate_trade_ids": duplicates[:20]},
        )

    try:
        rematerialized = base._rematerialize_market_features(raw_market)
        aligned, alignment_report = base._align_point_in_time_market_features(
            normalized,
            rematerialized,
        )
    except Exception as exc:
        return _blocked_report(
            boundary=boundary,
            provenance=provenance,
            stress_bps=stress_bps,
            outcome_source=outcome_source,
            market_source=market_source,
            input_row_count=len(input_rows),
            valid_closed_outcome_count=len(normalized),
            reason="market_feature_rematerialization_or_alignment_failed",
            blockers=["market_feature_rematerialization_or_alignment_failed"],
            diagnostics={"error_type": type(exc).__name__, "error": str(exc)},
        )

    pre_boundary = [row for row in aligned if row["__open_time"] <= boundary]
    prospective = [row for row in aligned if row["__open_time"] > boundary]
    label_cutoff = boundary - timedelta(seconds=base.OUTCOME_AVAILABILITY_EMBARGO_SECONDS)
    unavailable_pre_boundary = [
        row for row in pre_boundary if row["__close_time"] > label_cutoff
    ]

    common = _common_report(
        boundary=boundary,
        provenance=provenance,
        stress_bps=stress_bps,
        outcome_source=outcome_source,
        market_source=market_source,
        input_row_count=len(input_rows),
        valid_closed_outcome_count=len(normalized),
        alignment_report=alignment_report,
        pre_boundary_count=len(pre_boundary),
        prospective_count=len(prospective),
        unavailable_pre_boundary_count=len(unavailable_pre_boundary),
    )

    preflight: list[str] = list(provenance_blockers)
    if alignment_report["coverage"] < base.MIN_PIT_ALIGNMENT_COVERAGE:
        preflight.append("point_in_time_market_feature_coverage_not_met")
    if len(pre_boundary) < base.MIN_INITIAL_CONTEXT_TRADES:
        preflight.append("min_pre_boundary_context_not_met")
    if not pre_boundary:
        preflight.append("pre_boundary_rows_missing")
    if preflight and predictor is None:
        unique = list(dict.fromkeys(preflight))
        return {
            **common,
            "status": "blocked",
            "reason": unique[0],
            "decision": "MANTER_EM_RESEARCH",
            "freeze_spec": None,
            "prospective_observations": [],
            "economic_evidence": None,
            "blockers": unique,
        }
    if any(item not in provenance_blockers for item in preflight):
        unique = list(dict.fromkeys(preflight))
        return {
            **common,
            "status": "blocked",
            "reason": unique[0],
            "decision": "MANTER_EM_RESEARCH",
            "freeze_spec": None,
            "prospective_observations": [],
            "economic_evidence": None,
            "blockers": unique,
        }

    sentinel = dict(pre_boundary[-1])
    sentinel["__open_time"] = boundary
    sentinel["__close_time"] = boundary + timedelta(seconds=1)
    prepared, preparation_blockers = v2._prepare_fold(
        prior_rows=pre_boundary,
        test_rows=[sentinel],
        stress_bps=stress_bps,
    )
    if prepared is None:
        blockers = [f"freeze:{item}" for item in preparation_blockers]
        return {
            **common,
            "status": "blocked",
            "reason": blockers[0],
            "decision": "MANTER_EM_RESEARCH",
            "freeze_spec": None,
            "prospective_observations": [],
            "economic_evidence": None,
            "blockers": blockers,
        }

    prospective_x = _matrix_from_frozen_fit(prepared, prospective)
    score_x = prospective_x if len(prospective_x) else prepared.test_x

    try:
        if predictor is not None:
            return _run_predictor(
                predictor=predictor,
                predictor_mode="injected_test_double",
                native_qlib_used=False,
                model_metadata={"framework": "test_double", "fallback_used": False},
                prepared=prepared,
                prospective=prospective,
                prospective_x=prospective_x,
                score_x=score_x,
                stress_bps=stress_bps,
                boundary=boundary,
                provenance=provenance,
                provenance_blockers=provenance_blockers,
                common=common,
                expected_freeze_spec=expected_freeze_spec,
            )

        with v2._native_qlib_lgb_predictor_context() as (native_predictor, metadata):
            return _run_predictor(
                predictor=native_predictor,
                predictor_mode="native_qlib_contrib_lgb_frozen_prospective_v1",
                native_qlib_used=True,
                model_metadata=metadata,
                prepared=prepared,
                prospective=prospective,
                prospective_x=prospective_x,
                score_x=score_x,
                stress_bps=stress_bps,
                boundary=boundary,
                provenance=provenance,
                provenance_blockers=provenance_blockers,
                common=common,
                expected_freeze_spec=expected_freeze_spec,
            )
    except Exception as exc:
        return {
            **common,
            "status": "blocked",
            "reason": "frozen_policy_model_fit_or_score_failed",
            "decision": "MANTER_EM_RESEARCH",
            "predictor_mode": "native_qlib_contrib_lgb_frozen_prospective_v1",
            "native_qlib_used": False,
            "model": {"error_type": type(exc).__name__, "error": str(exc)},
            "freeze_spec": None,
            "prospective_observations": [],
            "economic_evidence": None,
            "blockers": ["frozen_policy_model_fit_or_score_failed"],
        }


def _run_predictor(
    *,
    predictor: base.Predictor,
    predictor_mode: str,
    native_qlib_used: bool,
    model_metadata: Mapping[str, Any],
    prepared: base.PreparedFold,
    prospective: Sequence[Mapping[str, Any]],
    prospective_x: pd.DataFrame,
    score_x: pd.DataFrame,
    stress_bps: float,
    boundary: datetime,
    provenance: Mapping[str, Any],
    provenance_blockers: Sequence[str],
    common: Mapping[str, Any],
    expected_freeze_spec: Mapping[str, Any] | None,
) -> dict[str, Any]:
    calibration_scores, test_scores = predictor(
        prepared.train_x,
        prepared.train_y,
        prepared.calibration_x,
        score_x,
        fold_id="prospective_freeze_v1",
    )
    calibration_scores = base._finite_scores(
        calibration_scores,
        expected=len(prepared.calibration_rows),
    )
    test_scores = base._finite_scores(test_scores, expected=len(score_x))
    prospective_scores = (
        test_scores[: len(prospective)] if prospective else np.asarray([], dtype=float)
    )

    calibration = v2._select_calibration_threshold(
        rows=prepared.calibration_rows,
        scores=calibration_scores,
        stress_bps=stress_bps,
    )
    if calibration["status"] != "ok":
        return {
            **dict(common),
            "status": "blocked",
            "reason": "frozen_calibration_no_economic_threshold",
            "decision": "MANTER_EM_RESEARCH",
            "predictor_mode": predictor_mode,
            "native_qlib_used": native_qlib_used,
            "model": dict(model_metadata),
            "calibration": calibration,
            "freeze_spec": None,
            "prospective_observations": [],
            "economic_evidence": None,
            "blockers": ["frozen_calibration_no_economic_threshold"],
        }

    threshold = float(calibration["threshold"])
    dataset_sha = _dataset_fingerprint(prepared)
    feature_medians = _feature_medians(prepared)
    calibration_score_fingerprint_sha256 = _score_fingerprint(calibration_scores)
    contract = _freeze_contract(
        boundary=boundary,
        provenance=provenance,
        stress_bps=stress_bps,
        prepared=prepared,
        calibration=calibration,
        model_metadata=model_metadata,
        dataset_sha=dataset_sha,
        feature_medians=feature_medians,
        calibration_score_fingerprint_sha256=calibration_score_fingerprint_sha256,
    )
    policy_sha = _sha256_json(contract)
    freeze_spec = {**contract, "policy_sha256": policy_sha}

    mismatch = _freeze_mismatch(expected_freeze_spec, freeze_spec)
    if mismatch:
        return {
            **dict(common),
            "status": "blocked",
            "reason": "frozen_policy_fingerprint_mismatch",
            "decision": "MANTER_EM_RESEARCH",
            "predictor_mode": predictor_mode,
            "native_qlib_used": native_qlib_used,
            "model": dict(model_metadata),
            "calibration": calibration,
            "freeze_spec": freeze_spec,
            "freeze_mismatch_fields": mismatch,
            "prospective_observations": [],
            "economic_evidence": None,
            "blockers": ["frozen_policy_fingerprint_mismatch"],
        }

    if provenance_blockers and native_qlib_used:
        unique = list(dict.fromkeys(provenance_blockers))
        return {
            **dict(common),
            "status": "blocked",
            "reason": unique[0],
            "decision": "MANTER_EM_RESEARCH",
            "predictor_mode": predictor_mode,
            "native_qlib_used": native_qlib_used,
            "model": dict(model_metadata),
            "calibration": calibration,
            "freeze_spec": freeze_spec,
            "freeze_spec_verified": False,
            "prospective_observations": [],
            "economic_evidence": None,
            "blockers": unique,
        }

    observations: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    for row, score in zip(prospective, prospective_scores, strict=True):
        selected = bool(v2._eligible(row) and float(score) >= threshold)
        if selected:
            selected_rows.append(dict(row))
        observations.append(
            {
                "observation_id": _observation_id(row, policy_sha),
                "trade_id": str(row.get("trade_id") or row.get("event_id") or ""),
                "symbol": str(row.get("__symbol") or row.get("symbol_norm") or ""),
                "side": str(row.get("side") or "").lower(),
                "open_time_utc": row["__open_time"].isoformat(),
                "close_time_utc": row["__close_time"].isoformat(),
                "pit_feature_ts_utc": _time_iso(row.get("__market_feature_ts")),
                "pit_feature_available_at_utc": _time_iso(
                    row.get("__market_feature_available_at")
                ),
                "score": round(float(score), 12),
                "threshold": round(threshold, 12),
                "eligible": v2._eligible(row),
                "selected": selected,
                "realized_net_pnl": round(float(row["net_pnl"]), 10),
                "realized_stressed_net_pnl": round(
                    base._stressed_pnl(row, stress_bps), 10
                ),
                "policy_sha256": policy_sha,
                "pre_boundary_dataset_sha256": dataset_sha,
            }
        )

    baseline_metrics = base._economic_metrics(prospective, stress_bps)
    treatment_metrics = base._economic_metrics(selected_rows, stress_bps)
    evidence = _economic_evidence(
        baseline=baseline_metrics,
        treatment=treatment_metrics,
        prospective_count=len(prospective),
        selected_count=len(selected_rows),
    )
    return {
        **dict(common),
        "status": "observing" if prospective else "frozen_waiting_for_prospective_trades",
        "reason": (
            "frozen_policy_scored_prospective_closed_trades"
            if prospective
            else "frozen_policy_ready_no_post_boundary_closed_trades"
        ),
        "decision": "COLETAR_EVIDENCIA_PROSPECTIVA",
        "predictor_mode": predictor_mode,
        "native_qlib_used": native_qlib_used,
        "model": dict(model_metadata),
        "calibration": calibration,
        "freeze_spec": freeze_spec,
        "freeze_spec_verified": expected_freeze_spec is not None,
        "prospective_observations": observations,
        "prospective_closed_trade_count": len(prospective),
        "prospective_selected_trade_count": len(selected_rows),
        "economic_evidence": evidence,
        "prospective_profit_certified": False,
        "promotion_allowed": False,
        "blockers": [],
    }


def _freeze_contract(
    *,
    boundary: datetime,
    provenance: Mapping[str, Any],
    stress_bps: float,
    prepared: base.PreparedFold,
    calibration: Mapping[str, Any],
    model_metadata: Mapping[str, Any],
    dataset_sha: str,
    feature_medians: Mapping[str, float],
    calibration_score_fingerprint_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "source_policy_schema": POLICY_VERSION,
        "prospective_start_utc": boundary.isoformat(),
        "freeze_materialized_at_utc": _time_iso(provenance["freeze_materialized_at_utc"]),
        "certified_dev_commit": CERTIFIED_DEV_COMMIT,
        "certified_implementation_commit": provenance["certified_implementation_commit"],
        "certified_ci_run_id": provenance["certified_ci_run_id"],
        "certified_ci_completed_at_utc": _time_iso(
            provenance["certified_ci_completed_at_utc"]
        ),
        "freeze_provenance_complete": bool(provenance["freeze_provenance_complete"]),
        "additional_execution_stress_bps": stress_bps,
        "target": v2.TARGET_NAME,
        "eligibility_policy": v2.ELIGIBILITY_POLICY,
        "score_quantiles": list(v2.SCORE_QUANTILES),
        "feature_columns": list(prepared.feature_columns),
        "outcome_availability_embargo_seconds": base.OUTCOME_AVAILABILITY_EMBARGO_SECONDS,
        "fit_trade_count": len(prepared.fit_rows),
        "calibration_trade_count": len(prepared.calibration_rows),
        "pre_boundary_dataset_sha256": dataset_sha,
        "feature_medians": {
            column: round(float(feature_medians[column]), 12)
            for column in prepared.feature_columns
        },
        "calibration_score_count": len(prepared.calibration_rows),
        "calibration_score_fingerprint_sha256": calibration_score_fingerprint_sha256,
        "threshold": float(calibration["threshold"]),
        "selected_quantile": calibration["selected_quantile"],
        "model": dict(model_metadata),
    }


def _resolve_provenance(
    *,
    prospective_start_utc: str | datetime | None,
    certified_implementation_commit: str | None,
    certified_ci_run_id: int | None,
    certified_ci_completed_at_utc: str | datetime | None,
    freeze_materialized_at_utc: str | datetime | None,
    expected_freeze_spec: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if expected_freeze_spec is not None:
        boundary_value = expected_freeze_spec.get("prospective_start_utc")
        if boundary_value is None:
            return None, ["existing_freeze_missing_prospective_start_utc"]
        boundary = _parse_boundary(boundary_value)
        materialized = _parse_optional_time(
            expected_freeze_spec.get("freeze_materialized_at_utc")
        )
        ci_completed = _parse_optional_time(
            expected_freeze_spec.get("certified_ci_completed_at_utc")
        )
        provenance = {
            "prospective_start_utc": boundary,
            "freeze_materialized_at_utc": materialized,
            "certified_implementation_commit": expected_freeze_spec.get(
                "certified_implementation_commit"
            ),
            "certified_ci_run_id": expected_freeze_spec.get("certified_ci_run_id"),
            "certified_ci_completed_at_utc": ci_completed,
            "freeze_provenance_complete": bool(
                expected_freeze_spec.get("freeze_provenance_complete")
            ),
        }
        blockers = _provenance_blockers(provenance)
        if expected_freeze_spec.get("certified_dev_commit") != CERTIFIED_DEV_COMMIT:
            blockers.append("existing_freeze_certified_dev_commit_mismatch")
        return provenance, list(dict.fromkeys(blockers))

    if prospective_start_utc is None:
        return None, ["prospective_start_utc_required_without_existing_freeze"]

    boundary = _parse_boundary(prospective_start_utc)
    materialized = _parse_optional_time(freeze_materialized_at_utc)
    ci_completed = _parse_optional_time(certified_ci_completed_at_utc)
    provenance = {
        "prospective_start_utc": boundary,
        "freeze_materialized_at_utc": materialized,
        "certified_implementation_commit": (
            None
            if certified_implementation_commit is None
            else str(certified_implementation_commit).strip().lower()
        ),
        "certified_ci_run_id": certified_ci_run_id,
        "certified_ci_completed_at_utc": ci_completed,
        "freeze_provenance_complete": False,
    }
    blockers = _provenance_blockers(provenance)
    provenance["freeze_provenance_complete"] = not blockers
    return provenance, blockers


def _provenance_blockers(provenance: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    commit = provenance.get("certified_implementation_commit")
    ci_run = provenance.get("certified_ci_run_id")
    ci_completed = provenance.get("certified_ci_completed_at_utc")
    materialized = provenance.get("freeze_materialized_at_utc")
    boundary = provenance.get("prospective_start_utc")

    if not isinstance(commit, str) or _SHA1_RE.fullmatch(commit) is None:
        blockers.append("certified_implementation_commit_required")
    if not isinstance(ci_run, int) or isinstance(ci_run, bool) or ci_run <= 0:
        blockers.append("certified_ci_run_id_required")
    if not isinstance(ci_completed, datetime):
        blockers.append("certified_ci_completed_at_utc_required")
    if not isinstance(materialized, datetime):
        blockers.append("freeze_materialized_at_utc_required")
    if not isinstance(boundary, datetime):
        blockers.append("prospective_start_utc_required")

    if isinstance(ci_completed, datetime) and isinstance(materialized, datetime):
        if materialized < ci_completed:
            blockers.append("freeze_materialized_before_certified_ci_completed")
    if isinstance(materialized, datetime) and isinstance(boundary, datetime):
        if boundary != materialized:
            blockers.append("prospective_start_must_equal_freeze_materialization_time")
    return blockers


def _matrix_from_frozen_fit(
    prepared: base.PreparedFold,
    rows: Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    feature_columns = list(prepared.feature_columns)
    fit_feature_rows = [base._model_feature_row(row) for row in prepared.fit_rows]
    row_features = [base._model_feature_row(row) for row in rows]
    medians: dict[str, float] = {}
    for column in feature_columns:
        values = [base._numeric(row.get(column)) for row in fit_feature_rows]
        finite = [value for value in values if value is not None and math.isfinite(value)]
        if not finite:
            raise ValueError(f"frozen_feature_median_missing:{column}")
        medians[column] = float(np.median(np.asarray(finite, dtype=float)))

    data: dict[str, list[float]] = {column: [] for column in feature_columns}
    for row in row_features:
        for column in feature_columns:
            value = base._numeric(row.get(column))
            data[column].append(
                medians[column]
                if value is None or not math.isfinite(value)
                else float(value)
            )
    frame = pd.DataFrame(data, columns=feature_columns, dtype=float)
    if len(frame) and not np.isfinite(frame.to_numpy()).all():
        raise ValueError("non_finite_prospective_feature_matrix")
    return frame



def _feature_medians(prepared: base.PreparedFold) -> dict[str, float]:
    fit_feature_rows = [base._model_feature_row(row) for row in prepared.fit_rows]
    medians: dict[str, float] = {}
    for column in prepared.feature_columns:
        values = [base._numeric(row.get(column)) for row in fit_feature_rows]
        finite = [
            float(value)
            for value in values
            if value is not None and math.isfinite(value)
        ]
        if not finite:
            raise ValueError(f"frozen_feature_median_missing:{column}")
        medians[column] = float(np.median(np.asarray(finite, dtype=float)))
    return medians


def _score_fingerprint(scores: np.ndarray) -> str:
    values = base._finite_scores(scores, expected=len(scores))
    payload = [round(float(value), 10) for value in values]
    return _sha256_json(payload)

def _dataset_fingerprint(prepared: base.PreparedFold) -> str:
    payload: list[dict[str, Any]] = []
    for partition, rows in (
        ("fit", prepared.fit_rows),
        ("calibration", prepared.calibration_rows),
    ):
        for row in rows:
            features = base._model_feature_row(row)
            payload.append(
                {
                    "partition": partition,
                    "trade_id": str(row.get("trade_id") or row.get("event_id") or ""),
                    "symbol": str(row.get("__symbol") or ""),
                    "side": str(row.get("side") or "").lower(),
                    "open_time_utc": row["__open_time"].isoformat(),
                    "close_time_utc": row["__close_time"].isoformat(),
                    "net_pnl": float(row["net_pnl"]),
                    "notional": base._notional(row),
                    "features": {
                        column: base._numeric(features.get(column))
                        for column in prepared.feature_columns
                    },
                }
            )
    return _sha256_json(payload)


def _economic_evidence(
    *,
    baseline: Mapping[str, Any],
    treatment: Mapping[str, Any],
    prospective_count: int,
    selected_count: int,
) -> dict[str, Any]:
    baseline_net = float(baseline["net_pnl"])
    treatment_net = float(treatment["net_pnl"])
    treatment_pf = treatment.get("profit_factor")
    economic_positive = bool(
        selected_count > 0
        and treatment_net > 0
        and float(treatment["expectancy"]) > 0
        and treatment_pf is not None
        and float(treatment_pf) >= base.MIN_TREATMENT_PROFIT_FACTOR
    )
    return {
        "prospective_closed_trade_count": prospective_count,
        "selected_trade_count": selected_count,
        "baseline": dict(baseline),
        "treatment": dict(treatment),
        "delta_stressed_net_pnl": round(treatment_net - baseline_net, 10),
        "economic_positive_so_far": economic_positive,
        "confirmation_adjudicated": False,
        "confirmation_reason": "sample_size_and_duration_gate_not_defined_by_v2_contract",
    }


def _freeze_mismatch(
    expected: Mapping[str, Any] | None,
    actual: Mapping[str, Any],
) -> list[str]:
    if expected is None:
        return []
    keys = (
        "schema_version",
        "provenance_schema_version",
        "source_policy_schema",
        "prospective_start_utc",
        "freeze_materialized_at_utc",
        "certified_dev_commit",
        "certified_implementation_commit",
        "certified_ci_run_id",
        "certified_ci_completed_at_utc",
        "freeze_provenance_complete",
        "additional_execution_stress_bps",
        "target",
        "eligibility_policy",
        "score_quantiles",
        "feature_columns",
        "outcome_availability_embargo_seconds",
        "fit_trade_count",
        "calibration_trade_count",
        "pre_boundary_dataset_sha256",
        "feature_medians",
        "calibration_score_count",
        "calibration_score_fingerprint_sha256",
        "threshold",
        "selected_quantile",
        "policy_sha256",
    )
    return [key for key in keys if expected.get(key) != actual.get(key)]


def _common_report(
    *,
    boundary: datetime,
    provenance: Mapping[str, Any],
    stress_bps: float,
    outcome_source: Path,
    market_source: Path,
    input_row_count: int,
    valid_closed_outcome_count: int,
    alignment_report: Mapping[str, Any],
    pre_boundary_count: int,
    prospective_count: int,
    unavailable_pre_boundary_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "source_policy_schema": POLICY_VERSION,
        "prospective_start_utc": boundary.isoformat(),
        "freeze_materialized_at_utc": _time_iso(provenance["freeze_materialized_at_utc"]),
        "certified_dev_commit": CERTIFIED_DEV_COMMIT,
        "certified_implementation_commit": provenance["certified_implementation_commit"],
        "certified_ci_run_id": provenance["certified_ci_run_id"],
        "certified_ci_completed_at_utc": _time_iso(
            provenance["certified_ci_completed_at_utc"]
        ),
        "freeze_provenance_complete": bool(provenance["freeze_provenance_complete"]),
        "additional_execution_stress_bps": stress_bps,
        "outcome_source_path": str(outcome_source),
        "market_features_source_path": str(market_source),
        "input_row_count": input_row_count,
        "valid_closed_outcome_count": valid_closed_outcome_count,
        "pre_boundary_aligned_trade_count": pre_boundary_count,
        "pre_boundary_labels_unavailable_at_freeze_count": unavailable_pre_boundary_count,
        "prospective_aligned_closed_trade_count": prospective_count,
        "point_in_time_alignment": dict(alignment_report),
        "training_contamination_allowed": False,
        "prospective_labels_used_for_training": False,
        "prospective_labels_used_for_calibration": False,
        "threshold_recalibration_after_boundary_allowed": False,
        "prospective_profit_certified": False,
        **SAFETY_FLAGS,
        "write_performed": False,
    }


def _blocked_report(
    *,
    boundary: datetime,
    provenance: Mapping[str, Any],
    stress_bps: float,
    outcome_source: Path,
    market_source: Path,
    input_row_count: int,
    valid_closed_outcome_count: int,
    reason: str,
    blockers: list[str],
    diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "source_policy_schema": POLICY_VERSION,
        "prospective_start_utc": boundary.isoformat(),
        "freeze_materialized_at_utc": _time_iso(provenance["freeze_materialized_at_utc"]),
        "certified_dev_commit": CERTIFIED_DEV_COMMIT,
        "certified_implementation_commit": provenance["certified_implementation_commit"],
        "certified_ci_run_id": provenance["certified_ci_run_id"],
        "certified_ci_completed_at_utc": _time_iso(
            provenance["certified_ci_completed_at_utc"]
        ),
        "freeze_provenance_complete": bool(provenance["freeze_provenance_complete"]),
        "additional_execution_stress_bps": stress_bps,
        "outcome_source_path": str(outcome_source),
        "market_features_source_path": str(market_source),
        "input_row_count": input_row_count,
        "valid_closed_outcome_count": valid_closed_outcome_count,
        "status": "blocked",
        "reason": reason,
        "decision": "MANTER_EM_RESEARCH",
        "freeze_spec": None,
        "prospective_observations": [],
        "economic_evidence": None,
        "diagnostics": dict(diagnostics),
        "prospective_profit_certified": False,
        **SAFETY_FLAGS,
        "write_performed": False,
        "blockers": blockers,
    }


def _blocked_without_boundary_report(
    *,
    stress_bps: float,
    outcome_source: Path,
    market_source: Path,
    reason: str,
    blockers: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "source_policy_schema": POLICY_VERSION,
        "prospective_start_utc": None,
        "freeze_materialized_at_utc": None,
        "certified_dev_commit": CERTIFIED_DEV_COMMIT,
        "certified_implementation_commit": None,
        "certified_ci_run_id": None,
        "certified_ci_completed_at_utc": None,
        "freeze_provenance_complete": False,
        "additional_execution_stress_bps": stress_bps,
        "outcome_source_path": str(outcome_source),
        "market_features_source_path": str(market_source),
        "status": "blocked",
        "reason": reason,
        "decision": "MANTER_EM_RESEARCH",
        "freeze_spec": None,
        "prospective_observations": [],
        "economic_evidence": None,
        "prospective_profit_certified": False,
        **SAFETY_FLAGS,
        "write_performed": False,
        "blockers": blockers,
    }


def _duplicate_trade_ids(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        trade_id = str(row.get("trade_id") or row.get("event_id") or "").strip()
        if not trade_id:
            continue
        if trade_id in seen:
            duplicates.add(trade_id)
        seen.add(trade_id)
    return sorted(duplicates)


def _observation_id(row: Mapping[str, Any], policy_sha: str) -> str:
    trade_id = str(row.get("trade_id") or row.get("event_id") or "")
    raw = f"{policy_sha}|{trade_id}|{row['__open_time'].isoformat()}"
    return "pros-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _parse_boundary(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("prospective_start_utc_must_be_timezone_aware")
    return parsed.astimezone(UTC)


def _parse_optional_time(value: Any) -> datetime | None:
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
        return None
    return parsed.astimezone(UTC)


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _time_iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return None
