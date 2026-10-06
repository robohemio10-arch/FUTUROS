from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts.build_paper_b_soak_checkpoint_certification_v1 import main
from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_outcome_resolver import (
    MIN_OBSERVATION_DAYS as V2_MIN_OBSERVATION_DAYS,
    MIN_RESOLVED_DECISIONS as V2_MIN_RESOLVED_DECISIONS,
    MIN_SCORER_COVERAGE as V2_MIN_SCORER_COVERAGE,
    MIN_SELECTED_TRADES as V2_MIN_SELECTED_TRADES,
)
from smartcrypto.learning.qlib_v3_prospective.contracts import (
    EvidenceError,
    digest,
)
from smartcrypto.research.canonical_treatment.soak_checkpoint_certification import (
    MIN_OBSERVATION_DAYS,
    MIN_RESOLVED_DECISIONS,
    MIN_SCORER_COVERAGE,
    MIN_SELECTED_CLOSED,
    build_checkpoint_report,
    run_certification,
)

BASELINE_SHA = "b" * 64
COVERAGE_SHA = "c" * 64
FORMAL_ACTIVATION = "2026-09-20T00:00:00Z"
FIX_DEPLOYED = "2026-09-24T22:44:17Z"


def _iso_after_fix(days: float) -> str:
    start = datetime(2026, 9, 24, 22, 44, 17, tzinfo=UTC)
    return (start + timedelta(days=days)).isoformat().replace("+00:00", "Z")


def _summary(
    *,
    eligible_count: int,
    resolved_count: int,
    selected_closed_count: int,
) -> dict[str, Any]:
    return {
        "eligible_count": eligible_count,
        "resolved_count": resolved_count,
        "allow_count": selected_closed_count,
        "abstain_count": max(0, eligible_count - selected_closed_count),
        "selected_closed_count": selected_closed_count,
        "good_abstention_count": 0,
        "false_abstention_count": 0,
        "good_selection_count": 0,
        "bad_selection_count": 0,
        "avoided_loss_usdt": 0.0,
        "missed_profit_usdt": 0.0,
        "selected_profit_usdt": 0.0,
        "selected_loss_usdt": 0.0,
        "net_selector_value_usdt": 0.0,
        "paired_resolved_count": 0,
        "control_net_pnl": None,
        "treatment_net_pnl": None,
        "delta_net_pnl": None,
        "control_expectancy": None,
        "treatment_expectancy": None,
        "delta_expectancy": None,
        "control_profit_factor": None,
        "treatment_profit_factor": None,
    }


def _attribution(
    *,
    observation_days: float = 20.0,
    postfix_eligible: int = 40,
    postfix_scored: int = 40,
    postfix_selected: int = 8,
    postfix_executed: int = 8,
    postfix_closed: int = 8,
    postfix_resolved: int = 30,
    postfix_selected_closed: int = 8,
    prefix_resolved: int = 1000,
    prefix_selected_closed: int = 500,
) -> dict[str, Any]:
    coverage = (
        postfix_scored / postfix_eligible
        if postfix_eligible
        else None
    )
    post = _summary(
        eligible_count=postfix_eligible,
        resolved_count=postfix_resolved,
        selected_closed_count=postfix_selected_closed,
    )
    pre = _summary(
        eligible_count=109,
        resolved_count=prefix_resolved,
        selected_closed_count=prefix_selected_closed,
    )
    total = _summary(
        eligible_count=postfix_eligible + 109,
        resolved_count=postfix_resolved + prefix_resolved,
        selected_closed_count=(
            postfix_selected_closed + prefix_selected_closed
        ),
    )

    report: dict[str, Any] = {
        "schema_version": "paper_b_selector_economic_attribution_v1",
        "generated_at_utc": _iso_after_fix(observation_days),
        "status": "ok",
        "reason": "closed_exact_causal_outcomes_only",
        "baseline_sha256": BASELINE_SHA,
        "coverage_report_sha256": COVERAGE_SHA,
        "coverage_funnel": {
            "eligible_decision_count": postfix_eligible,
            "scored_decision_count": postfix_scored,
            "selected_decision_count": postfix_selected,
            "allow_decision_count": postfix_selected,
            "executed_decision_count": postfix_executed,
            "closed_decision_count": postfix_closed,
            "coverage": coverage,
        },
        "summary": total,
        "by": {
            "epoch": {
                "PRE_FIX": pre,
                "POST_FIX": post,
            }
        },
        "checkpoints": {
            f"checkpoint_{value}_selected_closed": (
                "PASS"
                if total["selected_closed_count"] >= value
                else "PENDING_SAMPLE"
            )
            for value in (10, 25, 50)
        },
        "safety": {
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "changes_model": False,
            "changes_threshold": False,
            "changes_strategy": False,
            "changes_risk": False,
            "sends_orders": False,
            "replay": False,
            "backfill": False,
        },
        "write_performed": False,
        "write_requested": False,
    }
    report["report_sha256"] = digest(report)
    return report


