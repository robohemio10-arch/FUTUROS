"""Research-only prospective collector for AIBOT Parity Paper A/B evidence."""

from .collector import (
    CollectionResult,
    DEFAULT_OBSERVATIONS,
    DecisionLedgerRows,
    OBSERVATION_SCHEMA_VERSION,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    capture_observations,
    collect_prospective_evidence,
    load_aibot_snapshots,
    load_decision_ledger_jsonl,
    load_normalized_closed_trades,
    materialize_candidate_rows,
    merge_observations,
    read_observation_ledger,
    write_observations_idempotent,
)

__all__ = [
    "CollectionResult",
    "DEFAULT_OBSERVATIONS",
    "DecisionLedgerRows",
    "OBSERVATION_SCHEMA_VERSION",
    "SAFETY_FLAGS",
    "SCHEMA_VERSION",
    "capture_observations",
    "collect_prospective_evidence",
    "load_aibot_snapshots",
    "load_decision_ledger_jsonl",
    "load_normalized_closed_trades",
    "materialize_candidate_rows",
    "merge_observations",
    "read_observation_ledger",
    "write_observations_idempotent",
]
