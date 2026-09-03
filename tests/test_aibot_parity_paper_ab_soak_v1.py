from __future__ import annotations

import hashlib
from typing import Any

import pytest

from smartcrypto.research.aibot_parity_paper_ab_soak import (
    build_preregistration,
    evaluate_prospective_ab_soak,
)

BASE_SHA = "2daa54c47b033b5951ebf6f2ff7fa615beab3ee8"
START = "2026-09-03T20:00:00Z"


def _config(**overrides: Any):
    payload: dict[str, Any] = {
        "experiment_id": "aibot-parity-paper-ab-soak-test",
        "preregistered_start_utc": START,
        "software_dod_merge_sha": BASE_SHA,
        "minimum_observations_per_arm": 2,
        "minimum_observation_days": 1,
        "minimum_profit_factor": 1.1,
        "bootstrap_iterations": 200,
        "bootstrap_seed": 20260820,
        "confidence_level": 0.95,
    }
    payload.update(overrides)
    return build_preregistration(payload)


def _arm(candidate_id: str, experiment_id: str = "aibot-parity-paper-ab-soak-test") -> str:
    digest = hashlib.sha256(f"{experiment_id}|{candidate_id}".encode()).digest()
    return "CONTROL" if digest[0] < 128 else "TREATMENT"


def _candidate_id_for_arm(arm: str, ordinal: int) -> str:
    found = []
    index = 0
    while len(found) <= ordinal:
        candidate_id = f"candidate-{index:04d}"
        if _arm(candidate_id) == arm:
            found.append(candidate_id)
        index += 1
    return found[ordinal]


def _row(
    *,
    candidate_id: str,
    cycle_id: str,
    observed_at: str,
    action: str = "ACCEPT",
    pnl: float | None = None,
    outcome_at: str | None = None,
    qlib_status: str = "BLOCKED_EXTERNAL",
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "cycle_id": cycle_id,
        "observed_at_utc": observed_at,
        "treatment_action": action,
        "riskmanager_shadow_decision": "ALLOW" if action == "ACCEPT" else "BLOCK",
        "point_in_time_valid": True,
        "financial_config_unchanged": True,
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "signal_published": False,
        "writes_active_signals": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "qlib_status": qlib_status,
        "symbol": "BTC/USDT:USDT",
        "side": "long",
        "regime": "trend",
        "realized_net_pnl_usdt": pnl,
        "outcome_available_at_utc": outcome_at,
    }


def test_preregistration_freezes_shadow_only_definitions() -> None:
    config = _config()
    assert config.control_definition == "FREQTRADE_PAPER_BASELINE_OBSERVED_ONLY"
    assert config.treatment_definition == "AIBOT_PARITY_SHADOW_COUNTERFACTUAL_ONLY"
    with pytest.raises(ValueError, match="treatment_definition"):
        _config(treatment_definition="ACTIVE_PAPER_TREATMENT")


def test_pre_registration_rows_are_excluded_from_prospective_sample() -> None:
    config = _config()
    row = _row(
        candidate_id="historical-candidate",
        cycle_id="historical-cycle",
        observed_at="2026-09-03T19:59:59Z",
    )
    report, assignments = evaluate_prospective_ab_soak(config, [row])
    assert assignments == []
    assert report["soak_health"]["pre_registration_excluded_count"] == 1
    assert report["paper_treatment_release_allowed"] is False


def test_assignment_matches_existing_sha256_contract_and_is_analytical_only() -> None:
    config = _config()
    candidate_id = "candidate-stable"
    report, assignments = evaluate_prospective_ab_soak(
        config,
        [
            _row(
                candidate_id=candidate_id,
                cycle_id="cycle-stable",
                observed_at="2026-09-03T20:01:00Z",
            )
        ],
    )
    assert len(assignments) == 1
    digest = hashlib.sha256(
        f"{config.experiment_id}|{candidate_id}".encode()
    ).hexdigest()
    assert assignments[0]["assignment_id"] == f"ab-{digest}"
    assert assignments[0]["arm"] == _arm(candidate_id)
    assert report["design"]["traffic_split_performed"] is False
    assert report["operational_authority"] is False


def test_missing_fail_closed_safety_proof_blocks_integrity() -> None:
    config = _config()
    row = _row(
        candidate_id="unsafe-candidate",
        cycle_id="unsafe-cycle",
        observed_at="2026-09-03T20:01:00Z",
    )
    row.pop("sends_orders")
    report, assignments = evaluate_prospective_ab_soak(config, [row])
    assert assignments == []
    assert report["status"] == "blocked"
    assert report["soak_health"]["soak_status"] == "BLOCKED_INTEGRITY"
    assert "SAFETY_FLAG_VIOLATION" in report["reason"]


