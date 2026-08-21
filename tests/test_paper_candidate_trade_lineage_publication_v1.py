from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1 import (
    ATTESTATION_KEY,
    CandidateLineageError,
    ConcreteSignalOccurrenceV1,
    attach_materialized_identity_to_signal,
    materialize_concrete_signal_identity,
    select_non_blocking_paper_publication_signals,
)


def _signal_candidate_id(parts: tuple[Any, ...]) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"signal_candidate_{digest[:16]}"


def _research_candidate() -> dict[str, Any]:
    source_candidate_id = "candidate-registry-alpha"
    candidate_type = "threshold_candidate"
    source_id = "qlib-shadow"
    threshold = 0.61
    actionability = "research_observation_only"
    return {
        "source_candidate_id": source_candidate_id,
        "signal_candidate_id": _signal_candidate_id(
            (source_candidate_id, candidate_type, source_id, threshold, actionability)
        ),
        "source_model_candidate_type": candidate_type,
        "source_id": source_id,
        "threshold": threshold,
        "signal_actionability": actionability,
        "symbol_scope": ["BTCUSDT"],
        "side_scope": ["long"],
        "regime_scope": ["trend"],
        "signal_direction": "long",
    }


def _registry_candidate() -> dict[str, Any]:
    return {
        "candidate_id": "candidate-registry-alpha",
        "candidate_type": "threshold_candidate",
    }


def _binding():
    occurrence = ConcreteSignalOccurrenceV1(
        producer_id="phase13-signal-producer",
        signal_instance_id="instance-0001",
        signal_timestamp_utc=datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc),
        pair="BTC/USDT:USDT",
        symbol="BTCUSDT",
        side="long",
        regime="trend",
        occurrence_source_sha256="1" * 64,
    )
    return materialize_concrete_signal_identity(
        _research_candidate(),
        _registry_candidate(),
        occurrence,
        producer_id="phase13-signal-producer",
    )


def _approved_signal() -> dict[str, Any]:
    base = {
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "score": 0.90,
        "confidence": 0.82,
        "risk_approved": True,
        "risk_manager_source": "smartcrypto.risk.risk_manager.RiskManager",
        "risk_reasons": [],
        "max_position_usdt": 50.0,
        "leverage": 2.0,
    }
    return attach_materialized_identity_to_signal(base, _binding())


@dataclass
class _RiskGate:
    status: str
    approved_signals: list[dict[str, Any]]


@dataclass
class _Report:
    publication_blocked: bool


@dataclass
class _Observability:
    active_signals: list[dict[str, Any]]
    report: _Report


def _observed(signal: dict[str, Any]) -> dict[str, Any]:
    output = dict(signal)
    output.update(
        {
            "approved_stake_usdt": 50.0,
            "approved_leverage": 2.0,
            "final_decision": "ALLOW",
            "final_reasons": ["risk_manager_approved"],
        }
    )
    output["decision_ledger"] = {
        "decision_event_id": "decision:event:abc123",
        "decision_payload_sha256": "2" * 64,
        "candidate_id": signal["candidate_id"],
        "signal_id": signal["signal_id"],
        "correlation_id": signal["correlation_id"],
        "decision_timestamp": "2026-08-21T18:00:01+00:00",
    }
    return output


def test_attach_materialized_identity_preserves_execution_fields() -> None:
    base = {
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "score": 0.9,
    }
    enriched = attach_materialized_identity_to_signal(base, _binding())

    for key, value in base.items():
        assert enriched[key] == value

    assert enriched["candidate_id"] == "candidate-registry-alpha"
    assert enriched["signal_id"].startswith("signal:")
    assert enriched["correlation_id"].startswith("correlation:")
    assert enriched[ATTESTATION_KEY]["authoritative_identity"] is True
    assert enriched[ATTESTATION_KEY]["synthetic_identity"] is False


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("pair", "ETH/USDT:USDT", "concrete_signal_pair_binding_mismatch"),
        ("symbol", "ETHUSDT", "concrete_signal_symbol_binding_mismatch"),
        ("side", "short", "concrete_signal_side_binding_mismatch"),
    ],
)
def test_attach_materialized_identity_rejects_execution_scope_drift(
    field: str,
    value: str,
    reason: str,
) -> None:
    signal = {
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
    }
    signal[field] = value

    with pytest.raises(CandidateLineageError, match=reason):
        attach_materialized_identity_to_signal(signal, _binding())


def test_attach_materialized_identity_rejects_identity_override() -> None:
    signal = {
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "candidate_id": "forged-candidate",
    }

    with pytest.raises(
        CandidateLineageError,
        match="concrete_signal_identity_conflict:candidate_id",
    ):
        attach_materialized_identity_to_signal(signal, _binding())


def test_observability_block_preserves_risk_approved_baseline_exactly() -> None:
    approved = _approved_signal()
    result = select_non_blocking_paper_publication_signals(
        risk_gate=_RiskGate(status="ok", approved_signals=[approved]),
        observability=_Observability(active_signals=[], report=_Report(publication_blocked=True)),
    )

    assert result.status == "baseline_preserved"
    assert result.attribution_evidence_blocked is True
    assert result.active_signals == (approved,)
    assert result.lineage_envelope_count == 0
    assert result.to_dict()["publication_blocked_by_lineage"] is False


