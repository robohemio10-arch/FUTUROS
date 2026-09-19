from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from smartcrypto.research.research_council.alpha_uplift_ab import (
    ResearchCouncilAlphaUpliftError,
    build_research_council_alpha_uplift_ab_from_scorecard,
)

SCORECARD_SHA = "a" * 64
POLICY_SHA = "b" * 64
SNAPSHOT_SHA = "c" * 64
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _scorecard() -> dict[str, object]:
    return {
        "schema_version": "economic_control_treatment_scorecard_v1",
        "status": "ok",
        "decision": "SCORECARD_READY_RESEARCH_ONLY",
        "scorecard_evidence_sha256": SCORECARD_SHA,
        "source_of_financial_truth": {
            "new_financial_source_created": False,
        },
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "promotion_allowed": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_data": False,
    }


def _row(
    index: int,
    *,
    period_id: str,
    pnl: float,
    action: str,
    compute_cost: float = 0.01,
) -> dict[str, object]:
    decision = T0 + timedelta(hours=index)
    return {
        "candidate_id": f"candidate-{index}",
        "period_id": period_id,
        "symbol": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
        "side": "long" if index % 2 == 0 else "short",
        "candidate_decision_time_utc": decision.isoformat(),
        "council_available_at_utc": decision.isoformat(),
        "outcome_close_time_utc": (decision + timedelta(minutes=30)).isoformat(),
        "quant_net_pnl_after_costs": pnl,
        "council_action": action,
        "council_latency_ms": 12.0 + index,
        "council_compute_cost_usdt": compute_cost,
        "council_snapshot_id": f"snapshot-{index}",
        "council_snapshot_sha256": SNAPSHOT_SHA,
    }


def _evidence(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "research_council_alpha_uplift_ab_evidence_v1",
        "experiment_id": "council-ab-test",
        "policy_id": "frozen-veto-v1",
        "policy_sha256": POLICY_SHA,
        "policy_frozen_before_oos": True,
        "source_scorecard_evidence_sha256": SCORECARD_SHA,
        "quant_cost_basis": "net_pnl_after_quant_execution_costs",
        "compute_cost_basis": "measured_research_compute_cost_usdt",
        "rows": rows,
    }


def test_missing_ab_evidence_waits_without_inventing_uplift() -> None:
    report = build_research_council_alpha_uplift_ab_from_scorecard(
        scorecard_report=_scorecard(),
        council_ab_evidence=None,
    )

    assert report["status"] == "waiting"
    assert report["decision"] == "WAITING_FOR_COUNCIL_AB_EVIDENCE"
    assert report["paired_candidate_count"] == 0
    assert report["operational_authority"] is False
    assert report["decision_path_influence_allowed"] is False


def test_positive_repeatable_uplift_becomes_research_candidate_only() -> None:
    rows: list[dict[str, object]] = []
    index = 0
    for period in ("p1", "p2", "p3"):
        for local in range(10):
            pnl = -2.0 if local < 4 else 1.0
            action = "ABSTAIN" if pnl < 0.0 else "ALLOW"
            rows.append(_row(index, period_id=period, pnl=pnl, action=action))
            index += 1

    report = build_research_council_alpha_uplift_ab_from_scorecard(
        scorecard_report=_scorecard(),
        council_ab_evidence=_evidence(rows),
    )

    assert report["status"] == "ok"
    assert report["decision"] == "COUNCIL_UPLIFT_RESEARCH_CANDIDATE"
    assert report["uplift"]["delta_net_pnl"] > 0.0
    assert report["uplift"]["delta_ev_per_candidate"] > 0.0
    assert report["repeatability"]["repeatable_positive_uplift"] is True
    assert report["council_effectiveness"]["bad_trade_avoidance_rate"] == 1.0
    assert report["council_effectiveness"]["false_abstention_rate"] == 0.0
    assert report["decision_path_influence_allowed"] is False
    assert report["sends_orders"] is False


def test_aggregate_positive_but_not_repeatable_remains_analytical_only() -> None:
    rows: list[dict[str, object]] = []
    index = 0
    period_specs = {
        "p1": [(-10.0, "ABSTAIN")] + [(1.0, "ALLOW")] * 9,
        "p2": [(1.0, "ABSTAIN")] + [(0.5, "ALLOW")] * 9,
        "p3": [(1.0, "ABSTAIN")] + [(0.5, "ALLOW")] * 9,
    }
    for period, specs in period_specs.items():
        for pnl, action in specs:
            rows.append(
                _row(
                    index,
                    period_id=period,
                    pnl=pnl,
                    action=action,
                    compute_cost=0.0,
                )
            )
            index += 1

    report = build_research_council_alpha_uplift_ab_from_scorecard(
        scorecard_report=_scorecard(),
        council_ab_evidence=_evidence(rows),
    )

    assert report["uplift"]["delta_net_pnl"] > 0.0
    assert report["repeatability"]["positive_period_count"] == 1
    assert report["repeatability"]["repeatable_positive_uplift"] is False
    assert report["decision"] == "COUNCIL_ANALYTICAL_ONLY"


def test_compute_cost_can_erase_nominal_avoidance_edge() -> None:
    rows: list[dict[str, object]] = []
    index = 0
    for period in ("p1", "p2", "p3"):
        for local in range(10):
            pnl = -1.0 if local == 0 else 0.2
            action = "ABSTAIN" if pnl < 0.0 else "ALLOW"
            rows.append(
                _row(
                    index,
                    period_id=period,
                    pnl=pnl,
                    action=action,
                    compute_cost=0.20,
                )
            )
            index += 1

    report = build_research_council_alpha_uplift_ab_from_scorecard(
        scorecard_report=_scorecard(),
        council_ab_evidence=_evidence(rows),
    )

    assert report["compute_cost"]["total_usdt"] == pytest.approx(6.0)
    assert report["uplift"]["delta_net_pnl"] < 0.0
    assert report["decision"] == "COUNCIL_ANALYTICAL_ONLY"


def test_insufficient_candidate_sample_collects_more_evidence() -> None:
    rows = [
        _row(
            index,
            period_id=f"p{index % 3}",
            pnl=-1.0,
            action="ABSTAIN",
            compute_cost=0.0,
        )
        for index in range(12)
    ]
    report = build_research_council_alpha_uplift_ab_from_scorecard(
        scorecard_report=_scorecard(),
        council_ab_evidence=_evidence(rows),
    )

    assert report["sample_sufficient"] is False
    assert report["decision"] == "COLLECT/INSUFFICIENT"


def test_council_context_available_after_decision_fails_closed() -> None:
    evidence = _evidence(
        [_row(index, period_id="p1", pnl=1.0, action="ALLOW") for index in range(30)]
    )
    first = evidence["rows"][0]
    decision = datetime.fromisoformat(str(first["candidate_decision_time_utc"]))
    first["council_available_at_utc"] = (decision + timedelta(seconds=1)).isoformat()

    with pytest.raises(
        ResearchCouncilAlphaUpliftError,
        match="council_context_after_candidate_decision",
    ):
        build_research_council_alpha_uplift_ab_from_scorecard(
            scorecard_report=_scorecard(),
            council_ab_evidence=evidence,
        )


def test_scorecard_lineage_mismatch_fails_closed() -> None:
    evidence = _evidence([])
    evidence["source_scorecard_evidence_sha256"] = "d" * 64

    with pytest.raises(
        ResearchCouncilAlphaUpliftError,
        match="source_scorecard_evidence_sha_mismatch",
    ):
        build_research_council_alpha_uplift_ab_from_scorecard(
            scorecard_report=_scorecard(),
            council_ab_evidence=evidence,
        )
