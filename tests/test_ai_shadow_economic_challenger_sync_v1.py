from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from smartcrypto.execution.decision_ledger_v4_2.contracts import seal_decision_record
from smartcrypto.learning.qlib_v3_prospective import store
from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL
from smartcrypto.research.aibot_parity.ai_shadow_economic_challenger import (
    AIShadowEconomicChallengerError,
    evaluate_evidence_state,
)

BASE = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _decision(index: int, shadow_decision: str):
    signal_id = f"signal-{index}"
    event_id = f"decision-{index}"
    score = 0.20 if shadow_decision == "ALLOW" else -0.20
    reasons = (
        ["v3_score_gte_frozen_threshold"]
        if shadow_decision == "ALLOW"
        else ["v3_score_lt_frozen_threshold"]
    )
    return seal_decision_record(
        {
            "event_id": event_id,
            "signal_id": signal_id,
            "candidate_id": f"candidate-{index}",
            "correlation_id": f"correlation-{index}",
            "idempotency_key": f"decision-{index}",
            "runtime_mode": "paper",
            "pair": "BTC/USDT:USDT",
            "symbol": "BTCUSDT",
            "side": "long",
            "feature_timestamp": BASE + timedelta(minutes=index),
            "decision_timestamp": BASE + timedelta(minutes=index, seconds=1),
            "feature_contract_version": "qlib_v3_reproducible_freeze_epoch_v1",
            "feature_hash": f"{index + 1:x}" * 64,
            "model_id": "qlib_v3_economic_shadow",
            "model_version": "v3",
            "model_hash": CANONICAL.model_artifact_sha256,
            "qlib_score": score,
            "calibrated_probability": None,
            "expected_net_pnl": None,
            "fast_stop_probability": None,
            "regime": "unit_test",
            "alignment": "unknown",
            "ai_shadow_decision": shadow_decision,
            "ai_shadow_reasons": reasons,
            "risk_decision": "APPROVED",
            "risk_reasons": ["risk_manager_approved"],
            "approved_stake_usdt": 25.0,
            "approved_leverage": 2.0,
            "final_decision": "ALLOW",
            "final_reasons": ["paper_approved"],
            "operational_authority": False,
            "runtime_integration": False,
            "sends_orders": False,
            "exchange_private_access": False,
        }
    )


def _state() -> dict[str, object]:
    decisions = [
        _decision(1, "ABSTAIN"),
        _decision(2, "ALLOW"),
        _decision(3, "ABSTAIN"),
        _decision(4, "ALLOW"),
    ]
    pnl = [-10.0, 8.0, 5.0, -2.0]
    durations = [5, 10, 20, 45]

    signals = []
    outcomes = []
    for index, (decision, value, minutes) in enumerate(
        zip(decisions, pnl, durations, strict=True),
        start=1,
    ):
        opened = BASE + timedelta(hours=index)
        closed = opened + timedelta(minutes=minutes)
        signals.append(
            {
                "signal_id": decision.signal_id,
                "decision_event_id": decision.event_id,
                "decision": decision.model_dump(mode="json"),
            }
        )
        outcomes.append(
            {
                "signal_id": decision.signal_id,
                "decision_event_id": decision.event_id,
                "trade_id": index,
                "open_time_utc": opened.isoformat(),
                "close_time_utc": closed.isoformat(),
                "is_closed": True,
                "net_pnl": value,
            }
        )

    return {
        "schema_version": store.SCHEMA,
        "identity": CANONICAL.mapping(),
        "signals": signals,
        "outcomes": outcomes,
    }


def test_control_treatment_metrics_are_deterministic() -> None:
    state = _state()
    first = evaluate_evidence_state(state)
    second = evaluate_evidence_state(deepcopy(state))

    assert first == second
    assert first["status"] == "ok"
    assert first["resolved_trade_count"] == 4

    comparison = first["global_comparison"]
    assert comparison["control"]["net_pnl"] == pytest.approx(1.0)
    assert comparison["treatment"]["net_pnl"] == pytest.approx(6.0)
    assert comparison["delta_net_pnl"] == pytest.approx(5.0)
    assert comparison["control"]["profit_factor"] == pytest.approx(13.0 / 12.0)
    assert comparison["treatment"]["profit_factor"] == pytest.approx(4.0)
    assert comparison["coverage"] == pytest.approx(0.5)
    assert comparison["bad_trade_avoidance_rate"] == pytest.approx(0.5)
    assert comparison["false_abstention_rate"] == pytest.approx(0.5)
    assert comparison["profitable_trade_retention_rate"] == pytest.approx(0.5)
    assert comparison["economic_direction"] == "positive"


def test_branch08_priority_under_15m_is_measured_separately() -> None:
    report = evaluate_evidence_state(_state())

    assert report["branch08_priority"] == {
        "dimension": "duration_bucket",
        "bucket": "<15m",
    }
    priority = report["priority_segment_comparison"]
    assert priority["control"]["trade_count"] == 2
    assert priority["treatment"]["trade_count"] == 1
    assert priority["control"]["net_pnl"] == pytest.approx(-2.0)
    assert priority["treatment"]["net_pnl"] == pytest.approx(8.0)
    assert priority["delta_net_pnl"] == pytest.approx(10.0)


def test_no_resolved_outcomes_is_waiting_not_promoted() -> None:
    state = _state()
    state["outcomes"] = []

    report = evaluate_evidence_state(state)

    assert report["status"] == "waiting"
    assert report["reason"] == "no_resolved_v3_shadow_outcomes"
    assert report["resolved_trade_count"] == 0
    assert report["operational_authority"] is False
    assert report["model_promotion_performed"] is False
    assert report["sends_orders"] is False


def test_identity_or_causal_conflict_fails_closed() -> None:
    bad_identity = _state()
    bad_identity["identity"] = dict(CANONICAL.mapping())
    bad_identity["identity"]["model_artifact_sha256"] = "f" * 64

    with pytest.raises(
        AIShadowEconomicChallengerError,
        match="evidence_identity_mismatch",
    ):
        evaluate_evidence_state(bad_identity)

    duplicate = _state()
    duplicate["outcomes"].append(deepcopy(duplicate["outcomes"][0]))
    with pytest.raises(
        AIShadowEconomicChallengerError,
        match="multiple_outcomes_for_signal",
    ):
        evaluate_evidence_state(duplicate)
