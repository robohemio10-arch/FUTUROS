"""Identity-safe ex-ante observer for the frozen Qlib V2 Paper policy.

The observer consumes only a certified immutable freeze, the current read-only
``active_freqtrade_signals.json`` payload and point-in-time market data. It records
Qlib V2 score/selection decisions before any trade outcome is available. Authoritative
identity is proven by the sealed Decision Ledger V4.2 record referenced by the active
signal envelope:

    candidate_id -> signal_id -> decision_event_id -> sealed decision payload SHA-256

No timestamp-nearest identity repair, symbol/side identity inference, trade-id aliasing,
RiskManager mutation, Freqtrade mutation, private exchange access or order submission
is allowed. The native Qlib model is reconstructed from pre-freeze Paper outcomes and
must reproduce the calibration-score fingerprint stored in the immutable freeze before
any prospective signal is accepted as evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smartcrypto.execution.freqtrade_contract import internal_symbol
from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    FinalDecision,
    RiskDecision,
    parse_payload_record,
)
from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1.publication import (
    ATTESTATION_KEY,
    ATTESTATION_SCHEMA,
    DECISION_LEDGER_KEY,
)
from smartcrypto.learning.paper_autolearning import (
    qlib_long_economic_policy_challenger_v2 as v2,
)
from smartcrypto.learning.paper_autolearning import (
    qlib_market_context_economic_challenger as base,
)
from smartcrypto.learning.paper_autolearning import (
    qlib_v2_prospective_paper_confirmation as prospective,
)

SCHEMA_VERSION = "paper_autolearning_qlib_v2_prospective_signal_observer_v2"
LEDGER_SCHEMA_VERSION = "paper_autolearning_qlib_v2_prospective_signal_ledger_v2"
DECISION_IDENTITY_SCHEMA_VERSION = "decision_ledger_payload_v4_2"
DEFAULT_ACTIVE_SIGNALS_PATH = Path("data/runtime/active_freqtrade_signals.json")
DEFAULT_DECISION_LEDGER_PATH = Path(
    "data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl"
)
DEFAULT_FREEZE_SPEC_PATH = Path(
    "data/reports/qlib_v2/qlib_v2_prospective_freeze_spec_decision_ledger_v2.json"
)
DEFAULT_LEDGER_PATH = Path(
    "data/research/qlib_v2/qlib_v2_prospective_signal_observations_v2.json"
)

FORBIDDEN_POST_OUTCOME_FIELDS = frozenset(
    {
        "trade_id",
        "paper_trade_id",
        "close_time",
        "close_time_utc",
        "close_date",
        "net_pnl",
        "gross_pnl",
        "profit_ratio",
        "exit_reason",
        "realized_net_pnl",
        "realized_stressed_net_pnl",
    }
)

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "read_only_execution_inputs": True,
    "operational_authority": False,
    "promotion_allowed": False,
    "changes_risk": False,
    "changes_strategy": False,
    "changes_stake": False,
    "changes_leverage": False,
    "changes_model": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "historical_backfill_allowed": False,
    "fuzzy_identity_matching_allowed": False,
    "timestamp_only_identity_matching_allowed": False,
    "symbol_side_identity_inference_allowed": False,
    "trade_id_as_candidate_id_allowed": False,
}


def build_qlib_v2_prospective_signal_observer_v1(
    *,
    project_root: str | Path,
    freeze_spec: Mapping[str, Any] | None = None,
    signal_payload: Mapping[str, Any] | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    market_rows: Sequence[Mapping[str, Any]] | pd.DataFrame | None = None,
    freeze_spec_path: str | Path | None = None,
    active_signals_path: str | Path | None = None,
    decision_ledger_path: str | Path | None = None,
    decision_ledger_records: Sequence[Mapping[str, Any]] | None = None,
    outcome_path: str | Path | None = None,
    market_features_path: str | Path | None = None,
    predictor: base.Predictor | None = None,
    observed_at_utc: datetime | None = None,
    score_completed_at_utc: datetime | None = None,
) -> dict[str, Any]:
    """Score authoritative active Paper signals against one certified frozen policy."""

    root = Path(project_root).resolve()
    resolved_freeze_path = base._resolve(
        root, freeze_spec_path or DEFAULT_FREEZE_SPEC_PATH
    )
    resolved_signals_path = base._resolve(
        root, active_signals_path or DEFAULT_ACTIVE_SIGNALS_PATH
    )
    resolved_decision_ledger_path = base._resolve(
        root, decision_ledger_path or DEFAULT_DECISION_LEDGER_PATH
    )
    outcome_source = base._resolve(root, outcome_path or base.DEFAULT_OUTCOME_PATH)
    market_source = base._resolve(
        root, market_features_path or base.DEFAULT_MARKET_FEATURES_PATH
    )

    freeze = (
        dict(freeze_spec)
        if freeze_spec is not None
        else _read_json_object(resolved_freeze_path)
    )
    payload = (
        dict(signal_payload)
        if signal_payload is not None
        else _read_json_object(resolved_signals_path)
    )

    freeze_state, freeze_blockers = _validate_freeze(freeze)
    if freeze_state is None:
        return _blocked_report(
            reason=freeze_blockers[0],
            blockers=freeze_blockers,
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
        )

    boundary = freeze_state["boundary"]
    observation_time = _required_utc(
        observed_at_utc if observed_at_utc is not None else datetime.now(UTC),
        "observed_at_utc",
    )
    policy_sha = str(freeze_state["policy_sha256"])
    threshold = float(freeze_state["threshold"])
    feature_columns = tuple(freeze_state["feature_columns"])
    feature_medians = dict(freeze_state["feature_medians"])

    extracted, identity_blockers = _extract_authoritative_signals(
        payload=payload,
        boundary=boundary,
        observed_at=observation_time,
        decision_ledger_path=resolved_decision_ledger_path,
        decision_ledger_records=decision_ledger_records,
    )
    if identity_blockers:
        return _blocked_report(
            reason=identity_blockers[0],
            blockers=identity_blockers,
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
            diagnostics={
                "decision_ledger_source_path": str(resolved_decision_ledger_path),
            },
        )

    if not extracted:
        return {
            **_common_report(
                freeze_path=resolved_freeze_path,
                signals_path=resolved_signals_path,
                outcome_source=outcome_source,
                market_source=market_source,
                policy_sha256=policy_sha,
                prospective_start_utc=boundary,
                observer_run_at_utc=observation_time,
            ),
            "status": "waiting_for_signals",
            "reason": "no_authoritative_post_freeze_active_signals",
            "decision": "COLETAR_EVIDENCIA_PROSPECTIVA",
            "decision_ledger_source_path": str(resolved_decision_ledger_path),
            "native_qlib_used": False,
            "freeze_spec_verified": True,
            "model_reconstruction_verified": False,
            "input_signal_count": 0,
            "observation_count": 0,
            "selected_signal_count": 0,
            "observations": [],
            "blockers": [],
        }

    input_rows = (
        [dict(row) for row in rows]
        if rows is not None
        else base._read_rows(outcome_source)
    )
    raw_market = base._market_frame(market_rows, market_source)

    normalized, invalid_time_count = base._normalize_outcomes(input_rows)
    if invalid_time_count:
        return _blocked_report(
            reason="invalid_trade_time_detected_in_freeze_reconstruction",
            blockers=["invalid_trade_time_detected_in_freeze_reconstruction"],
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )

    try:
        rematerialized = base._rematerialize_market_features(raw_market)
        aligned_outcomes, _ = base._align_point_in_time_market_features(
            normalized,
            rematerialized,
        )
    except Exception as exc:
        return _blocked_report(
            reason="market_feature_rematerialization_or_alignment_failed",
            blockers=["market_feature_rematerialization_or_alignment_failed"],
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
            diagnostics={"error_type": type(exc).__name__, "error": str(exc)},
        )

    pre_boundary = [
        row for row in aligned_outcomes if row["__open_time"] <= boundary
    ]
    sentinel = dict(pre_boundary[-1]) if pre_boundary else None
    if sentinel is None:
        return _blocked_report(
            reason="pre_boundary_rows_missing",
            blockers=["pre_boundary_rows_missing"],
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )
    sentinel["__open_time"] = boundary
    sentinel["__close_time"] = boundary + timedelta(seconds=1)

    prepared, preparation_blockers = v2._prepare_fold(
        prior_rows=pre_boundary,
        test_rows=[sentinel],
        stress_bps=float(freeze_state["stress_bps"]),
    )
    if prepared is None:
        blockers = [f"freeze_reconstruction:{item}" for item in preparation_blockers]
        return _blocked_report(
            reason=blockers[0],
            blockers=blockers,
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )

    reconstruction_blockers = _validate_reconstructed_freeze_inputs(
        freeze=freeze,
        prepared=prepared,
    )
    if reconstruction_blockers:
        return _blocked_report(
            reason=reconstruction_blockers[0],
            blockers=reconstruction_blockers,
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )

    signal_rows = [_signal_alignment_row(item) for item in extracted]
    aligned_signals, signal_alignment = base._align_point_in_time_market_features(
        signal_rows,
        rematerialized,
    )
    if len(aligned_signals) != len(signal_rows):
        return _blocked_report(
            reason="prospective_signal_point_in_time_alignment_incomplete",
            blockers=["prospective_signal_point_in_time_alignment_incomplete"],
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
            diagnostics={"signal_alignment": signal_alignment},
        )

    aligned_by_signal_id = {
        str(row["__observer_signal_id"]): row for row in aligned_signals
    }
    if len(aligned_by_signal_id) != len(extracted):
        return _blocked_report(
            reason="prospective_signal_alignment_identity_collision",
            blockers=["prospective_signal_alignment_identity_collision"],
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )

    signal_matrix_rows: list[dict[str, float]] = []
    ordered_alignment_rows: list[dict[str, Any]] = []
    for signal in extracted:
        aligned = aligned_by_signal_id.get(signal["signal_id"])
        if aligned is None:
            return _blocked_report(
                reason="prospective_signal_alignment_missing_identity",
                blockers=["prospective_signal_alignment_missing_identity"],
                freeze_path=resolved_freeze_path,
                signals_path=resolved_signals_path,
                outcome_source=outcome_source,
                market_source=market_source,
                policy_sha256=policy_sha,
                prospective_start_utc=boundary,
            )
        ordered_alignment_rows.append(aligned)
        raw_features = base._model_feature_row(aligned)
        vector: dict[str, float] = {}
        for column in feature_columns:
            raw_value = base._numeric(raw_features.get(column))
            value = feature_medians[column] if raw_value is None else float(raw_value)
            if not math.isfinite(value):
                return _blocked_report(
                    reason=f"prospective_signal_non_finite_feature:{column}",
                    blockers=[f"prospective_signal_non_finite_feature:{column}"],
                    freeze_path=resolved_freeze_path,
                    signals_path=resolved_signals_path,
                    outcome_source=outcome_source,
                    market_source=market_source,
                    policy_sha256=policy_sha,
                    prospective_start_utc=boundary,
                )
            vector[column] = value
        signal_matrix_rows.append(vector)

    signal_x = pd.DataFrame(signal_matrix_rows, columns=feature_columns, dtype=float)
    if not np.isfinite(signal_x.to_numpy()).all():
        return _blocked_report(
            reason="prospective_signal_feature_matrix_non_finite",
            blockers=["prospective_signal_feature_matrix_non_finite"],
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )

    try:
        if predictor is not None:
            calibration_scores, signal_scores = predictor(
                prepared.train_x,
                prepared.train_y,
                prepared.calibration_x,
                signal_x,
                fold_id="prospective_signal_observer_v1",
            )
            native_qlib_used = False
            predictor_mode = "injected_test_double"
            model_metadata: dict[str, Any] = {
                "framework": "test_double",
                "fallback_used": False,
            }
        else:
            with v2._native_qlib_lgb_predictor_context() as (
                native_predictor,
                metadata,
            ):
                calibration_scores, signal_scores = native_predictor(
                    prepared.train_x,
                    prepared.train_y,
                    prepared.calibration_x,
                    signal_x,
                    fold_id="prospective_signal_observer_v1",
                )
                model_metadata = dict(metadata)
            native_qlib_used = True
            predictor_mode = "native_qlib_contrib_lgb_frozen_signal_observer_v1"
    except Exception as exc:
        return _blocked_report(
            reason="frozen_policy_model_reconstruction_or_signal_score_failed",
            blockers=["frozen_policy_model_reconstruction_or_signal_score_failed"],
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
            diagnostics={"error_type": type(exc).__name__, "error": str(exc)},
        )

    calibration_scores = base._finite_scores(
        calibration_scores,
        expected=len(prepared.calibration_rows),
    )
    signal_scores = base._finite_scores(signal_scores, expected=len(extracted))
    reconstructed_fingerprint = prospective._score_fingerprint(calibration_scores)
    if reconstructed_fingerprint != freeze["calibration_score_fingerprint_sha256"]:
        return _blocked_report(
            reason="frozen_model_calibration_score_fingerprint_mismatch",
            blockers=["frozen_model_calibration_score_fingerprint_mismatch"],
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
            diagnostics={
                "expected": freeze["calibration_score_fingerprint_sha256"],
                "actual": reconstructed_fingerprint,
            },
        )

    if predictor is None and score_completed_at_utc is not None:
        return _blocked_report(
            reason="native_score_completion_timestamp_override_forbidden",
            blockers=["native_score_completion_timestamp_override_forbidden"],
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )

    score_completed_at = _required_utc(
        (
            score_completed_at_utc
            if score_completed_at_utc is not None
            else (observation_time if predictor is not None else datetime.now(UTC))
        ),
        "score_completed_at_utc",
    )
    if score_completed_at < observation_time:
        return _blocked_report(
            reason="score_completed_before_observer_started",
            blockers=["score_completed_before_observer_started"],
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )

    expired_signal_ids = [
        str(signal["signal_id"])
        for signal in extracted
        if score_completed_at > signal["valid_until"]
    ]
    if expired_signal_ids:
        blocker = f"signal_expired_before_score_completion:{expired_signal_ids[0]}"
        return _blocked_report(
            reason=blocker,
            blockers=[blocker],
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
        )

    scoring_latency_seconds = max(
        0.0,
        (score_completed_at - observation_time).total_seconds(),
    )

    observations: list[dict[str, Any]] = []
    for signal, aligned, vector, score in zip(
        extracted,
        ordered_alignment_rows,
        signal_matrix_rows,
        signal_scores,
        strict=True,
    ):
        selected = bool(signal["side"] == "long" and float(score) >= threshold)
        observation = {
            "schema_version": SCHEMA_VERSION,
            "policy_sha256": policy_sha,
            "pre_boundary_dataset_sha256": freeze["pre_boundary_dataset_sha256"],
            "candidate_id": signal["candidate_id"],
            "signal_id": signal["signal_id"],
            "correlation_id": signal["correlation_id"],
            "decision_event_id": signal["decision_event_id"],
            "decision_payload_sha256": signal["decision_payload_sha256"],
            "decision_ledger_schema_version": signal["decision_ledger_schema_version"],
            "decision_ledger_record_type": signal["decision_ledger_record_type"],
            "decision_ledger_runtime_mode": signal["decision_ledger_runtime_mode"],
            "decision_ledger_identity_verified": True,
            "decision_feature_timestamp_utc": _time_iso(
                signal["decision_feature_timestamp"]
            ),
            "decision_feature_hash": signal["decision_feature_hash"],
            "decision_model_id": signal["decision_model_id"],
            "decision_model_version": signal["decision_model_version"],
            "decision_model_hash": signal["decision_model_hash"],
            "lineage_attestation_present": signal["lineage_attestation"] is not None,
            "lineage_attestation_sha256": signal["lineage_attestation_sha256"],
            "pair": signal["pair"],
            "symbol": signal["symbol"],
            "side": signal["side"],
            "signal_generated_at_utc": _time_iso(signal["generated_at"]),
            "decision_timestamp_utc": _time_iso(signal["decision_timestamp"]),
            "signal_valid_until_utc": _time_iso(signal["valid_until"]),
            "observed_at_utc": _time_iso(observation_time),
            "score_completed_at_utc": _time_iso(score_completed_at),
            "scoring_latency_seconds": round(scoring_latency_seconds, 6),
            "signal_active_at_observation": True,
            "signal_active_at_score_completion": True,
            "post_outcome_fields_present": False,
            "signal_snapshot_sha256": signal["signal_snapshot_sha256"],
            "pit_feature_ts_utc": _time_iso(aligned.get("__market_feature_ts")),
            "pit_feature_available_at_utc": _time_iso(
                aligned.get("__market_feature_available_at")
            ),
            "feature_vector": {
                column: round(float(vector[column]), 12) for column in feature_columns
            },
            "feature_vector_sha256": prospective._sha256_json(
                {
                    column: round(float(vector[column]), 12)
                    for column in feature_columns
                }
            ),
            "score": round(float(score), 12),
            "threshold": round(threshold, 12),
            "eligible": signal["side"] == "long",
            "selected": selected,
        }
        observation["observation_id"] = _observation_id(observation)
        observation["observation_sha256"] = _observation_sha256(observation)
        observations.append(observation)

    return {
        **_common_report(
            freeze_path=resolved_freeze_path,
            signals_path=resolved_signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha,
            prospective_start_utc=boundary,
            observer_run_at_utc=observation_time,
        ),
        "status": "observing",
        "reason": "authoritative_post_freeze_signals_scored_before_outcomes",
        "decision": "COLETAR_EVIDENCIA_PROSPECTIVA",
        "decision_ledger_source_path": str(resolved_decision_ledger_path),
        "predictor_mode": predictor_mode,
        "native_qlib_used": native_qlib_used,
        "model": model_metadata,
        "freeze_spec_verified": True,
        "model_reconstruction_verified": True,
        "calibration_score_fingerprint_sha256": reconstructed_fingerprint,
        "score_completed_at_utc": _time_iso(score_completed_at),
        "scoring_latency_seconds": round(scoring_latency_seconds, 6),
        "input_signal_count": len(extracted),
        "sealed_decision_record_count": len(extracted),
        "decision_ledger_identity_verified": True,
        "observation_count": len(observations),
        "selected_signal_count": sum(1 for item in observations if item["selected"]),
        "observations": observations,
        "blockers": [],
    }


def _validate_freeze(
    freeze: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    blockers: list[str] = []
    if not freeze:
        return None, ["certified_freeze_spec_missing"]
    if freeze.get("schema_version") != prospective.SCHEMA_VERSION:
        blockers.append("certified_freeze_schema_mismatch")
    if freeze.get("freeze_provenance_complete") is not True:
        blockers.append("certified_freeze_provenance_incomplete")

    policy_sha = _nonempty_text(freeze.get("policy_sha256"))
    if policy_sha is None:
        blockers.append("certified_freeze_policy_sha256_missing")
    else:
        contract = {key: value for key, value in freeze.items() if key != "policy_sha256"}
        if prospective._sha256_json(contract) != policy_sha:
            blockers.append("certified_freeze_policy_sha256_mismatch")

    try:
        boundary = prospective._parse_boundary(freeze.get("prospective_start_utc"))
    except (TypeError, ValueError):
        boundary = None
        blockers.append("certified_freeze_boundary_invalid")

    feature_columns_raw = freeze.get("feature_columns")
    if not isinstance(feature_columns_raw, list) or not feature_columns_raw:
        feature_columns: tuple[str, ...] = ()
        blockers.append("certified_freeze_feature_columns_missing")
    else:
        feature_columns = tuple(str(item) for item in feature_columns_raw)
        if len(set(feature_columns)) != len(feature_columns):
            blockers.append("certified_freeze_feature_columns_duplicate")

    medians_raw = freeze.get("feature_medians")
    feature_medians: dict[str, float] = {}
    if not isinstance(medians_raw, Mapping):
        blockers.append("certified_freeze_feature_medians_missing")
    else:
        for column in feature_columns:
            value = base._numeric(medians_raw.get(column))
            if value is None:
                blockers.append(f"certified_freeze_feature_median_invalid:{column}")
            else:
                feature_medians[column] = float(value)

    threshold = base._numeric(freeze.get("threshold"))
    if threshold is None:
        blockers.append("certified_freeze_threshold_invalid")

    stress_bps = base._numeric(freeze.get("additional_execution_stress_bps"))
    if stress_bps is None or stress_bps < 0:
        blockers.append("certified_freeze_stress_bps_invalid")

    calibration_fingerprint = _nonempty_text(
        freeze.get("calibration_score_fingerprint_sha256")
    )
    if calibration_fingerprint is None or len(calibration_fingerprint) != 64:
        blockers.append("certified_freeze_calibration_score_fingerprint_missing")

    dataset_sha = _nonempty_text(freeze.get("pre_boundary_dataset_sha256"))
    if dataset_sha is None or len(dataset_sha) != 64:
        blockers.append("certified_freeze_dataset_sha256_missing")

    if (
        blockers
        or boundary is None
        or policy_sha is None
        or threshold is None
        or stress_bps is None
    ):
        return None, list(dict.fromkeys(blockers))
    return {
        "boundary": boundary,
        "policy_sha256": policy_sha,
        "threshold": threshold,
        "stress_bps": stress_bps,
        "feature_columns": feature_columns,
        "feature_medians": feature_medians,
    }, []


def _extract_authoritative_signals(
    *,
    payload: Mapping[str, Any],
    boundary: datetime,
    observed_at: datetime,
    decision_ledger_path: Path,
    decision_ledger_records: Sequence[Mapping[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    raw_signals = payload.get("signals", [])
    if not isinstance(raw_signals, list):
        return [], ["active_signal_payload_signals_must_be_list"]

    preliminary: list[dict[str, Any]] = []
    blockers: list[str] = []
    seen_signal_ids: set[str] = set()
    seen_decision_ids: set[str] = set()

    for index, raw in enumerate(raw_signals):
        if not isinstance(raw, Mapping):
            blockers.append(f"active_signal_not_mapping:{index}")
            continue
        try:
            signal = _extract_one_signal(raw, boundary=boundary, observed_at=observed_at)
        except ValueError as exc:
            blockers.append(f"active_signal_invalid:{index}:{str(exc)}")
            continue
        if signal is None:
            continue
        if signal["signal_id"] in seen_signal_ids:
            blockers.append(f"duplicate_signal_id:{signal['signal_id']}")
            continue
        if signal["decision_event_id"] in seen_decision_ids:
            blockers.append(f"duplicate_decision_event_id:{signal['decision_event_id']}")
            continue
        seen_signal_ids.add(signal["signal_id"])
        seen_decision_ids.add(signal["decision_event_id"])
        preliminary.append(signal)

    if blockers:
        return [], list(dict.fromkeys(blockers))
    if not preliminary:
        return [], []

    records, ledger_blockers = _load_authoritative_decision_records(
        decision_ledger_path=decision_ledger_path,
        decision_ledger_records=decision_ledger_records,
        target_event_ids={item["decision_event_id"] for item in preliminary},
    )
    if ledger_blockers:
        return [], ledger_blockers

    extracted: list[dict[str, Any]] = []
    for signal in preliminary:
        record = records.get(signal["decision_event_id"])
        if record is None:
            blockers.append(
                f"decision_ledger_record_missing:{signal['decision_event_id']}"
            )
            continue
        try:
            extracted.append(_bind_sealed_decision_record(signal, record))
        except ValueError as exc:
            blockers.append(
                f"decision_ledger_binding_invalid:{signal['decision_event_id']}:{exc}"
            )

    if blockers:
        return [], list(dict.fromkeys(blockers))
    extracted.sort(key=lambda item: (item["decision_timestamp"], item["signal_id"]))
    return extracted, []


def _extract_one_signal(
    raw: Mapping[str, Any],
    *,
    boundary: datetime,
    observed_at: datetime,
) -> dict[str, Any] | None:
    if raw.get("risk_approved") is not True:
        return None

    envelope = raw.get(DECISION_LEDGER_KEY)
    if not isinstance(envelope, Mapping):
        raise ValueError("decision_ledger_envelope_missing")
    decision_timestamp = _required_utc(
        envelope.get("decision_timestamp"),
        "decision_timestamp",
    )
    if decision_timestamp <= boundary:
        return None

    forbidden = sorted(
        field for field in FORBIDDEN_POST_OUTCOME_FIELDS if field in raw
    )
    if forbidden:
        raise ValueError("post_outcome_fields_forbidden:" + ",".join(forbidden))

    candidate_id = _required_text(raw, "candidate_id")
    signal_id = _required_text(raw, "signal_id")
    correlation_id = _required_text(raw, "correlation_id")
    pair = _required_text(raw, "pair")
    symbol = internal_symbol(str(raw.get("symbol") or pair))
    if not symbol:
        raise ValueError("symbol_missing")
    side = _required_text(raw, "side").lower()
    if side not in {"long", "short"}:
        raise ValueError("side_invalid")

    decision_event_id = _required_text(envelope, "decision_event_id")
    decision_payload_sha256 = _required_sha256(envelope, "decision_payload_sha256")
    for field, expected in (
        ("candidate_id", candidate_id),
        ("signal_id", signal_id),
        ("correlation_id", correlation_id),
    ):
        if _required_text(envelope, field) != expected:
            raise ValueError(f"decision_ledger_identity_mismatch:{field}")

    generated_at = _required_utc(raw.get("generated_at"), "generated_at")
    valid_until = _required_utc(raw.get("valid_until"), "valid_until")
    if generated_at > decision_timestamp:
        raise ValueError("signal_generated_after_decision")
    if observed_at < decision_timestamp:
        raise ValueError("observer_timestamp_before_decision")
    if valid_until < decision_timestamp:
        raise ValueError("signal_valid_until_before_decision")
    if observed_at > valid_until:
        raise ValueError("signal_expired_before_observation")

    attestation, attestation_sha256 = _validate_optional_attestation(
        raw=raw,
        candidate_id=candidate_id,
        signal_id=signal_id,
        correlation_id=correlation_id,
    )

    expected_feature_timestamp = _first_present_timestamp(
        raw,
        "feature_timestamp",
        "prediction_timestamp",
        "date",
        "generated_at",
    )
    model_version = _nonempty_text(raw.get("model_version"))
    raw_score = base._numeric(raw.get("score"))

    return {
        "candidate_id": candidate_id,
        "signal_id": signal_id,
        "correlation_id": correlation_id,
        "decision_event_id": decision_event_id,
        "decision_payload_sha256": decision_payload_sha256,
        "pair": pair,
        "symbol": symbol,
        "side": side,
        "generated_at": generated_at,
        "decision_timestamp": decision_timestamp,
        "valid_until": valid_until,
        "expected_feature_timestamp": expected_feature_timestamp,
        "model_version": model_version,
        "raw_score": raw_score,
        "lineage_attestation": attestation,
        "lineage_attestation_sha256": attestation_sha256,
    }


def _validate_optional_attestation(
    *,
    raw: Mapping[str, Any],
    candidate_id: str,
    signal_id: str,
    correlation_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    attestation = raw.get(ATTESTATION_KEY)
    if attestation is None:
        return None, None
    if not isinstance(attestation, Mapping):
        raise ValueError("lineage_attestation_invalid_type")
    if attestation.get("schema_version") != ATTESTATION_SCHEMA:
        raise ValueError("lineage_attestation_schema_invalid")
    if attestation.get("prospective_only") is not True:
        raise ValueError("lineage_attestation_not_prospective")
    if attestation.get("authoritative_identity") is not True:
        raise ValueError("lineage_attestation_not_authoritative")
    if attestation.get("synthetic_identity") is not False:
        raise ValueError("lineage_attestation_synthetic")
    if attestation.get("trade_id_used_as_candidate_id") is not False:
        raise ValueError("lineage_attestation_trade_id_alias")
    for hash_field in (
        "materialization_sha256",
        "source_signal_sha256",
        "research_candidate_sha256",
        "registry_candidate_sha256",
    ):
        _required_sha256(attestation, hash_field)
    _required_text(attestation, "research_signal_candidate_id")
    _required_text(attestation, "signal_instance_id")
    _required_text(attestation, "producer_id")
    for field, expected in (
        ("candidate_id", candidate_id),
        ("signal_id", signal_id),
        ("correlation_id", correlation_id),
    ):
        if _required_text(attestation, field) != expected:
            raise ValueError(f"lineage_attestation_identity_mismatch:{field}")
    materialized = dict(attestation)
    return materialized, prospective._sha256_json(materialized)


def _first_present_timestamp(source: Mapping[str, Any], *fields: str) -> datetime:
    for field in fields:
        value = source.get(field)
        if value is not None and str(value).strip():
            return _required_utc(value, field)
    raise ValueError("feature_timestamp_source_missing")


def _load_authoritative_decision_records(
    *,
    decision_ledger_path: Path,
    decision_ledger_records: Sequence[Mapping[str, Any]] | None,
    target_event_ids: set[str],
) -> tuple[dict[str, DecisionRecordV42], list[str]]:
    if not target_event_ids:
        return {}, []

    raw_records: list[tuple[int, Mapping[str, Any]]] = []
    blockers: list[str] = []
    if decision_ledger_records is not None:
        for index, raw in enumerate(decision_ledger_records, start=1):
            if not isinstance(raw, Mapping):
                blockers.append(f"decision_ledger_injected_record_not_mapping:{index}")
                continue
            raw_records.append((index, raw))
    else:
        if not decision_ledger_path.exists() or not decision_ledger_path.is_file():
            return {}, [f"decision_ledger_source_missing:{decision_ledger_path}"]
        with decision_ledger_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    raw = json.loads(text)
                except json.JSONDecodeError:
                    blockers.append(f"decision_ledger_json_invalid:{line_number}")
                    continue
                if not isinstance(raw, Mapping):
                    blockers.append(f"decision_ledger_record_not_mapping:{line_number}")
                    continue
                raw_records.append((line_number, raw))

    if blockers:
        return {}, list(dict.fromkeys(blockers))

    found: dict[str, DecisionRecordV42] = {}
    seen_target_ids: set[str] = set()
    for line_number, raw in raw_records:
        event_id = _nonempty_text(raw.get("event_id"))
        if event_id not in target_event_ids:
            continue
        if event_id in seen_target_ids:
            blockers.append(f"decision_ledger_duplicate_event_id:{event_id}")
            continue
        seen_target_ids.add(event_id)
        try:
            parsed = parse_payload_record(dict(raw))
        except (TypeError, ValueError) as exc:
            blockers.append(
                f"decision_ledger_record_invalid:{event_id}:{type(exc).__name__}"
            )
            continue
        if not isinstance(parsed, DecisionRecordV42):
            blockers.append(f"decision_ledger_target_not_decision:{event_id}")
            continue
        found[event_id] = parsed

    for event_id in sorted(target_event_ids - set(found)):
        blockers.append(f"decision_ledger_record_missing:{event_id}")

    if blockers:
        return {}, list(dict.fromkeys(blockers))
    return found, []


def _bind_sealed_decision_record(
    signal: Mapping[str, Any],
    record: DecisionRecordV42,
) -> dict[str, Any]:
    expected = {
        "event_id": signal["decision_event_id"],
        "candidate_id": signal["candidate_id"],
        "signal_id": signal["signal_id"],
        "correlation_id": signal["correlation_id"],
        "pair": signal["pair"],
        "symbol": signal["symbol"],
        "side": signal["side"],
    }
    actual = {
        "event_id": record.event_id,
        "candidate_id": record.candidate_id,
        "signal_id": record.signal_id,
        "correlation_id": record.correlation_id,
        "pair": record.pair,
        "symbol": record.symbol,
        "side": record.side.value,
    }
    for field, expected_value in expected.items():
        if str(actual[field]) != str(expected_value):
            raise ValueError(f"sealed_decision_identity_mismatch:{field}")

    if record.schema_version != DECISION_IDENTITY_SCHEMA_VERSION:
        raise ValueError("sealed_decision_schema_mismatch")
    if record.record_type != "decision":
        raise ValueError("sealed_decision_record_type_invalid")
    if record.runtime_mode != "paper":
        raise ValueError("sealed_decision_runtime_mode_invalid")
    if record.risk_decision is not RiskDecision.APPROVED:
        raise ValueError("sealed_decision_risk_not_approved")
    if record.final_decision is not FinalDecision.ALLOW:
        raise ValueError("sealed_decision_final_not_allow")
    if record.payload_sha256 != signal["decision_payload_sha256"]:
        raise ValueError("sealed_decision_payload_sha256_mismatch")
    if record.decision_timestamp != signal["decision_timestamp"]:
        raise ValueError("sealed_decision_timestamp_mismatch")
    if record.feature_timestamp != signal["expected_feature_timestamp"]:
        raise ValueError("sealed_decision_feature_timestamp_mismatch")

    model_version = signal.get("model_version")
    if model_version is not None and record.model_version != model_version:
        raise ValueError("sealed_decision_model_version_mismatch")
    raw_score = signal.get("raw_score")
    if raw_score is not None and not math.isclose(
        float(record.qlib_score),
        float(raw_score),
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("sealed_decision_qlib_score_mismatch")

    bound = dict(signal)
    bound.update(
        {
            "decision_ledger_schema_version": record.schema_version,
            "decision_ledger_record_type": record.record_type,
            "decision_ledger_runtime_mode": record.runtime_mode,
            "decision_feature_timestamp": record.feature_timestamp,
            "decision_feature_hash": record.feature_hash,
            "decision_model_id": record.model_id,
            "decision_model_version": record.model_version,
            "decision_model_hash": record.model_hash,
            "decision_ledger_identity_verified": True,
        }
    )
    bound["signal_snapshot_sha256"] = _signal_snapshot_sha256(
        signal=bound,
    )
    return bound

def _signal_alignment_row(signal: Mapping[str, Any]) -> dict[str, Any]:
    timestamp = signal["decision_timestamp"]
    return {
        "side": signal["side"],
        "__symbol": signal["symbol"],
        "__open_time": timestamp,
        "__close_time": timestamp + timedelta(seconds=1),
        "__observer_signal_id": signal["signal_id"],
    }


def _validate_reconstructed_freeze_inputs(
    *,
    freeze: Mapping[str, Any],
    prepared: base.PreparedFold,
) -> list[str]:
    blockers: list[str] = []
    if list(prepared.feature_columns) != list(freeze.get("feature_columns", [])):
        blockers.append("frozen_feature_columns_reconstruction_mismatch")
    if len(prepared.fit_rows) != int(freeze.get("fit_trade_count", -1)):
        blockers.append("frozen_fit_trade_count_reconstruction_mismatch")
    if len(prepared.calibration_rows) != int(freeze.get("calibration_trade_count", -1)):
        blockers.append("frozen_calibration_trade_count_reconstruction_mismatch")
    if prospective._dataset_fingerprint(prepared) != freeze.get(
        "pre_boundary_dataset_sha256"
    ):
        blockers.append("frozen_dataset_reconstruction_mismatch")

    actual_medians = prospective._feature_medians(prepared)
    expected_medians = freeze.get("feature_medians")
    if not isinstance(expected_medians, Mapping):
        blockers.append("frozen_feature_medians_missing")
    else:
        for column in prepared.feature_columns:
            expected = base._numeric(expected_medians.get(column))
            actual = float(actual_medians[column])
            if expected is None or not math.isclose(
                actual,
                expected,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                blockers.append(f"frozen_feature_median_reconstruction_mismatch:{column}")
    return list(dict.fromkeys(blockers))


def _observation_id(observation: Mapping[str, Any]) -> str:
    raw = "|".join(
        (
            str(observation["policy_sha256"]),
            str(observation["signal_id"]),
            str(observation["decision_event_id"]),
        )
    )
    return "qlibv2-sig-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _observation_sha256(observation: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in observation.items()
        if key not in {"observation_sha256", "ledger_recorded_at_utc"}
    }
    return prospective._sha256_json(payload)


def _signal_snapshot_sha256(*, signal: Mapping[str, Any]) -> str:
    payload = {
        "candidate_id": signal.get("candidate_id"),
        "signal_id": signal.get("signal_id"),
        "correlation_id": signal.get("correlation_id"),
        "decision_event_id": signal.get("decision_event_id"),
        "decision_payload_sha256": signal.get("decision_payload_sha256"),
        "pair": signal.get("pair"),
        "symbol": signal.get("symbol"),
        "side": signal.get("side"),
        "generated_at": _time_iso(signal.get("generated_at")),
        "decision_timestamp": _time_iso(signal.get("decision_timestamp")),
        "valid_until": _time_iso(signal.get("valid_until")),
        "decision_feature_timestamp": _time_iso(
            signal.get("decision_feature_timestamp")
        ),
        "decision_feature_hash": signal.get("decision_feature_hash"),
        "decision_model_id": signal.get("decision_model_id"),
        "decision_model_version": signal.get("decision_model_version"),
        "decision_model_hash": signal.get("decision_model_hash"),
        "lineage_attestation_sha256": signal.get("lineage_attestation_sha256"),
    }
    return prospective._sha256_json(payload)

def _required_sha256(source: Mapping[str, Any], field: str) -> str:
    value = _required_text(source, field).lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"invalid_sha256:{field}")
    return value


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, Mapping) else {}


def _required_text(source: Mapping[str, Any], field: str) -> str:
    value = _nonempty_text(source.get(field))
    if value is None:
        raise ValueError(f"required_text_missing:{field}")
    return value


def _nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _required_utc(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"timestamp_missing:{field}")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp_not_timezone_aware:{field}")
    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"timestamp_not_utc:{field}")
    return parsed.astimezone(UTC)


def _time_iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return None


def _common_report(
    *,
    freeze_path: Path,
    signals_path: Path,
    outcome_source: Path,
    market_source: Path,
    policy_sha256: str | None,
    prospective_start_utc: datetime | None,
    observer_run_at_utc: datetime | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "freeze_spec_path": str(freeze_path),
        "active_signals_path": str(signals_path),
        "outcome_source_path": str(outcome_source),
        "market_features_source_path": str(market_source),
        "policy_sha256": policy_sha256,
        "prospective_start_utc": _time_iso(prospective_start_utc),
        "observer_run_at_utc": _time_iso(observer_run_at_utc),
        "outcomes_read_for_frozen_model_reconstruction_only": True,
        "post_boundary_outcomes_used_for_training": False,
        "post_boundary_outcomes_used_for_selection": False,
        "active_signal_identity_source": (
            "active_signal_envelope+sealed_decision_ledger_v4_2"
        ),
        "decision_identity_schema_version": DECISION_IDENTITY_SCHEMA_VERSION,
        "lineage_attestation_required": False,
        "lineage_attestation_optional_and_strict_if_present": True,
        "sealed_decision_ledger_required": True,
        **SAFETY_FLAGS,
        "write_performed": False,
    }


def _blocked_report(
    *,
    reason: str,
    blockers: Sequence[str],
    freeze_path: Path,
    signals_path: Path,
    outcome_source: Path,
    market_source: Path,
    policy_sha256: str | None = None,
    prospective_start_utc: datetime | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **_common_report(
            freeze_path=freeze_path,
            signals_path=signals_path,
            outcome_source=outcome_source,
            market_source=market_source,
            policy_sha256=policy_sha256,
            prospective_start_utc=prospective_start_utc,
        ),
        "status": "blocked",
        "reason": reason,
        "decision": "MANTER_EM_RESEARCH",
        "native_qlib_used": False,
        "freeze_spec_verified": (
            policy_sha256 is not None and prospective_start_utc is not None
        ),
        "decision_ledger_identity_verified": False,
        "model_reconstruction_verified": False,
        "input_signal_count": 0,
        "observation_count": 0,
        "selected_signal_count": 0,
        "observations": [],
        "diagnostics": dict(diagnostics or {}),
        "blockers": list(dict.fromkeys(str(item) for item in blockers)),
    }
