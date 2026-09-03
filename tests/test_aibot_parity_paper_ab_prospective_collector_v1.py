from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    seal_decision_record,
    seal_trade_link_record,
)
from smartcrypto.research.aibot_parity_orchestrator.contracts import (
    REQUIRED_SOURCE_NAMES,
    AibotParityPipelineSnapshot,
    PipelineSourceView,
    PipelineStatus,
    PointInTimeStatus,
)
from smartcrypto.research.aibot_parity_paper_ab_prospective_collector import (
    capture_observations,
    collect_prospective_evidence,
    load_decision_ledger_jsonl,
    materialize_candidate_rows,
    merge_observations,
    read_observation_ledger,
    write_observations_idempotent,
)
from smartcrypto.research.aibot_parity_paper_ab_soak import build_preregistration

OBSERVED = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _preregistration():
    return build_preregistration(
        {
            "experiment_id": "aibot-parity-paper-ab-soak-v1",
            "preregistered_start_utc": "2026-09-03T19:55:50Z",
            "software_dod_merge_sha": "2daa54c47b033b5951ebf6f2ff7fa615beab3ee8",
            "assignment_salt_version": "sha256-v1",
            "minimum_observations_per_arm": 200,
            "minimum_observation_days": 45,
            "minimum_profit_factor": 1.1,
            "bootstrap_iterations": 5000,
            "bootstrap_seed": 20260820,
            "confidence_level": 0.95,
            "control_definition": "FREQTRADE_PAPER_BASELINE_OBSERVED_ONLY",
            "treatment_definition": "AIBOT_PARITY_SHADOW_COUNTERFACTUAL_ONLY",
        }
    )


def _decision(candidate_id: str = "candidate-1"):
    return seal_decision_record(
        {
            "event_id": f"decision-{candidate_id}",
            "signal_id": f"signal-{candidate_id}",
            "candidate_id": candidate_id,
            "correlation_id": f"corr-{candidate_id}",
            "idempotency_key": f"idem-{candidate_id}",
            "pair": "BTC/USDT:USDT",
            "symbol": "BTCUSDT",
            "side": "long",
            "feature_timestamp": OBSERVED - timedelta(minutes=1),
            "decision_timestamp": OBSERVED - timedelta(seconds=30),
            "feature_contract_version": "features-v1",
            "feature_hash": "1" * 64,
            "model_id": "research-model",
            "model_version": "v1",
            "model_hash": "2" * 64,
            "qlib_score": 0.2,
            "calibrated_probability": 0.6,
            "expected_net_pnl": 1.0,
            "fast_stop_probability": 0.1,
            "regime": "trend",
            "alignment": "aligned",
            "ai_shadow_decision": "ALLOW",
            "ai_shadow_reasons": [],
            "risk_decision": "APPROVED",
            "risk_reasons": [],
            "approved_stake_usdt": 10.0,
            "approved_leverage": 1.0,
            "final_decision": "ALLOW",
            "final_reasons": ["paper_baseline"],
        }
    )


def _trade_link(decision, trade_id: int = 101, *, suffix: str = ""):
    return seal_trade_link_record(
        {
            "event_id": f"trade-link-{trade_id}{suffix}",
            "parent_event_id": decision.event_id,
            "signal_id": decision.signal_id,
            "candidate_id": decision.candidate_id,
            "trade_id": trade_id,
            "correlation_id": decision.correlation_id,
            "idempotency_key": f"trade-link-idem-{trade_id}{suffix}",
            "pair": decision.pair,
            "symbol": decision.symbol,
            "side": decision.side,
            "decision_timestamp": decision.decision_timestamp,
            "execution_timestamp": decision.decision_timestamp + timedelta(seconds=5),
            "decision_payload_sha256": decision.payload_sha256,
            "link_reason": "paper_trade_created",
        }
    )


