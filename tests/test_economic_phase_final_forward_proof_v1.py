from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_canonical_treatment_causal_governance_v1 import audit

from smartcrypto.execution.decision_ledger_v4_2 import seal_decision_record
from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL, digest
from smartcrypto.research.aibot_parity import economic_phase_final_forward_proof as b17
from smartcrypto.research.canonical_treatment import causal_governance as g
from smartcrypto.research.canonical_treatment.publisher import (
    LEDGER_SCHEMA_VERSION,
    _crosswalk_index,
)

START = datetime(2026, 10, 1, tzinfo=UTC)


def seal_report(report: dict, field: str) -> dict:
    report[field] = hashlib.sha256(
        json.dumps(
            report, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return report


def upstream(waiting: bool = False) -> dict:
    state = "waiting" if waiting else "ok"
    return {
        "branch15": seal_report(
            {
                "schema_version": b17.PORTFOLIO_SCHEMA,
                "status": state,
                "source_scorecard_evidence_sha256": "a" * 64,
                "sleeve_count": 1,
                **b17.SAFETY_FLAGS,
            },
            "selection_evidence_sha256",
        ),
        "branch16": seal_report(
            {
                "schema_version": b17.COUNCIL_SCHEMA,
                "status": state,
                "source_scorecard_evidence_sha256": "a" * 64,
                "period_reports": [{"period": "oos"}],
                **b17.SAFETY_FLAGS,
            },
            "council_ab_evidence_sha256",
        ),
    }


def evidence_case(
    tmp_path: Path, monkeypatch, *, count: int = 200, selected: int = 60, days: int = 46
) -> dict:
    monkeypatch.setattr(g, "now_utc", lambda: START)
    manifest = g.activate_once(tmp_path / "activation.json", audit(START))
    now = START + timedelta(days=days)
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/decision_ledger_v4_2/valid_decision.json").read_text()
    )
    records, signals, rows, a, b = [], [], [], [], []
    for n in range(count):
        at = START + timedelta(seconds=10 + n * 2)
        event = f"decision-event:case-{n}"
        body = {k: v for k, v in fixture.items() if k != "payload_sha256"}
        body.update(
            event_id=event,
            signal_id=f"signal:phase13-signal-producer:{n}",
            candidate_id=f"candidate:phase13-signal-producer:{n}",
            correlation_id=f"correlation:{n}",
            idempotency_key=f"decision:{n}",
            feature_timestamp=at.isoformat(),
            decision_timestamp=at.isoformat(),
        )
        op = seal_decision_record(body).model_dump(mode="json")
        records.append(op)
        body.update(
            event_id=f"v3-shadow-decision:{n}",
            model_hash=CANONICAL.model_artifact_sha256,
            ai_shadow_decision="ALLOW" if n < selected else "ABSTAIN",
            ai_shadow_reasons=["fixture"],
        )
        v3 = seal_decision_record(body).model_dump(mode="json")
        cw = {
            "schema_version": "qlib_v3_operational_decision_crosswalk_v1",
            "signal_id": op["signal_id"],
            "operational_decision_event_id": event,
            "operational_decision_payload_sha256": op["payload_sha256"],
            "v3_decision_event_id": v3["event_id"],
            "v3_decision_payload_sha256": v3["payload_sha256"],
        }
        cw["crosswalk_sha256"] = digest(cw)
        signal = {
            "epoch_version": "v3",
            "origin": "natural_paper_runtime",
            "replayed": False,
            "backfilled": False,
            "synthetic": False,
            "identity": CANONICAL.mapping(),
            "decision": v3,
            "signal_id": op["signal_id"],
            "decision_event_id": v3["event_id"],
            "signal_timestamp_utc": at.isoformat(),
            "operational_crosswalk": cw,
        }
        signals.append(signal)
        normalized = _crosswalk_index({"signals": [signal]})[event]
        rows.append(
            dict(
                normalized,
                selected=n < selected,
                status="scored",
                first_observed_at_utc=at.isoformat(),
                last_observed_at_utc=at.isoformat(),
                valid_until=(at + timedelta(minutes=30)).isoformat(),
            )
        )
        trade = {
            "id": n + 1,
            "is_open": 0,
            "pair": "BTC/USDT:USDT",
            "is_short": 0,
            "open_date": (at + timedelta(seconds=1)).isoformat(),
            "close_date": (at + timedelta(minutes=1)).isoformat(),
            "enter_tag": f"decision_event_id={event}",
            "close_profit_abs": 1.0 if n < selected else -1.0,
            "stake_amount": 50.0,
            "leverage": 2.0,
        }
        a.append(trade)
        if n < selected:
            b.append(dict(trade, close_profit_abs=-1.0 if n == 0 else 3.0))
    store = {
        "schema_version": "qlib_v3_prospective_evidence_store_v1",
        "identity": CANONICAL.mapping(),
        "signals": signals,
        "outcomes": [],
    }
    store["store_sha256"] = digest(store)
    return {
        "causal_manifest": manifest,
        "parity_report": audit(now),
        "operational_records": records,
        "v3_evidence": store,
        "treatment_ledger": {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "experiment_id": "canonical-treatment-paper-v1",
            "rows": rows,
        },
        "control_trades": a,
        "treatment_trades": b,
        "upstream_reports": upstream(),
        "as_of_utc": now.isoformat(),
    }


def test_full_exact_cohort_uses_actual_two_arm_pnl_and_preserves_safety(
    tmp_path, monkeypatch
) -> None:
    case = evidence_case(tmp_path, monkeypatch)
    report = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert report["status"] == "ok", report["blockers"]
    assert report["certification_attempted"] is True
    assert report["decision"] == "FORWARD_PROOF_RESEARCH_ONLY"
    assert report["treatment_metrics"]["net_pnl"] == pytest.approx(-1 + 59 * 3 - 60 * 0.05)
    assert all(report[k] is v for k, v in b17.SAFETY_FLAGS.items())
    assert all(gate["passed"] for gate in report["sample_gates"].values())


@pytest.mark.parametrize("count,selected,days", [(199, 60, 46), (200, 49, 46), (200, 60, 44)])
def test_insufficient_real_sample_never_attempts_certification(
    tmp_path, monkeypatch, count, selected, days
) -> None:
    case = evidence_case(tmp_path, monkeypatch, count=count, selected=selected, days=days)
    report = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert report["status"] == "waiting", report["blockers"]
    assert report["decision"] == "WAITING_FORWARD_EVIDENCE"
    assert report["certification_attempted"] is False


def test_b15_b16_waiting_prevent_certification_despite_sufficient_sample(
    tmp_path, monkeypatch
) -> None:
    case = evidence_case(tmp_path, monkeypatch)
    case["upstream_reports"] = upstream(waiting=True)
    report = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert report["status"] == "waiting"
    assert report["certification_attempted"] is False
    assert report["blockers"] == ["branch15", "branch16"]


def test_coverage_denominator_includes_missing_treatment_events(tmp_path, monkeypatch) -> None:
    case = evidence_case(tmp_path, monkeypatch, count=210)
    case["treatment_ledger"]["rows"] = case["treatment_ledger"]["rows"][:-7]
    report = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert report["causal_population"]["eligible_decisions"] == 210
    assert report["causal_population"]["coverage_misses"] == 7
    assert report["sample_gates"]["scorer_coverage"]["value"] == pytest.approx(203 / 210)
    assert report["certification_attempted"] is False


def test_even_subthreshold_gap_prevents_false_economic_pass(tmp_path, monkeypatch) -> None:
    case = evidence_case(tmp_path, monkeypatch, count=201)
    case["treatment_ledger"]["rows"].pop()
    report = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert report["sample_gates"]["scorer_coverage"]["passed"] is True
    assert report["status"] == "blocked"
    assert report["economic_gates"]["economic_dod_no_unexplained_gap"]["passed"] is False


def test_pre_activation_smoke_and_seven_historical_gaps_excluded(tmp_path, monkeypatch) -> None:
    case = evidence_case(tmp_path, monkeypatch, count=7, selected=0)
    later = START + timedelta(hours=1)
    monkeypatch.setattr(g, "now_utc", lambda: later)
    case["causal_manifest"] = g.activate_once(tmp_path / "later-registration.json", audit(later))
    case["treatment_ledger"]["rows"] = []
    report = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert report["status"] == "waiting", report["blockers"]
    assert report["causal_population"]["eligible_decisions"] == 0
    assert report["causal_population"]["coverage_misses"] == 0
    assert case["v3_evidence"]["signals"]  # no source rewriting / backfill


@pytest.mark.parametrize(
    "mutation",
    [
        "seal",
        "crosswalk",
        "parent",
        "tag",
        "pnl",
        "future_trade",
        "duplicate_trade",
        "selection",
        "v3_origin",
    ],
)
def test_invalid_lineage_or_financial_data_fails_closed(tmp_path, monkeypatch, mutation) -> None:
    case = evidence_case(tmp_path, monkeypatch, count=2, selected=2, days=1)
    if mutation == "seal":
        case["operational_records"][0]["model_hash"] = "c" * 64
    elif mutation == "crosswalk":
        case["treatment_ledger"]["rows"][0]["crosswalk_sha256"] = "c" * 64
    elif mutation == "parent":
        case["treatment_ledger"]["rows"][0]["operational_decision_payload_sha256"] = "c" * 64
    elif mutation == "tag":
        case["treatment_trades"][0]["enter_tag"] = "decision_event_id=approximate"
    elif mutation == "pnl":
        case["treatment_trades"][0]["close_profit_abs"] = float("nan")
    elif mutation == "future_trade":
        case["treatment_trades"][0]["close_date"] = (START + timedelta(days=2)).isoformat()
    elif mutation == "duplicate_trade":
        case["treatment_trades"].append(case["treatment_trades"][0])
    elif mutation == "selection":
        case["treatment_ledger"]["rows"][0]["selected"] = False
    else:
        store = case["v3_evidence"]
        store["signals"][0]["origin"] = "replay"
        store["store_sha256"] = digest({k: v for k, v in store.items() if k != "store_sha256"})
    report = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert report["status"] == "blocked", report
    assert report["certification_attempted"] is False


def test_negative_actual_treatment_is_not_replaced_with_control_pnl(tmp_path, monkeypatch) -> None:
    case = evidence_case(tmp_path, monkeypatch)
    for row in case["treatment_trades"]:
        row["close_profit_abs"] = -10.0
    report = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert report["status"] == "blocked"
    assert report["treatment_metrics"]["net_pnl"] < 0


def test_rerun_and_identical_operational_event_do_not_duplicate(tmp_path, monkeypatch) -> None:
    case = evidence_case(tmp_path, monkeypatch, count=2, selected=2)
    before = deepcopy(case)
    first = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert first == b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert case == before
    case["operational_records"].append(case["operational_records"][0])
    again = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert again["causal_population"]["eligible_decisions"] == 2


def test_missing_manifest_waits_but_bad_parity_blocks(tmp_path, monkeypatch) -> None:
    case = evidence_case(tmp_path, monkeypatch, count=0, selected=0)
    case["causal_manifest"] = None
    assert b17.build_economic_phase_final_forward_proof_from_reports(**case)["status"] == "waiting"
    case["parity_report"]["parity_status"] = "BLOCKED"
    result = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert result["status"] == "blocked"


@pytest.mark.parametrize(
    "stage,seal",
    [("branch15", "selection_evidence_sha256"), ("branch16", "council_ab_evidence_sha256")],
)
def test_missing_upstream_safety_does_not_become_ready(tmp_path, monkeypatch, stage, seal) -> None:
    case = evidence_case(tmp_path, monkeypatch, count=0, selected=0)
    report = case["upstream_reports"][stage]
    del report["paper_only"]
    del report[seal]
    seal_report(report, seal)
    result = b17.build_economic_phase_final_forward_proof_from_reports(**case)
    assert result["status"] == "blocked"
    assert stage + "_required_safety_missing_or_invalid" in result["blockers"]
