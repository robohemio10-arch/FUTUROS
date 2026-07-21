"""Machine-readable authoritative field-source registry for P0.4B."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

PROFILE_VERSION = "decision_ledger_runtime_observability_profile_v1"

REQUIRED_TARGET_FIELDS: tuple[str, ...] = (
    "schema_version",
    "record_type",
    "event_id",
    "parent_event_id",
    "signal_id",
    "candidate_id",
    "trade_id",
    "correlation_id",
    "idempotency_key",
    "runtime_mode",
    "pair",
    "symbol",
    "side",
    "feature_timestamp",
    "decision_timestamp",
    "execution_timestamp",
    "feature_contract_version",
    "feature_hash",
    "model_id",
    "model_version",
    "model_hash",
    "qlib_score",
    "calibrated_probability",
    "expected_net_pnl",
    "fast_stop_probability",
    "regime",
    "alignment",
    "ai_shadow_decision",
    "ai_shadow_reasons",
    "risk_decision",
    "risk_reasons",
    "approved_stake_usdt",
    "approved_leverage",
    "final_decision",
    "final_reasons",
    "operational_authority",
    "runtime_integration",
    "sends_orders",
    "exchange_private_access",
    "payload_sha256",
)

SourceKind = Literal[
    "constant",
    "candidate_signal",
    "feature_lineage",
    "model_lineage",
    "ai_shadow_result",
    "risk_gate_result",
    "final_decision_orchestrator",
    "deterministic_identity",
    "authoritative_trade_feedback",
    "canonical_serializer",
]
AbsencePolicy = Literal[
    "forbidden",
    "nullable_explicit",
    "decision_only_none",
    "trade_link_required",
]


@dataclass(frozen=True)
class FieldSourceSpec:
    field: str
    source_kind: SourceKind
    authoritative_path: str
    source_key: str
    absence_policy: AbsencePolicy
    transformation: str
    record_types: tuple[str, ...]


def _spec(
    field: str,
    source_kind: SourceKind,
    authoritative_path: str,
    source_key: str,
    absence_policy: AbsencePolicy,
    transformation: str,
    record_types: tuple[str, ...] = ("decision", "trade_link"),
) -> FieldSourceSpec:
    return FieldSourceSpec(
        field=field,
        source_kind=source_kind,
        authoritative_path=authoritative_path,
        source_key=source_key,
        absence_policy=absence_policy,
        transformation=transformation,
        record_types=record_types,
    )


FIELD_SOURCE_REGISTRY: tuple[FieldSourceSpec, ...] = (
    _spec("schema_version", "constant", "decision_ledger_v4_2.contracts", "SCHEMA_VERSION", "forbidden", "fixed decision_ledger_payload_v4_2"),
    _spec("record_type", "constant", "mapping adapter", "projection_type", "forbidden", "decision or trade_link by adapter"),
    _spec("event_id", "deterministic_identity", "identifiers.py", "event_id", "forbidden", "sha256-derived identifier"),
    _spec("parent_event_id", "deterministic_identity", "mapping adapter", "decision_event_id", "decision_only_none", "None for decision; parent decision event for trade_link"),
    _spec("signal_id", "candidate_signal", "signal producer candidate", "signal_id", "forbidden", "strip and validate"),
    _spec("candidate_id", "candidate_signal", "signal producer candidate", "candidate_id", "forbidden", "strip and validate"),
    _spec("trade_id", "authoritative_trade_feedback", "Freqtrade paper DB reconciliation", "trade_id", "decision_only_none", "None for decision; positive integer for trade_link"),
    _spec("correlation_id", "candidate_signal", "signal producer candidate", "correlation_id", "forbidden", "strip and validate"),
    _spec("idempotency_key", "deterministic_identity", "identifiers.py", "idempotency_key", "forbidden", "canonical sha256 over authoritative identity fields"),
    _spec("runtime_mode", "constant", "runtime profile", "runtime_mode", "forbidden", "fixed paper"),
    _spec("pair", "candidate_signal", "signal producer candidate", "pair", "forbidden", "preserve canonical pair"),
    _spec("symbol", "candidate_signal", "signal producer candidate", "symbol", "forbidden", "verify against normalized pair"),
    _spec("side", "candidate_signal", "signal producer candidate", "side", "forbidden", "strict long or short"),
    _spec("feature_timestamp", "feature_lineage", "feature contract output", "feature_timestamp", "forbidden", "UTC offset zero", ("decision",)),
    _spec("decision_timestamp", "final_decision_orchestrator", "final decision boundary", "decision_timestamp", "forbidden", "UTC offset zero"),
    _spec("execution_timestamp", "authoritative_trade_feedback", "Freqtrade paper DB reconciliation", "execution_timestamp", "decision_only_none", "None for decision; UTC timestamp for trade_link"),
    _spec("feature_contract_version", "feature_lineage", "feature contract output", "feature_contract_version", "forbidden", "validate identifier", ("decision",)),
    _spec("feature_hash", "feature_lineage", "feature contract output", "feature_hash", "forbidden", "lowercase sha256", ("decision",)),
    _spec("model_id", "model_lineage", "model/registry evidence", "model_id", "forbidden", "validate identifier", ("decision",)),
    _spec("model_version", "model_lineage", "model/registry evidence", "model_version", "forbidden", "validate identifier", ("decision",)),
    _spec("model_hash", "model_lineage", "model/registry evidence", "model_hash", "forbidden", "lowercase sha256", ("decision",)),
    _spec("qlib_score", "model_lineage", "Qlib candidate output", "qlib_score", "forbidden", "finite float", ("decision",)),
    _spec("calibrated_probability", "model_lineage", "calibration output", "calibrated_probability", "nullable_explicit", "probability or explicit null", ("decision",)),
    _spec("expected_net_pnl", "model_lineage", "economic scoring output", "expected_net_pnl", "nullable_explicit", "finite float or explicit null", ("decision",)),
    _spec("fast_stop_probability", "model_lineage", "fast-stop model output", "fast_stop_probability", "nullable_explicit", "probability or explicit null", ("decision",)),
    _spec("regime", "feature_lineage", "regime classifier output", "regime", "forbidden", "non-empty text", ("decision",)),
    _spec("alignment", "feature_lineage", "directional alignment output", "alignment", "forbidden", "enum mapping", ("decision",)),
    _spec("ai_shadow_decision", "ai_shadow_result", "AI Shadow observation", "ai_shadow_decision", "forbidden", "strict enum", ("decision",)),
    _spec("ai_shadow_reasons", "ai_shadow_result", "AI Shadow observation", "ai_shadow_reasons", "forbidden", "ordered immutable reasons", ("decision",)),
    _spec("risk_decision", "risk_gate_result", "signal_risk_gate.py", "risk_approved", "forbidden", "True -> APPROVED; False -> REJECTED", ("decision",)),
    _spec("risk_reasons", "risk_gate_result", "signal_risk_gate.py", "risk_reasons", "forbidden", "ordered immutable reasons", ("decision",)),
    _spec("approved_stake_usdt", "risk_gate_result", "RiskManager approved signal", "approved_stake_usdt", "forbidden", "positive for ALLOW; zero for BLOCK", ("decision",)),
    _spec("approved_leverage", "risk_gate_result", "RiskManager approved signal", "approved_leverage", "forbidden", "positive for ALLOW; zero for BLOCK", ("decision",)),
    _spec("final_decision", "final_decision_orchestrator", "final decision boundary", "final_decision", "forbidden", "RiskManager remains final authority", ("decision",)),
    _spec("final_reasons", "final_decision_orchestrator", "final decision boundary", "final_reasons", "forbidden", "ordered immutable reasons", ("decision",)),
    _spec("operational_authority", "constant", "runtime profile", "operational_authority", "forbidden", "fixed false"),
    _spec("runtime_integration", "constant", "runtime profile", "runtime_integration", "forbidden", "fixed false in P0.4B"),
    _spec("sends_orders", "constant", "runtime profile", "sends_orders", "forbidden", "fixed false"),
    _spec("exchange_private_access", "constant", "runtime profile", "exchange_private_access", "forbidden", "fixed false"),
    _spec("payload_sha256", "canonical_serializer", "decision_ledger_v4_2.serialization", "payload_sha256", "forbidden", "canonical JSON sha256 excluding self field"),
)

ENVELOPE_EXTENSION_FIELDS: tuple[str, ...] = (
    "profile_version",
    "activation_state",
    "risk_checked_at_utc",
    "risk_policy_id",
    "risk_config_hash",
    "source_signal_sha256",
    "source_database_sha256",
    "source_table",
    "source_row_fingerprint",
    "field_source_registry_sha256",
    "mapping_input_sha256",
    "writer_invoked",
)


def registry_payload() -> dict[str, object]:
    validate_registry()
    return {
        "schema_version": "decision_ledger_field_source_registry_v1",
        "profile_version": PROFILE_VERSION,
        "required_target_field_count": len(REQUIRED_TARGET_FIELDS),
        "required_target_fields": list(REQUIRED_TARGET_FIELDS),
        "envelope_extension_fields": list(ENVELOPE_EXTENSION_FIELDS),
        "fields": [asdict(item) for item in FIELD_SOURCE_REGISTRY],
    }


def registry_sha256() -> str:
    payload = json.dumps(
        registry_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_registry() -> None:
    names = [item.field for item in FIELD_SOURCE_REGISTRY]
    if len(names) != len(set(names)):
        raise ValueError("field_source_registry_contains_duplicates")
    if tuple(names) != REQUIRED_TARGET_FIELDS:
        missing = sorted(set(REQUIRED_TARGET_FIELDS) - set(names))
        unexpected = sorted(set(names) - set(REQUIRED_TARGET_FIELDS))
        raise ValueError(
            f"field_source_registry_mismatch:missing={missing}:unexpected={unexpected}"
        )
