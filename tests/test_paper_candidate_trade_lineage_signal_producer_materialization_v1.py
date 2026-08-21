from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1 import (
    ATTESTATION_KEY,
    materialize_signal_batch_from_explicit_provenance,
)
from smartcrypto.execution.signal_risk_gate import RiskGateResult
from smartcrypto.execution.signal_producer import build_active_signals


def _signal_candidate_id(
    candidate_id: str,
    candidate_type: str,
    source_id: str,
    threshold: float,
    actionability: str,
) -> str:
    raw = "|".join(
        str(item)
        for item in (
            candidate_id,
            candidate_type,
            source_id,
            threshold,
            actionability,
        )
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"signal_candidate_{digest[:16]}"


def _research_registry() -> tuple[dict[str, Any], dict[str, Any]]:
    candidate_id = "candidate-1"
    candidate_type = "ensemble_threshold_candidate"
    source_id = "ensemble_threshold_calibration"
    threshold = 0.55
    actionability = "research_observation_only"
    signal_candidate_id = _signal_candidate_id(
        candidate_id,
        candidate_type,
        source_id,
        threshold,
        actionability,
    )
    research = {
        "signal_candidates": [
            {
                "signal_candidate_id": signal_candidate_id,
                "source_candidate_id": candidate_id,
                "source_model_candidate_type": candidate_type,
                "source_id": source_id,
                "symbol_scope": ["BTCUSDT"],
                "side_scope": ["long"],
                "regime_scope": ["normal"],
                "threshold": threshold,
                "signal_direction": "long",
                "signal_actionability": actionability,
                "blocked_reasons": [],
            }
        ]
    }
    registry = {
        "candidates": [
            {
                "candidate_id": candidate_id,
                "candidate_type": candidate_type,
                "source_id": source_id,
                "symbol_scope": ["BTCUSDT"],
                "side_scope": ["long"],
                "regime_scope": ["normal"],
                "threshold": threshold,
            }
        ]
    }
    return research, registry


def _source_row(**overrides: Any) -> dict[str, Any]:
    research, _ = _research_registry()
    candidate = research["signal_candidates"][0]
    row = {
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "score": 0.90,
        "confidence": 0.85,
        "model_version": "qlib-test-v1",
        "source_candidate_id": candidate["source_candidate_id"],
        "signal_candidate_id": candidate["signal_candidate_id"],
        "signal_instance_id": "prediction-occurrence-0001",
        "signal_timestamp_utc": "2026-08-21T18:00:00Z",
        "regime": "normal",
    }
    row.update(overrides)
    return row


def _signal() -> dict[str, Any]:
    return {
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": "long",
        "score": 0.90,
        "confidence": 0.85,
        "prob_up": None,
        "predicted_direction": 1,
        "leverage": 2.0,
        "max_position_usdt": 50.0,
        "model_version": "qlib-test-v1",
        "generated_at": "2026-08-21T18:01:00+00:00",
        "valid_until": "2026-08-21T18:31:00+00:00",
        "source": "qlib",
    }


def test_explicit_provenance_materializes_authoritative_identity() -> None:
    research, registry = _research_registry()
    outcome = materialize_signal_batch_from_explicit_provenance(
        signals=[_signal()],
        source_rows=[_source_row()],
        research_report=research,
        registry_report=registry,
        producer_id="phase13-signal-producer",
    )

    assert outcome.report.status == "ok"
    assert outcome.report.materialized_count == 1
    enriched = outcome.signals[0]
    assert enriched["candidate_id"] == "candidate-1"
    assert enriched["signal_id"].startswith("signal:")
    assert enriched["correlation_id"].startswith("correlation:")
    assert enriched["signal_id"] != _source_row()["signal_candidate_id"]
    assert enriched[ATTESTATION_KEY]["authoritative_identity"] is True


def test_same_explicit_occurrence_is_deterministic() -> None:
    research, registry = _research_registry()
    kwargs = {
        "signals": [_signal()],
        "source_rows": [_source_row()],
        "research_report": research,
        "registry_report": registry,
        "producer_id": "phase13-signal-producer",
    }
    first = materialize_signal_batch_from_explicit_provenance(**kwargs)
    second = materialize_signal_batch_from_explicit_provenance(**kwargs)
    for field in ("candidate_id", "signal_id", "correlation_id"):
        assert first.signals[0][field] == second.signals[0][field]


def test_missing_signal_instance_id_blocks_lineage_not_signal() -> None:
    research, registry = _research_registry()
    row = _source_row()
    row.pop("signal_instance_id")
    baseline = _signal()

    outcome = materialize_signal_batch_from_explicit_provenance(
        signals=[baseline],
        source_rows=[row],
        research_report=research,
        registry_report=registry,
        producer_id="phase13-signal-producer",
    )

    assert outcome.report.status == "blocked"
    assert outcome.signals == (baseline,)
    assert outcome.report.failures[0]["reason"] == (
        "explicit_source_lineage_field_missing:signal_instance_id"
    )


def test_missing_signal_timestamp_has_no_wall_clock_fallback() -> None:
    research, registry = _research_registry()
    row = _source_row()
    row.pop("signal_timestamp_utc")

    outcome = materialize_signal_batch_from_explicit_provenance(
        signals=[_signal()],
        source_rows=[row],
        research_report=research,
        registry_report=registry,
        producer_id="phase13-signal-producer",
    )

    assert outcome.report.status == "blocked"
    assert outcome.report.failures[0]["reason"] == (
        "explicit_source_lineage_field_missing:signal_timestamp_utc"
    )


def test_candidate_identity_is_not_inferred_from_scope() -> None:
    research, registry = _research_registry()
    baseline = _signal()

    outcome = materialize_signal_batch_from_explicit_provenance(
        signals=[baseline],
        source_rows=[
            _source_row(source_candidate_id="candidate-does-not-exist")
        ],
        research_report=research,
        registry_report=registry,
        producer_id="phase13-signal-producer",
    )

    assert outcome.report.status == "blocked"
    assert outcome.signals == (baseline,)
    assert outcome.report.failures[0]["reason"] == (
        "explicit_research_candidate_identity_not_found"
    )


def test_post_outcome_source_field_blocks_lineage_not_execution() -> None:
    research, registry = _research_registry()
    baseline = _signal()
    outcome = materialize_signal_batch_from_explicit_provenance(
        signals=[baseline],
        source_rows=[_source_row(realized_pnl=3.14)],
        research_report=research,
        registry_report=registry,
        producer_id="phase13-signal-producer",
    )

    assert outcome.report.status == "blocked"
    assert outcome.signals == (baseline,)
    assert outcome.report.failures[0]["reason"].startswith(
        "source_row_contains_post_outcome_field:"
    )


@dataclass(frozen=True)
class _Prepared:
    signals: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _ObsReport:
    publication_blocked: bool = False

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "status": "ok",
            "reason": None,
            "publication_blocked": self.publication_blocked,
            "writer_invoked": False,
            "writes_runtime": False,
        }


