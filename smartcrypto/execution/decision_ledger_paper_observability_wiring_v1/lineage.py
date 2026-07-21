"""Deterministic pre-RiskManager lineage preparation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from .contracts import PaperObservabilityWiringConfigV1, PreparedSignalBatchV1


def prepare_signal_batch(
    signals: Sequence[Mapping[str, Any]],
    *,
    producer_id: str,
    config: PaperObservabilityWiringConfigV1,
) -> PreparedSignalBatchV1:
    source = [dict(item) for item in signals]
    if not config.enabled:
        return PreparedSignalBatchV1(
            producer_id=producer_id,
            enabled=False,
            source_signal_count=len(source),
            prepared_signal_count=len(source),
            signals=tuple(source),
            config=config,
            lineage_built_before_risk_manager=False,
        )

    prepared = tuple(
        _prepare_signal(item, producer_id=producer_id, config=config)
        for item in source
    )
    return PreparedSignalBatchV1(
        producer_id=producer_id,
        enabled=True,
        source_signal_count=len(source),
        prepared_signal_count=len(prepared),
        signals=prepared,
        config=config,
        lineage_built_before_risk_manager=True,
    )


def complete_after_risk_manager(
    signal: Mapping[str, Any],
    *,
    expected_approved: bool,
) -> dict[str, object]:
    completed: dict[str, object] = dict(signal)
    risk_reasons = tuple(str(item) for item in signal.get("risk_reasons", ()))
    if expected_approved:
        completed["approved_stake_usdt"] = _finite_float(
            signal.get("approved_stake_usdt", signal.get("max_position_usdt"))
        )
        completed["approved_leverage"] = _finite_float(
            signal.get("approved_leverage", signal.get("leverage"))
        )
        completed["final_decision"] = "ALLOW"
        completed["final_reasons"] = tuple(
            str(item) for item in signal.get("final_reasons", ("risk_manager_approved",))
        )
    else:
        completed["approved_stake_usdt"] = 0.0
        completed["approved_leverage"] = 0.0
        completed["final_decision"] = "BLOCK"
        completed["final_reasons"] = risk_reasons or ("risk_manager_rejected",)
    return completed


def canonical_observation_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _normalize(dict(payload)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prepare_signal(
    signal: dict[str, Any],
    *,
    producer_id: str,
    config: PaperObservabilityWiringConfigV1,
) -> dict[str, object]:
    original_hash = canonical_observation_sha256(signal)
    short_hash = original_hash[:40]
    prepared: dict[str, object] = dict(signal)
    prepared.setdefault("signal_id", f"signal:{producer_id}:{short_hash}")
    prepared.setdefault("candidate_id", f"candidate:{producer_id}:{short_hash}")
    prepared.setdefault("correlation_id", f"correlation:{short_hash}")

    feature_timestamp = _first_value(
        signal,
        "feature_timestamp",
        "prediction_timestamp",
        "date",
        "generated_at",
    )
    if feature_timestamp is not None:
        prepared.setdefault("feature_timestamp", feature_timestamp)
    prepared.setdefault("feature_contract_version", config.feature_contract_version)
    prepared.setdefault("feature_hash", original_hash)

    model_version = str(signal.get("model_version") or "").strip()
    if config.model_id is not None:
        prepared.setdefault("model_id", config.model_id)
    elif model_version:
        prepared.setdefault("model_id", model_version)
    if config.model_hash is not None:
        prepared.setdefault("model_hash", config.model_hash)

    prepared.setdefault("qlib_score", _finite_float(signal.get("score")))
    prepared.setdefault("calibrated_probability", signal.get("calibrated_probability"))
    prepared.setdefault("expected_net_pnl", signal.get("expected_net_pnl"))
    prepared.setdefault("fast_stop_probability", signal.get("fast_stop_probability"))
    prepared.setdefault("regime", str(signal.get("market_regime") or "unknown"))
    prepared.setdefault("alignment", str(signal.get("alignment") or "unknown"))
    prepared.setdefault("ai_shadow_decision", "NOT_EVALUATED")
    prepared.setdefault("ai_shadow_reasons", ())
    prepared["decision_lineage_sha256"] = canonical_observation_sha256(prepared)
    return prepared


def _first_value(source: Mapping[str, Any], *keys: str) -> object | None:
    for key in keys:
        value = source.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _finite_float(value: object) -> float:
    if value is None:
        return 0.0
    number = float(str(value))
    if not (-float("inf") < number < float("inf")):
        raise ValueError("non_finite_lineage_number")
    return number


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("lineage_timestamp_must_be_timezone_aware")
        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    return value
