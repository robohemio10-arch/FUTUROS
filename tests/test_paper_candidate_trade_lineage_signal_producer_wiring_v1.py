from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.execution.signal_risk_gate import RiskGateResult
from smartcrypto.execution.signal_producer import build_active_signals


@dataclass(frozen=True)
class _Prepared:
    signals: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _ObservabilityReport:
    publication_blocked: bool
    reason: str | None = None
    status: str = "ok"

    def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
        del mode
        return {
            "status": self.status,
            "reason": self.reason,
            "publication_blocked": self.publication_blocked,
            "writer_invoked": False,
            "writes_runtime": False,
            "paper_behavior_changed": False,
        }


@dataclass(frozen=True)
class _Observability:
    active_signals: tuple[dict[str, Any], ...]
    report: _ObservabilityReport


def _config() -> dict[str, Any]:
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
        },
        "policy": {
            "min_abs_score": 0.0,
            "min_confidence": 0.0,
            "max_signals": 2,
            "include_top_n_when_threshold_empty": 2,
            "never_overwrite_with_empty": False,
            "max_prediction_age_minutes": 90,
            "max_input_data_age_minutes": 15,
        },
        "risk": {
            "max_position_usdt": 50.0,
            "leverage": 2.0,
        },
    }


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "pair": "BTC/USDT:USDT",
                "side": "long",
                "score": 0.90,
                "confidence": 0.85,
            }
        ]
    )


def _approve(submitted: list[dict[str, Any]]) -> RiskGateResult:
    approved = []
    for item in submitted:
        signal = dict(item)
        signal.update(
            {
                "risk_approved": True,
                "risk_reasons": [],
                "risk_checked_at_utc": "2026-08-21T18:00:01Z",
                "risk_policy_id": "risk:test",
                "risk_config_hash": "a" * 64,
                "risk_manager_source": "test-risk-manager",
            }
        )
        approved.append(signal)

    return RiskGateResult(
        status="ok",
        reason=None,
        risk_manager_available=True,
        risk_manager_source="test-risk-manager",
        risk_limits_path="risk.yml",
        risk_config_hash="a" * 64,
        signals_submitted=len(submitted),
        signals_approved=len(approved),
        signals_rejected=0,
        approved_signals=approved,
        rejected_signals=[],
    )


def _block(submitted: list[dict[str, Any]]) -> RiskGateResult:
    rejected = []
    for item in submitted:
        signal = dict(item)
        signal.update(
            {
                "risk_approved": False,
                "risk_reasons": ["blocked_by_test"],
            }
        )
        rejected.append(signal)

    return RiskGateResult(
        status="blocked",
        reason="risk_manager_evaluation_failed:Test",
        risk_manager_available=True,
        risk_manager_source="test-risk-manager",
        risk_limits_path="risk.yml",
        risk_config_hash="a" * 64,
        signals_submitted=len(submitted),
        signals_approved=0,
        signals_rejected=len(rejected),
        approved_signals=[],
        rejected_signals=rejected,
    )


def _install_common(monkeypatch):
    writes: dict[str, dict[str, Any]] = {}

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
        lambda *args, **kwargs: _frame(),
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.read_json",
        lambda *args, **kwargs: {},
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
        lambda result: {"status": "test", "allowed_count": len(result["allowed_signals"])},
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.atomic_write_json",
        lambda path, payload: writes.__setitem__(str(path), dict(payload)),
    )
    return writes


def test_risk_manager_receives_operational_candidates_not_observability_mutation(
    monkeypatch,
) -> None:
    writes = _install_common(monkeypatch)
    seen: dict[str, Any] = {}

    def prepare(signals, *, producer_id):
        del producer_id
        mutated = []
        for item in signals:
            changed = dict(item)
            changed["score"] = 0.01
            changed["synthetic_observability_field"] = True
            mutated.append(changed)
        return _Prepared(signals=tuple(mutated))

    def risk_gate(signals, *, risk_limits_path):
        del risk_limits_path
        seen["submitted"] = [dict(item) for item in signals]
        return _approve(seen["submitted"])

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.prepare_before_risk_manager",
        prepare,
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.apply_risk_manager_gate",
        risk_gate,
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.finalize_after_risk_manager",
        lambda prepared, *, risk_gate: _Observability(
            active_signals=tuple(risk_gate.approved_signals),
            report=_ObservabilityReport(publication_blocked=False),
        ),
    )

    report = build_active_signals(_config(), force_from_predictions=True)

    submitted = seen["submitted"]
    assert len(submitted) == 1
    assert submitted[0]["score"] == 0.90
    assert "synthetic_observability_field" not in submitted[0]

    assert report["status"] == "ok"
    assert report["signals_after"] == 1
    assert writes["primary.json"]["signals"][0]["score"] == 0.90
    assert "synthetic_observability_field" not in writes["primary.json"]["signals"][0]