def test_outcome_must_be_strictly_after_shadow_decision() -> None:
    config = _config()
    row = _row(
        candidate_id="bad-lineage",
        cycle_id="bad-lineage-cycle",
        observed_at="2026-09-03T20:01:00Z",
        pnl=1.0,
        outcome_at="2026-09-03T20:01:00Z",
    )
    report, assignments = evaluate_prospective_ab_soak(config, [row])
    assert len(assignments) == 1
    assert report["status"] == "blocked"
    assert report["financial_evidence"]["status"] == "EVIDENCE_BLOCKED"
    assert "OUTCOME_NOT_STRICTLY_AFTER_DECISION" in report["reason"]


def test_treatment_reject_has_zero_counterfactual_effective_pnl() -> None:
    config = _config(minimum_observations_per_arm=1, minimum_observation_days=0)
    candidate_id = _candidate_id_for_arm("TREATMENT", 0)
    row = _row(
        candidate_id=candidate_id,
        cycle_id="treatment-reject-cycle",
        observed_at="2026-09-03T20:01:00Z",
        action="REJECT",
        pnl=5.0,
        outcome_at="2026-09-03T21:01:00Z",
    )
    _, assignments = evaluate_prospective_ab_soak(config, [row])
    assert assignments[0]["arm"] == "TREATMENT"
    assert assignments[0]["realized_net_pnl_usdt"] == 5.0
    assert assignments[0]["effective_arm_pnl_usdt"] == 0.0


def test_exact_duplicate_is_idempotently_ignored() -> None:
    config = _config()
    row = _row(
        candidate_id="duplicate-candidate",
        cycle_id="duplicate-cycle",
        observed_at="2026-09-03T20:01:00Z",
    )
    report, assignments = evaluate_prospective_ab_soak(config, [row, dict(row)])
    assert len(assignments) == 1
    assert report["soak_health"]["exact_duplicate_count"] == 1
    assert report["status"] == "ok"


def test_qlib_blocked_external_is_counted_but_not_bypassed() -> None:
    config = _config()
    row = _row(
        candidate_id="qlib-isolated-candidate",
        cycle_id="qlib-isolated-cycle",
        observed_at="2026-09-03T20:01:00Z",
    )
    report, _ = evaluate_prospective_ab_soak(config, [row])
    assert report["soak_health"]["qlib_blocked_external_count"] == 1
    assert report["qlib_security_gate_bypassed"] is False
    assert report["updates_qlib_runtime"] is False


def test_sample_gate_remains_insufficient_before_preregistered_thresholds() -> None:
    config = _config(minimum_observations_per_arm=2, minimum_observation_days=1)
    control = _candidate_id_for_arm("CONTROL", 0)
    treatment = _candidate_id_for_arm("TREATMENT", 0)
    rows = [
        _row(
            candidate_id=control,
            cycle_id="c1",
            observed_at="2026-09-03T20:01:00Z",
            pnl=-1.0,
            outcome_at="2026-09-03T21:00:00Z",
        ),
        _row(
            candidate_id=treatment,
            cycle_id="t1",
            observed_at="2026-09-03T20:02:00Z",
            pnl=1.0,
            outcome_at="2026-09-03T21:01:00Z",
        ),
    ]
    report, _ = evaluate_prospective_ab_soak(config, rows)
    assert report["financial_evidence"]["status"] == "INSUFFICIENT_SAMPLE"
    assert report["soak_health"]["soak_status"] == "COLLECTING"
    assert report["next_gate"]["evidence_sufficient_for_release_review"] is False


def test_positive_prospective_evidence_is_research_only_and_never_releases() -> None:
    config = _config(minimum_observations_per_arm=2, minimum_observation_days=1)
    controls = [_candidate_id_for_arm("CONTROL", index) for index in range(2)]
    treatments = [_candidate_id_for_arm("TREATMENT", index) for index in range(2)]
    rows = [
        _row(
            candidate_id=controls[0],
            cycle_id="c-day1",
            observed_at="2026-09-04T00:00:00Z",
            pnl=-1.0,
            outcome_at="2026-09-04T01:00:00Z",
        ),
        _row(
            candidate_id=treatments[0],
            cycle_id="t-day1",
            observed_at="2026-09-04T00:01:00Z",
            pnl=-0.1,
            outcome_at="2026-09-04T01:01:00Z",
        ),
        _row(
            candidate_id=controls[1],
            cycle_id="c-day2",
            observed_at="2026-09-06T00:00:00Z",
            pnl=1.0,
            outcome_at="2026-09-06T01:00:00Z",
        ),
        _row(
            candidate_id=treatments[1],
            cycle_id="t-day2",
            observed_at="2026-09-06T00:01:00Z",
            pnl=2.0,
            outcome_at="2026-09-06T01:01:00Z",
        ),
    ]
    report, _ = evaluate_prospective_ab_soak(config, rows)
    assert report["financial_evidence"]["status"] == "INCREMENTAL_EDGE_RESEARCH_ONLY"
    assert report["financial_evidence"]["edge_ci_gate"] is True
    assert report["financial_evidence"]["treatment_profit_factor_gate"] is True
    assert report["next_gate"]["evidence_sufficient_for_release_review"] is True
    assert report["paper_treatment_release_allowed"] is False
    assert report["paper_activation_performed"] is False
    assert report["sends_orders"] is False