def _snapshot(
    *,
    candidate_id: str = "candidate-1",
    cycle_id: str = "aibot-parity-cycle-test-1",
    action: str = "ACCEPT",
    risk_decision: str = "ALLOW",
):
    views = tuple(
        PipelineSourceView(
            source_name=name,
            status="OK",
            point_in_time_status=PointInTimeStatus.VALID,
            source_hash=str(index + 1) * 64,
            evidence_time_utc=OBSERVED - timedelta(seconds=1),
            reason="point_in_time_valid",
        )
        for index, name in enumerate(REQUIRED_SOURCE_NAMES)
    )
    return AibotParityPipelineSnapshot(
        cycle_id=cycle_id,
        request_id=f"request-{cycle_id}",
        decision_time_utc=OBSERVED,
        created_at_utc=OBSERVED,
        status=PipelineStatus.READY_SHADOW,
        reason="counterfactual_signal_ready_after_shadow_riskmanager_allow",
        final_action="WOULD_SIGNAL",
        would_signal=True,
        qlib_status="BLOCKED_EXTERNAL",
        qlib_blocked_external=True,
        ensemble_action=action,
        riskmanager_shadow_decision=risk_decision,
        selected_candidate_ids=(candidate_id,),
        required_sources_present=tuple(REQUIRED_SOURCE_NAMES),
        missing_required_sources=(),
        blocking_reasons=(),
        source_views=views,
    )


def _closed_trade(trade_id: int = 101, *, symbol: str = "BTCUSDT"):
    return {
        "trade_id": str(trade_id),
        "symbol": symbol,
        "side": "long",
        "open_time": "2026-09-04T12:00:05Z",
        "close_time": "2026-09-04T13:00:00Z",
        "entry_price": 100.0,
        "exit_price": 101.0,
        "pnl": 1.25,
        "row_fingerprint": f"fingerprint-{trade_id}",
    }


def test_valid_explicit_link_materializes_outcome_without_starting_clock():
    decision = _decision()
    result = collect_prospective_evidence(
        preregistration=_preregistration(),
        snapshots=[_snapshot()],
        decisions=[decision],
        trade_links=[_trade_link(decision)],
        closed_trades=[_closed_trade()],
        financial_config_unchanged=True,
    )

    assert result.report["status"] == "ok"
    assert result.report["completed_outcome_count"] == 1
    assert result.candidate_rows[0]["realized_net_pnl_usdt"] == pytest.approx(1.25)
    assert result.report["collection_clock_started"] is False
    assert result.report["prospective_collection_running_proven"] is False
    assert result.report["qlib_security_gate_bypassed"] is False


def test_missing_trade_link_never_uses_symbol_or_time_heuristic():
    decision = _decision()
    observations, blockers = capture_observations(
        snapshots=[_snapshot()],
        decisions=[decision],
        financial_config_unchanged=True,
    )
    assert blockers == []

    rows, outcome_blockers, counters = materialize_candidate_rows(
        observations=observations,
        trade_links=[],
        closed_trades=[_closed_trade()],
    )
    assert outcome_blockers == []
    assert counters["pending_trade_link_count"] == 1
    assert "realized_net_pnl_usdt" not in rows[0]


def test_financial_config_requires_explicit_assertion():
    observations, blockers = capture_observations(
        snapshots=[_snapshot()],
        decisions=[_decision()],
        financial_config_unchanged=False,
    )
    assert observations == []
    assert blockers == ["FINANCIAL_CONFIG_PARITY_NOT_ASSERTED:aibot-parity-cycle-test-1"]


def test_noncanonical_action_and_accept_without_risk_allow_fail_closed():
    observations, blockers = capture_observations(
        snapshots=[_snapshot(action="ALLOW")],
        decisions=[_decision()],
        financial_config_unchanged=True,
    )
    assert observations == []
    assert blockers == ["TREATMENT_ACTION_NOT_CANONICAL:aibot-parity-cycle-test-1"]

    observations, blockers = capture_observations(
        snapshots=[_snapshot(risk_decision="BLOCK")],
        decisions=[_decision()],
        financial_config_unchanged=True,
    )
    assert observations == []
    assert blockers == ["ACCEPT_WITHOUT_SHADOW_RISK_ALLOW:candidate-1"]


