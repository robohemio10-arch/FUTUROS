"""Map RiskManager-stamped signals into the certified P0.4B input contract."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from smartcrypto.execution.decision_ledger_runtime_profile_v1 import RuntimeDecisionInputV1


class SignalSourceValidationError(ValueError):
    def __init__(self, message: str, *, missing_fields: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.missing_fields = missing_fields


_REQUIRED_FIELDS: tuple[str, ...] = (
    "signal_id",
    "candidate_id",
    "correlation_id",
    "pair",
    "symbol",
    "side",
    "feature_timestamp",
    "feature_contract_version",
    "feature_hash",
    "model_id",
    "model_version",
    "model_hash",
    "qlib_score",
    "regime",
    "alignment",
    "ai_shadow_decision",
    "ai_shadow_reasons",
    "risk_approved",
    "risk_reasons",
    "risk_checked_at_utc",
    "risk_policy_id",
    "risk_config_hash",
    "approved_stake_usdt",
    "approved_leverage",
    "final_decision",
    "final_reasons",
)


def canonical_signal_sha256(signal: Mapping[str, Any]) -> str:
    normalized = _normalize(dict(signal))
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_runtime_decision_input(
    signal: Mapping[str, Any],
    *,
    decision_timestamp: datetime,
) -> RuntimeDecisionInputV1:
    missing = tuple(
        field
        for field in _REQUIRED_FIELDS
        if field not in signal or signal[field] is None
    )
    if missing:
        raise SignalSourceValidationError(
            "required_signal_lineage_missing:" + ",".join(missing),
            missing_fields=missing,
        )

    risk_approved = signal["risk_approved"]
    if type(risk_approved) is not bool:
        raise SignalSourceValidationError("risk_approved_must_be_exact_bool")

    payload = {
        "runtime_mode": "paper",
        "signal_id": signal["signal_id"],
        "candidate_id": signal["candidate_id"],
        "correlation_id": signal["correlation_id"],
        "pair": signal["pair"],
        "symbol": signal["symbol"],
        "side": signal["side"],
        "feature_timestamp": signal["feature_timestamp"],
        "decision_timestamp": decision_timestamp,
        "risk_checked_at_utc": signal["risk_checked_at_utc"],
        "feature_contract_version": signal["feature_contract_version"],
        "feature_hash": signal["feature_hash"],
        "model_id": signal["model_id"],
        "model_version": signal["model_version"],
        "model_hash": signal["model_hash"],
        "qlib_score": signal["qlib_score"],
        "calibrated_probability": signal.get("calibrated_probability"),
        "expected_net_pnl": signal.get("expected_net_pnl"),
        "fast_stop_probability": signal.get("fast_stop_probability"),
        "regime": signal["regime"],
        "alignment": signal["alignment"],
        "ai_shadow_decision": signal["ai_shadow_decision"],
        "ai_shadow_reasons": tuple(signal["ai_shadow_reasons"]),
        "risk_approved": risk_approved,
        "risk_reasons": tuple(signal["risk_reasons"]),
        "risk_policy_id": signal["risk_policy_id"],
        "risk_config_hash": signal["risk_config_hash"],
        "approved_stake_usdt": signal["approved_stake_usdt"],
        "approved_leverage": signal["approved_leverage"],
        "final_decision": signal["final_decision"],
        "final_reasons": tuple(signal["final_reasons"]),
        "source_signal_sha256": canonical_signal_sha256(signal),
        "operational_authority": False,
        "runtime_integration": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }
    return RuntimeDecisionInputV1.model_validate(payload)


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    return value