def test_observability_publication_block_does_not_cancel_riskmanager_allow(
    monkeypatch,
) -> None:
    writes = _install_common(monkeypatch)

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.prepare_before_risk_manager",
        lambda signals, *, producer_id: _Prepared(
            signals=tuple(dict(item) for item in signals)
        ),
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.apply_risk_manager_gate",
        lambda signals, *, risk_limits_path: _approve(list(signals)),
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.finalize_after_risk_manager",
        lambda prepared, *, risk_gate: _Observability(
            active_signals=(),
            report=_ObservabilityReport(
                publication_blocked=True,
                reason="decision_projection_failed",
                status="blocked",
            ),
        ),
    )

    report = build_active_signals(_config(), force_from_predictions=True)

    assert report["status"] == "ok"
    assert report["signals_after"] == 1
    assert report["decision_ledger_observability"]["publication_blocked"] is True
    assert report["paper_lineage_publication"]["status"] == "baseline_preserved"
    assert report["paper_lineage_publication"]["attribution_evidence_blocked"] is True
    assert report["paper_lineage_publication"]["publication_blocked_by_lineage"] is False
    assert len(writes["primary.json"]["signals"]) == 1
    assert writes["primary.json"]["signals"][0]["risk_approved"] is True


def test_observability_preparation_exception_blocks_attribution_not_execution(
    monkeypatch,
) -> None:
    writes = _install_common(monkeypatch)
    seen = {"risk_called": False, "finalize_called": False}

    def prepare(*args, **kwargs):
        raise RuntimeError("simulated_preparation_failure")

    def risk_gate(signals, *, risk_limits_path):
        del risk_limits_path
        seen["risk_called"] = True
        return _approve(list(signals))

    def finalize(*args, **kwargs):
        seen["finalize_called"] = True
        raise AssertionError("finalize must not run after preparation failure")

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.prepare_before_risk_manager",
        prepare,
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.apply_risk_manager_gate",
        risk_gate,
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.finalize_after_risk_manager",
        finalize,
    )

    report = build_active_signals(_config(), force_from_predictions=True)

    assert seen["risk_called"] is True
    assert seen["finalize_called"] is False
    assert report["status"] == "ok"
    assert report["signals_after"] == 1
    assert report["decision_ledger_observability"]["reason"] == (
        "lineage_preparation_failed:RuntimeError"
    )
    assert report["paper_lineage_publication"]["status"] == "baseline_preserved"
    assert len(writes["primary.json"]["signals"]) == 1


def test_observability_finalization_exception_blocks_attribution_not_execution(
    monkeypatch,
) -> None:
    writes = _install_common(monkeypatch)

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.prepare_before_risk_manager",
        lambda signals, *, producer_id: _Prepared(
            signals=tuple(dict(item) for item in signals)
        ),
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.apply_risk_manager_gate",
        lambda signals, *, risk_limits_path: _approve(list(signals)),
    )

    def finalize(*args, **kwargs):
        raise ValueError("simulated_finalization_failure")

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.finalize_after_risk_manager",
        finalize,
    )

    report = build_active_signals(_config(), force_from_predictions=True)

    assert report["status"] == "ok"
    assert report["signals_after"] == 1
    assert report["decision_ledger_observability"]["reason"] == (
        "lineage_finalization_failed:ValueError"
    )
    assert report["paper_lineage_publication"]["status"] == "baseline_preserved"
    assert len(writes["pinned.json"]["signals"]) == 1


def test_risk_gate_failure_remains_fail_closed_and_lineage_cannot_override(
    monkeypatch,
) -> None:
    writes = _install_common(monkeypatch)
    seen = {"finalize_called": False}

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.prepare_before_risk_manager",
        lambda signals, *, producer_id: _Prepared(
            signals=tuple(dict(item) for item in signals)
        ),
    )
    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.apply_risk_manager_gate",
        lambda signals, *, risk_limits_path: _block(list(signals)),
    )

    def finalize(*args, **kwargs):
        seen["finalize_called"] = True
        raise AssertionError("finalize must not run when RiskManager is blocked")

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.finalize_after_risk_manager",
        finalize,
    )

    report = build_active_signals(_config(), force_from_predictions=True)

    assert seen["finalize_called"] is False
    assert report["status"] == "blocked"
    assert report["signals_after"] == 0
    assert report["written_primary"] is False
    assert report["written_pinned"] is False
    assert report["paper_lineage_publication"]["status"] == "blocked"
    assert report["paper_lineage_publication"]["reason"] == "risk_gate_not_ok"
    assert "primary.json" not in writes
    assert "pinned.json" not in writes
    assert "report.json" in writes


def test_signal_producer_keeps_shared_coordinator_order_but_removes_lineage_veto() -> None:
    source = Path("smartcrypto/execution/signal_producer.py").read_text(encoding="utf-8")

    prepare = source.index("prepare_before_risk_manager(")
    risk = source.index("apply_risk_manager_gate(", prepare)
    finalize = source.index("finalize_after_risk_manager(", risk)

    assert prepare < risk < finalize
    assert "apply_risk_manager_gate(\n        candidate_signals," in source
    assert "observability.report.publication_blocked" not in source
    assert "select_non_blocking_paper_publication_signals" in source
    assert '"paper_lineage_publication": publication.to_dict()' in source