@dataclass(frozen=True)
class _Obs:
    active_signals: tuple[dict[str, Any], ...]
    report: _ObsReport


def _approve(signals: list[dict[str, Any]]) -> RiskGateResult:
    approved = []
    for item in signals:
        stamped = dict(item)
        stamped.update(
            {
                "risk_approved": True,
                "risk_reasons": [],
                "risk_checked_at_utc": "2026-08-21T18:00:01Z",
                "risk_policy_id": "risk:test",
                "risk_config_hash": "a" * 64,
                "risk_manager_source": "test-risk-manager",
            }
        )
        approved.append(stamped)
    return RiskGateResult(
        status="ok",
        reason=None,
        risk_manager_available=True,
        risk_manager_source="test-risk-manager",
        risk_limits_path="risk.yml",
        risk_config_hash="a" * 64,
        signals_submitted=len(signals),
        signals_approved=len(approved),
        signals_rejected=0,
        approved_signals=approved,
        rejected_signals=[],
    )


def _producer_config() -> dict[str, Any]:
    return {
        "runtime_mode": "paper",
        "source": "qlib",
        "model_version_default": "qlib-test-v1",
        "paths": {
            "predictions": "predictions.parquet",
            "primary_signals": "primary.json",
            "pinned_signals": "pinned.json",
            "report": "report.json",
            "risk_limits": "risk.yml",
            "lineage_research_candidates": "research.json",
            "lineage_registry_candidates": "registry.json",
        },
        "policy": {
            "min_abs_score": 0.0,
            "min_confidence": 0.0,
            "max_signals": 1,
            "include_top_n_when_threshold_empty": 1,
            "never_overwrite_with_empty": False,
            "max_prediction_age_minutes": 90,
            "max_input_data_age_minutes": 15,
        },
        "risk": {"max_position_usdt": 50.0, "leverage": 2.0},
    }