def _build(attribution: dict[str, Any]) -> dict[str, Any]:
    return build_checkpoint_report(
        attribution=attribution,
        formal_activation_utc=FORMAL_ACTIVATION,
        fix_deployed_at_utc=FIX_DEPLOYED,
        baseline_sha256=BASELINE_SHA,
    )


def test_br04_thresholds_match_existing_canonical_sample_gate() -> None:
    assert MIN_OBSERVATION_DAYS == V2_MIN_OBSERVATION_DAYS == 45.0
    assert MIN_RESOLVED_DECISIONS == V2_MIN_RESOLVED_DECISIONS == 200
    assert MIN_SELECTED_CLOSED == V2_MIN_SELECTED_TRADES == 50
    assert MIN_SCORER_COVERAGE == V2_MIN_SCORER_COVERAGE == 0.99


def test_prefixed_history_never_satisfies_postfix_gate() -> None:
    report = _build(_attribution())
    assert report["decision"] == "CONTINUE_NATURAL_COLLECTION"
    assert report["observation"]["postfix_resolved_count"] == 30
    assert report["observation"]["postfix_selected_closed_count"] == 8
    assert report["sample_readiness_passed"] is False
    assert report["economic_gate"]["economic_edge_certified"] is False
    assert report["promotion_allowed"] is False
    assert report["operational_authority"] is False


def test_observation_clock_starts_at_fix_not_formal_activation() -> None:
    report = _build(
        _attribution(
            observation_days=44.0,
            postfix_eligible=250,
            postfix_scored=250,
            postfix_selected=60,
            postfix_executed=60,
            postfix_closed=55,
            postfix_resolved=220,
            postfix_selected_closed=50,
        )
    )
    assert report["observation"]["postfix_observation_days"] == 44.0
    assert (
        report["sample_readiness_gates"]["postfix_observation_days_min_45"]
        is False
    )
    assert report["ready_for_separate_economic_decision"] is False


def test_intermediate_postfix_milestones_are_exact() -> None:
    report = _build(
        _attribution(
            observation_days=30.0,
            postfix_eligible=50,
            postfix_scored=50,
            postfix_selected=25,
            postfix_executed=25,
            postfix_closed=25,
            postfix_resolved=50,
            postfix_selected_closed=25,
        )
    )
    milestones = report["milestones"]
    assert milestones["checkpoint_postfix_eligible_50"] == "PASS"
    assert milestones["checkpoint_postfix_eligible_100"] == "PENDING_SAMPLE"
    assert milestones["checkpoint_postfix_resolved_50"] == "PASS"
    assert milestones["checkpoint_postfix_resolved_200"] == "PENDING_SAMPLE"
    assert milestones["checkpoint_postfix_selected_closed_25"] == "PASS"
    assert milestones["checkpoint_postfix_selected_closed_50"] == "PENDING_SAMPLE"


def test_complete_postfix_sample_is_ready_only_for_separate_economic_gate() -> None:
    report = _build(
        _attribution(
            observation_days=46.0,
            postfix_eligible=220,
            postfix_scored=220,
            postfix_selected=60,
            postfix_executed=58,
            postfix_closed=55,
            postfix_resolved=205,
            postfix_selected_closed=50,
        )
    )
    assert report["sample_readiness_passed"] is True
    assert report["ready_for_separate_economic_decision"] is True
    assert report["decision"] == "READY_FOR_SEPARATE_ECONOMIC_DECISION"
    assert report["economic_gate"]["evaluated"] is False
    assert report["economic_gate"]["economic_edge_certified"] is False
    assert report["promotion_allowed"] is False


def test_coverage_below_099_blocks_sample_readiness() -> None:
    report = _build(
        _attribution(
            observation_days=46.0,
            postfix_eligible=201,
            postfix_scored=198,
            postfix_selected=60,
            postfix_executed=55,
            postfix_closed=52,
            postfix_resolved=200,
            postfix_selected_closed=50,
        )
    )
    assert (
        report["sample_readiness_gates"]["postfix_scorer_coverage_min_0_99"]
        is False
    )
    assert report["ready_for_separate_economic_decision"] is False


def test_attribution_hash_mutation_fails_closed() -> None:
    attribution = _attribution()
    attribution["by"]["epoch"]["POST_FIX"]["resolved_count"] += 1
    with pytest.raises(EvidenceError, match="checkpoint_attribution_hash_invalid"):
        _build(attribution)


def test_baseline_identity_mismatch_fails_closed() -> None:
    with pytest.raises(EvidenceError, match="checkpoint_baseline_identity_mismatch"):
        build_checkpoint_report(
            attribution=_attribution(),
            formal_activation_utc=FORMAL_ACTIVATION,
            fix_deployed_at_utc=FIX_DEPLOYED,
            baseline_sha256="a" * 64,
        )