def test_observability_count_mismatch_preserves_baseline() -> None:
    approved = _approved_signal()
    result = select_non_blocking_paper_publication_signals(
        risk_gate=_RiskGate(status="ok", approved_signals=[approved]),
        observability=_Observability(active_signals=[], report=_Report(publication_blocked=False)),
    )

    assert result.status == "baseline_preserved"
    assert result.reason == "lineage_observability_count_mismatch_baseline_preserved"
    assert result.active_signals == (approved,)


def test_valid_envelope_adds_only_decision_ledger_to_baseline() -> None:
    approved = _approved_signal()
    observed = _observed(approved)

    result = select_non_blocking_paper_publication_signals(
        risk_gate=_RiskGate(status="ok", approved_signals=[approved]),
        observability=_Observability(active_signals=[observed], report=_Report(publication_blocked=False)),
    )

    assert result.status == "lineage_propagated"
    assert result.attribution_evidence_blocked is False
    assert result.lineage_envelope_count == 1

    published = result.active_signals[0]
    assert published["decision_ledger"] == observed["decision_ledger"]
    assert set(published) == set(approved) | {"decision_ledger"}

    for key, value in approved.items():
        assert published[key] == value

    assert "approved_stake_usdt" not in published
    assert "final_decision" not in published


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pair", "ETH/USDT:USDT"),
        ("side", "short"),
        ("score", 0.1),
        ("risk_approved", False),
    ],
)
def test_observability_cannot_change_risk_approved_baseline(
    field: str,
    value: Any,
) -> None:
    approved = _approved_signal()
    observed = _observed(approved)
    observed[field] = value

    result = select_non_blocking_paper_publication_signals(
        risk_gate=_RiskGate(status="ok", approved_signals=[approved]),
        observability=_Observability(active_signals=[observed], report=_Report(publication_blocked=False)),
    )

    assert result.status == "baseline_preserved"
    assert result.active_signals == (approved,)
    assert result.attribution_evidence_blocked is True


def test_missing_attestation_blocks_attribution_not_execution() -> None:
    approved = _approved_signal()
    approved.pop(ATTESTATION_KEY)
    observed = _observed(approved)

    result = select_non_blocking_paper_publication_signals(
        risk_gate=_RiskGate(status="ok", approved_signals=[approved]),
        observability=_Observability(active_signals=[observed], report=_Report(publication_blocked=False)),
    )

    assert result.status == "baseline_preserved"
    assert result.reason == "lineage_attestation_missing:0"
    assert result.active_signals == (approved,)


def test_decision_envelope_identity_mismatch_blocks_attribution_not_execution() -> None:
    approved = _approved_signal()
    observed = _observed(approved)
    observed["decision_ledger"]["candidate_id"] = "different-candidate"

    result = select_non_blocking_paper_publication_signals(
        risk_gate=_RiskGate(status="ok", approved_signals=[approved]),
        observability=_Observability(active_signals=[observed], report=_Report(publication_blocked=False)),
    )

    assert result.status == "baseline_preserved"
    assert result.reason == "decision_ledger_identity_mismatch:0:candidate_id"
    assert result.active_signals == (approved,)


def test_risk_gate_failure_still_blocks_execution() -> None:
    result = select_non_blocking_paper_publication_signals(
        risk_gate=_RiskGate(status="blocked", approved_signals=[]),
        observability=_Observability(active_signals=[], report=_Report(publication_blocked=True)),
    )

    assert result.status == "blocked"
    assert result.active_signals == ()
    assert result.reason == "risk_gate_not_ok"


def test_no_risk_approved_signals_is_empty_not_lineage_failure() -> None:
    result = select_non_blocking_paper_publication_signals(
        risk_gate=_RiskGate(status="ok", approved_signals=[]),
        observability=_Observability(active_signals=[], report=_Report(publication_blocked=False)),
    )

    assert result.status == "empty"
    assert result.attribution_evidence_blocked is False
    assert result.active_signals == ()


def test_static_boundary_has_no_runtime_writer_or_order_capability_imports() -> None:
    import ast
    from pathlib import Path

    source_path = Path(
        "smartcrypto/execution/paper_candidate_trade_lineage_propagation_v1/publication.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))

    forbidden_module_prefixes = (
        "freqtrade",
        "ccxt",
        "redis",
        "sqlite3",
        "smartcrypto.execution.decision_ledger_paper_runtime_writer_v1",
    )
    forbidden_call_names = {
        "create_paper_runtime_writer",
        "send_order",
        "submit_order",
    }

    imported_modules: list[str] = []
    called_names: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.append(node.module)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called_names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.append(node.func.attr)

    for module_name in imported_modules:
        assert not any(
            module_name == prefix or module_name.startswith(f"{prefix}.")
            for prefix in forbidden_module_prefixes
        ), f"forbidden runtime capability import: {module_name}"

    assert forbidden_call_names.isdisjoint(called_names)