def _install_stubs(monkeypatch, row: dict[str, Any]):
    research, registry = _research_registry()
    writes: dict[str, dict[str, Any]] = {}
    seen: dict[str, Any] = {}

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.inspect_qlib_prediction_freshness",
        lambda *args, **kwargs: {
            "freshness_status": "fresh",
            "input_data_status": "input_data_fresh",
            "rows": 1,
        },
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.load_predictions",
        lambda *args, **kwargs: pd.DataFrame([row]),
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.read_json",
        lambda path: (
            research
            if str(path) == "research.json"
            else registry
            if str(path) == "registry.json"
            else {}
        ),
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.apply_paper_candidate_filter_to_signals",
        lambda signals, runtime_mode: {
            "allowed_signals": [dict(item) for item in signals],
            "runtime_mode": runtime_mode,
        },
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.summarize_runtime_wiring",
        lambda result: {"status": "test"},
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.prepare_before_risk_manager",
        lambda signals, *, producer_id: _Prepared(
            signals=tuple(dict(item) for item in signals)
        ),
    )

    def risk_gate(signals, *, risk_limits_path):
        del risk_limits_path
        seen["risk_input"] = [dict(item) for item in signals]
        return _approve(seen["risk_input"])

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.apply_risk_manager_gate",
        risk_gate,
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.finalize_after_risk_manager",
        lambda prepared, *, risk_gate: _Obs(
            active_signals=tuple(risk_gate.approved_signals),
            report=_ObsReport(),
        ),
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.atomic_write_json",
        lambda path, payload: writes.__setitem__(str(path), dict(payload)),
    )
    return writes, seen


def test_producer_passes_materialized_identity_to_riskmanager(
    monkeypatch,
) -> None:
    writes, seen = _install_stubs(monkeypatch, _source_row())
    report = build_active_signals(
        _producer_config(),
        force_from_predictions=True,
    )

    materialization = report["paper_candidate_lineage_materialization"]
    assert materialization["status"] == "ok"
    assert materialization["materialized_count"] == 1

    risk_input = seen["risk_input"][0]
    assert risk_input["candidate_id"] == "candidate-1"
    assert risk_input["signal_id"].startswith("signal:")
    assert risk_input["correlation_id"].startswith("correlation:")
    assert risk_input[ATTESTATION_KEY]["prospective_only"] is True
    assert writes["primary.json"]["signals"][0]["candidate_id"] == "candidate-1"


def test_producer_missing_provenance_preserves_operational_baseline(
    monkeypatch,
) -> None:
    row = _source_row()
    for field in (
        "source_candidate_id",
        "signal_candidate_id",
        "signal_instance_id",
        "signal_timestamp_utc",
        "regime",
    ):
        row.pop(field, None)

    writes, seen = _install_stubs(monkeypatch, row)
    report = build_active_signals(
        _producer_config(),
        force_from_predictions=True,
    )

    materialization = report["paper_candidate_lineage_materialization"]
    assert materialization["status"] == "blocked"
    assert materialization["materialized_count"] == 0
    assert report["status"] == "ok"
    assert report["signals_after"] == 1

    risk_input = seen["risk_input"][0]
    assert "candidate_id" not in risk_input
    assert "signal_id" not in risk_input
    assert "correlation_id" not in risk_input
    assert writes["primary.json"]["signals"][0]["risk_approved"] is True


def test_static_producer_requires_explicit_lineage_provenance() -> None:
    source = Path("smartcrypto/execution/signal_producer.py").read_text(
        encoding="utf-8"
    )
    materializer = Path(
        "smartcrypto/execution/"
        "paper_candidate_trade_lineage_propagation_v1/"
        "producer_materialization.py"
    ).read_text(encoding="utf-8")

    assert "materialize_signal_batch_from_explicit_provenance" in source
    for field in (
        "source_candidate_id",
        "signal_candidate_id",
        "signal_instance_id",
        "signal_timestamp_utc",
    ):
        assert field in materializer

    assert '"signal_timestamp_utc": generated_at' not in source
    assert 'source_row.get("generated_at")' not in materializer