def test_candidate_id_reuse_across_cycles_is_blocked():
    decision = _decision()
    first, _ = capture_observations(
        snapshots=[_snapshot(cycle_id="cycle-one")],
        decisions=[decision],
        financial_config_unchanged=True,
    )
    second, _ = capture_observations(
        snapshots=[_snapshot(cycle_id="cycle-two")],
        decisions=[decision],
        financial_config_unchanged=True,
    )

    merged, blockers = merge_observations(first, second)
    assert len(merged) == 1
    assert blockers == ["CANDIDATE_ID_REUSED_ACROSS_CYCLES:candidate-1"]


def test_conflicting_trade_links_are_blocked():
    decision = _decision()
    observations, _ = capture_observations(
        snapshots=[_snapshot()], decisions=[decision], financial_config_unchanged=True
    )
    _, blockers, _ = materialize_candidate_rows(
        observations=observations,
        trade_links=[
            _trade_link(decision, 101, suffix="-a"),
            _trade_link(decision, 102, suffix="-b"),
        ],
        closed_trades=[_closed_trade(101), _closed_trade(102)],
    )
    assert blockers == ["TRADE_LINK_CONFLICT:candidate-1"]


def test_observation_ledger_is_idempotent_and_tamper_evident(tmp_path: Path):
    observations, blockers = capture_observations(
        snapshots=[_snapshot()], decisions=[_decision()], financial_config_unchanged=True
    )
    assert blockers == []
    target = Path(
        "data/reports/aibot_parity/aibot_parity_paper_ab_prospective_observations_v1.jsonl"
    )

    assert (
        write_observations_idempotent(
            project_root=tmp_path, path=target, observations=observations
        )
        == 1
    )
    assert (
        write_observations_idempotent(
            project_root=tmp_path, path=target, observations=observations
        )
        == 0
    )
    assert read_observation_ledger(tmp_path / target) == observations

    payload = json.loads((tmp_path / target).read_text(encoding="utf-8"))
    payload["candidate_id"] = "tampered"
    (tmp_path / target).write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="observation_sha256_mismatch"):
        read_observation_ledger(tmp_path / target)


def test_writer_rejects_non_research_output_path(tmp_path: Path):
    observations, _ = capture_observations(
        snapshots=[_snapshot()], decisions=[_decision()], financial_config_unchanged=True
    )
    with pytest.raises(ValueError, match="observations_output_must_be_under"):
        write_observations_idempotent(
            project_root=tmp_path,
            path="data/runtime/forbidden.jsonl",
            observations=observations,
        )


def test_decision_ledger_loader_rejects_tampered_payload(tmp_path: Path):
    payload = _decision().model_dump(mode="json")
    payload["payload_sha256"] = "0" * 64
    path = tmp_path / "ledger.jsonl"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="decision_ledger_invalid_line:1"):
        load_decision_ledger_jsonl(path)


def test_collector_preserves_all_operational_safety_flags():
    result = collect_prospective_evidence(
        preregistration=_preregistration(),
        snapshots=[_snapshot(action="ABSTAIN")],
        decisions=[_decision()],
        trade_links=[],
        closed_trades=[],
        financial_config_unchanged=True,
    )
    for field in (
        "operational_authority",
        "traffic_split_performed",
        "paper_behavior_changed",
        "treatment_runtime_assignment_performed",
        "writes_active_signals",
        "signal_published",
        "sends_orders",
        "exchange_private_access",
        "changes_strategy",
        "changes_risk",
        "changes_stake",
        "changes_leverage",
        "changes_roi",
        "changes_stoploss",
        "changes_universe",
        "changes_model",
        "paper_treatment_release_allowed",
        "paper_activation_performed",
        "qlib_security_gate_bypassed",
    ):
        assert result.report[field] is False