def test_unsafe_upstream_attribution_fails_closed() -> None:
    attribution = _attribution()
    attribution["safety"]["changes_risk"] = True
    attribution.pop("report_sha256")
    attribution["report_sha256"] = digest(attribution)
    with pytest.raises(
        EvidenceError,
        match="checkpoint_attribution_safety_invalid:changes_risk",
    ):
        _build(attribution)


def test_invalid_causal_funnel_fails_closed() -> None:
    with pytest.raises(EvidenceError, match="checkpoint_causal_funnel_invalid"):
        _build(
            _attribution(
                postfix_eligible=20,
                postfix_scored=20,
                postfix_selected=10,
                postfix_executed=11,
                postfix_closed=10,
            )
        )


def test_generated_before_fix_fails_closed() -> None:
    attribution = _attribution(observation_days=1.0)
    attribution["generated_at_utc"] = "2026-09-24T22:00:00Z"
    attribution.pop("report_sha256")
    attribution["report_sha256"] = digest(attribution)
    with pytest.raises(EvidenceError, match="checkpoint_generated_before_fix"):
        _build(attribution)


def test_cli_default_is_no_write(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_run_certification(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "ok", "write_performed": False}

    monkeypatch.setattr(
        "scripts.build_paper_b_soak_checkpoint_certification_v1.run_certification",
        fake_run_certification,
    )
    assert main(["--runtime-root", "C:/runtime", "--json"]) == 0
    assert captured["write_report"] is False
    assert '"write_performed":false' in capsys.readouterr().out


def test_missing_runtime_sources_block_without_default_write(tmp_path: Path) -> None:
    report = run_certification(
        project_root=tmp_path,
        runtime_root=tmp_path,
    )
    assert report["status"] == "blocked"
    assert report["ready_for_separate_economic_decision"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports").exists()


def _epoch2_attribution() -> tuple[dict[str, Any], dict[str, str]]:
    attribution = _attribution()
    post = attribution["by"]["epoch"]["POST_FIX"]
    attribution["by"]["epoch"] = {"EPOCH_2": post}
    attribution["summary"] = post
    identity = {
        "epoch_id": "paper-b-epoch-2", "formal_activation_utc": FIX_DEPLOYED,
        "registration_sha256": "a" * 64, "epoch_baseline_sha256": BASELINE_SHA,
    }
    attribution["epoch"] = identity
    attribution.pop("report_sha256")
    attribution["report_sha256"] = digest(attribution)
    return attribution, identity


def test_epoch2_checkpoint_uses_only_its_epoch_and_activation_clock() -> None:
    attribution, identity = _epoch2_attribution()
    report = build_checkpoint_report(
        attribution=attribution, formal_activation_utc=FIX_DEPLOYED,
        fix_deployed_at_utc=FIX_DEPLOYED, baseline_sha256=BASELINE_SHA,
        epoch_identity=identity,
    )
    assert report["epoch"] == identity
    assert report["observation"]["postfix_resolved_count"] == 30
    assert report["observation"]["postfix_observation_days"] == 20.0
    assert report["observation"]["epoch_activation_utc"] == FIX_DEPLOYED
    assert "fix_deployed_at_utc" not in report["observation"]


def test_epoch2_checkpoint_rejects_predecessor_group_and_identity_drift() -> None:
    attribution, identity = _epoch2_attribution()
    attribution["by"]["epoch"]["PRE_FIX"] = _summary(
        eligible_count=109, resolved_count=109, selected_closed_count=50
    )
    attribution.pop("report_sha256")
    attribution["report_sha256"] = digest(attribution)
    with pytest.raises(EvidenceError, match="checkpoint_cross_epoch_group_invalid"):
        build_checkpoint_report(
            attribution=attribution, formal_activation_utc=FIX_DEPLOYED,
            fix_deployed_at_utc=FIX_DEPLOYED, baseline_sha256=BASELINE_SHA,
            epoch_identity=identity,
        )
    attribution, identity = _epoch2_attribution()
    identity = {**identity, "registration_sha256": "f" * 64}
    with pytest.raises(EvidenceError, match="checkpoint_epoch2_identity_or_boundary_invalid"):
        build_checkpoint_report(
            attribution=attribution, formal_activation_utc=FIX_DEPLOYED,
            fix_deployed_at_utc=FIX_DEPLOYED, baseline_sha256=BASELINE_SHA,
            epoch_identity=identity,
        )


def test_epoch2_checkpoint_cli_forwards_registration_without_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_certification(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "ok", "write_performed": False}

    monkeypatch.setattr(
        "scripts.build_paper_b_soak_checkpoint_certification_v1.run_certification",
        fake_run_certification,
    )
    assert main(["--runtime-root", "C:/runtime", "--epoch-registration", "C:/epoch.json"]) == 0
    assert captured["epoch_registration"] == Path("C:/epoch.json")
    assert captured["write_report"] is False
