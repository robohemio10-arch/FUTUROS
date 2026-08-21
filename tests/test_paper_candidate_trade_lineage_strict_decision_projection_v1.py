from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1 import (
    ATTESTATION_KEY,
    project_strict_decision_envelopes_in_memory,
)


def _signal_candidate_id() -> str:
    payload = "|".join(
        str(item)
        for item in (
            "candidate-1",
            "ensemble_threshold_candidate",
            "ensemble_threshold_calibration",
            0.55,
            "research_observation_only",
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"signal_candidate_{digest[:16]}"


def _approved_signal() -> dict[str, Any]:
    return {
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "score": 0.90,
        "confidence": 0.85,
        "leverage": 2.0,
        "max_position_usdt": 50.0,
        "model_version": "qlib-test-v1",
        "candidate_id": "candidate-1",
        "signal_id": "signal:phase13-signal-producer:abc123",
        "correlation_id": "correlation:abc123",
        ATTESTATION_KEY: {
            "schema_version": "paper_candidate_trade_lineage_signal_attestation_v1",
            "materialization_sha256": "1" * 64,
            "source_signal_sha256": "2" * 64,
            "research_candidate_sha256": "3" * 64,
            "registry_candidate_sha256": "4" * 64,
            "candidate_id": "candidate-1",
            "signal_id": "signal:phase13-signal-producer:abc123",
            "correlation_id": "correlation:abc123",
            "research_signal_candidate_id": _signal_candidate_id(),
            "signal_instance_id": "prediction-occurrence-0001",
            "producer_id": "phase13-signal-producer",
            "prospective_only": True,
            "authoritative_identity": True,
            "synthetic_identity": False,
            "trade_id_used_as_candidate_id": False,
        },
        "risk_approved": True,
        "risk_reasons": [],
        "risk_checked_at_utc": "2026-08-21T18:00:01Z",
        "risk_policy_id": "risk:test",
        "risk_config_hash": "a" * 64,
        "risk_manager_source": "test-risk-manager",
    }


def _source_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "source_candidate_id": "candidate-1",
        "signal_candidate_id": _signal_candidate_id(),
        "signal_instance_id": "prediction-occurrence-0001",
        "signal_timestamp_utc": "2026-08-21T17:59:59Z",
        "feature_timestamp_utc": "2026-08-21T17:59:58Z",
        "feature_contract_version": "feature-contract-v1",
        "feature_hash": "b" * 64,
        "model_id": "qlib-ranking-model",
        "model_hash": "c" * 64,
        "regime": "normal",
        "alignment": "aligned",
        "ai_shadow_decision": "NOT_EVALUATED",
        "ai_shadow_reasons": [],
        "calibrated_probability": 0.72,
        "expected_net_pnl": 0.12,
        "fast_stop_probability": 0.18,
    }
    row.update(overrides)
    return row


def test_strict_decision_projection_creates_decision_event_in_memory() -> None:
    outcome = project_strict_decision_envelopes_in_memory(
        approved_signals=[_approved_signal()],
        source_rows=[_source_row()],
        decision_timestamp_utc=datetime(
            2026,
            8,
            21,
            18,
            0,
            2,
            tzinfo=timezone.utc,
        ),
        producer_id="phase13-signal-producer",
    )

    assert outcome.report.status == "ok"
    assert outcome.report.projected_decision_count == 1
    assert outcome.report.publication_blocked is False

    envelope = outcome.active_signals[0]["decision_ledger"]
    assert envelope["decision_event_id"].startswith("decision-event:")
    assert len(envelope["decision_payload_sha256"]) == 64
    assert envelope["candidate_id"] == "candidate-1"
    assert envelope["signal_id"] == _approved_signal()["signal_id"]
    assert envelope["correlation_id"] == _approved_signal()["correlation_id"]
    assert envelope["writer_invoked"] is False
    assert envelope["writes_runtime"] is False


def test_same_inputs_produce_same_decision_event_id() -> None:
    kwargs = {
        "approved_signals": [_approved_signal()],
        "source_rows": [_source_row()],
        "decision_timestamp_utc": datetime(
            2026,
            8,
            21,
            18,
            0,
            2,
            tzinfo=timezone.utc,
        ),
        "producer_id": "phase13-signal-producer",
    }
    first = project_strict_decision_envelopes_in_memory(**kwargs)
    second = project_strict_decision_envelopes_in_memory(**kwargs)

    assert (
        first.active_signals[0]["decision_ledger"]["decision_event_id"]
        == second.active_signals[0]["decision_ledger"]["decision_event_id"]
    )


def test_missing_feature_hash_blocks_attribution_not_execution() -> None:
    baseline = _approved_signal()
    source = _source_row()
    source.pop("feature_hash")

    outcome = project_strict_decision_envelopes_in_memory(
        approved_signals=[baseline],
        source_rows=[source],
        decision_timestamp_utc=datetime(
            2026,
            8,
            21,
            18,
            0,
            2,
            tzinfo=timezone.utc,
        ),
        producer_id="phase13-signal-producer",
    )

    assert outcome.report.status == "blocked"
    assert outcome.report.publication_blocked is True
    assert outcome.active_signals == (baseline,)
    assert "decision_ledger" not in outcome.active_signals[0]
    assert outcome.report.failures[0]["reason"] == (
        "strict_decision_required_field_missing:feature_hash"
    )


def test_duplicate_exact_source_context_blocks_batch() -> None:
    baseline = _approved_signal()
    source = _source_row()

    outcome = project_strict_decision_envelopes_in_memory(
        approved_signals=[baseline],
        source_rows=[source, dict(source)],
        decision_timestamp_utc=datetime(
            2026,
            8,
            21,
            18,
            0,
            2,
            tzinfo=timezone.utc,
        ),
        producer_id="phase13-signal-producer",
    )

    assert outcome.report.status == "blocked"
    assert outcome.report.reason == "duplicate_strict_decision_source_context"
    assert outcome.active_signals == (baseline,)


def test_decision_timestamp_before_risk_check_is_blocked() -> None:
    baseline = _approved_signal()

    outcome = project_strict_decision_envelopes_in_memory(
        approved_signals=[baseline],
        source_rows=[_source_row()],
        decision_timestamp_utc=datetime(
            2026,
            8,
            21,
            18,
            0,
            0,
            tzinfo=timezone.utc,
        ),
        producer_id="phase13-signal-producer",
    )

    assert outcome.report.status == "blocked"
    assert outcome.active_signals == (baseline,)
    assert outcome.report.failures[0]["reason"] == (
        "strict_decision_timestamp_before_risk_check"
    )


def test_non_utc_decision_timestamp_is_rejected() -> None:
    from datetime import timedelta

    import pytest

    from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1 import (
        CandidateLineageError,
    )

    with pytest.raises(
        CandidateLineageError,
        match="strict_decision_timestamp_not_utc:decision_timestamp_utc",
    ):
        project_strict_decision_envelopes_in_memory(
            approved_signals=[_approved_signal()],
            source_rows=[_source_row()],
            decision_timestamp_utc=datetime(
                2026,
                8,
                21,
                15,
                0,
                2,
                tzinfo=timezone(timedelta(hours=-3)),
            ),
            producer_id="phase13-signal-producer",
        )


def test_static_projection_module_has_no_writer_or_execution_imports() -> None:
    import ast

    path = Path(
        "smartcrypto/execution/"
        "paper_candidate_trade_lineage_propagation_v1/"
        "decision_projection.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    forbidden_prefixes = (
        "freqtrade",
        "ccxt",
        "redis",
        "sqlite3",
        "smartcrypto.execution.decision_ledger_paper_runtime_writer_v1",
        "smartcrypto.execution.signal_risk_gate",
    )

    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    for module in imported:
        assert not any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in forbidden_prefixes
        )


def test_signal_producer_uses_strict_projection_for_publication() -> None:
    source = Path("smartcrypto/execution/signal_producer.py").read_text(
        encoding="utf-8"
    )

    projection = source.index(
        "project_strict_decision_envelopes_in_memory("
    )
    publication = source.index(
        "_safe_select_paper_lineage_publication(",
        projection,
    )
    assert projection < publication
    assert "observability=strict_decision_projection" in source
    assert '"paper_candidate_strict_decision_projection"' in source
