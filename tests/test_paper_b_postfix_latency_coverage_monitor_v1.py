"""Synthetic, no-runtime tests for the Paper-B post-fix monitor."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from smartcrypto.learning.qlib_v3_prospective.contracts import EvidenceError
from scripts.build_paper_b_postfix_latency_coverage_monitor_v1 import main
from smartcrypto.research.canonical_treatment.postfix_latency_coverage_monitor import (
    BRANCH01_BASELINE_COUNTS,
    BRANCH01_BASELINE_SHA256,
    EPOCH2_CANONICAL_ACTIVATION_UTC,
    EPOCH2_CANONICAL_REGISTRATION_SHA256,
    build_epoch2_foundation,
    build_monitor_report,
    load_epoch2_registration,
    report_output_path,
    validate_historical_baseline,
)
from smartcrypto.research.canonical_treatment.postfix_causal_soak_foundation import Snapshot
from smartcrypto.research.canonical_treatment.causal_epoch2_foundation import (
    CausalEpoch2FoundationError,
)


FIX = "2026-09-24T22:44:17Z"


def fixture_inputs() -> dict:
    return {
        "foundation": {
            "fix_deployed_at_utc": FIX,
            "historical_debt": {
                "eligible": 109, "scored_at_fix": 98, "misses_at_fix": 11,
                "missing_event_ids": ["old-miss"],
                "late_resolved_after_fix_event_ids": ["old-miss"],
                "root_cause_summary": {"scheduler_drift": 7, "dns": 2, "unresolved": 2},
            },
            "post_fix_counters": {
                "eligible": 3, "scored": 2, "misses": 1,
                "eligible_event_ids": ["event-1", "event-2", "event-3"],
                "scored_event_ids": ["event-1", "event-2"],
                "missing_event_ids": ["event-3"], "coverage": 2 / 3,
            },
            "b17_cumulative_counters": {"eligible": 112, "scored": 101, "misses": 11},
            "blockers": [],
        },
        "scored_rows": {
            "event-1": {"selected": True, "signal_timestamp_utc": "2026-09-25T00:00:01Z",
                        "valid_until": "2026-09-25T00:10:00Z",
                        "first_observed_at_utc": "2026-09-25T00:00:02Z"},
            "event-2": {"selected": False, "signal_timestamp_utc": "2026-09-25T00:01:01Z",
                        "valid_until": "2026-09-25T00:10:00Z",
                        "first_observed_at_utc": "2026-09-25T00:01:03Z"},
        },
        "treatment_rows": [
            {"operational_decision_event_id": "event-1", "status": "scored"},
            {"operational_decision_event_id": "event-2", "status": "scored"},
            {"operational_decision_event_id": "event-3", "status": "unmatched"},
        ],
        "treatment_trades": [
            {"id": 10, "open_date": "2026-09-25T00:00:04Z", "close_date": "2026-09-25T00:02:00Z",
             "is_open": 0, "pair": "BTC/USDT:USDT", "is_short": 0,
             "enter_tag": "decision_event_id=event-1"},
        ],
        "operational_rows": {
            "event-1": {"decision_timestamp": "2026-09-25T00:00:00Z",
                        "pair": "BTC/USDT:USDT", "side": "long"},
            "event-2": {"decision_timestamp": "2026-09-25T00:01:00Z",
                        "pair": "ETH/USDT:USDT", "side": "short"},
            "event-3": {"decision_timestamp": "2026-09-25T00:02:00Z",
                        "pair": "BTC/USDT:USDT", "side": "long"},
        },
        "baseline": {"causal_runtime_registered_started_at_utc": {"control": FIX}},
        "runtime_fingerprints": {"control": {"started_at": "2026-09-28T00:00:00Z"}},
        "source_hashes": {"baseline_sha256": "a" * 64},
        "generated_at_utc": "2026-09-28T00:00:00Z",
    }


def test_historical_debt_and_late_resolution_remain_immutable() -> None:
    report = build_monitor_report(**fixture_inputs())
    assert report["baseline_historical_debt"]["misses_at_fix"] == 11
    assert report["baseline_historical_debt"]["late_resolved_after_fix_event_ids"] == ["old-miss"]
    assert report["postfix_population"]["misses"] == 1


def test_exact_funnel_and_available_latency() -> None:
    report = build_monitor_report(**fixture_inputs())
    funnel = report["causal_funnel"]
    assert (funnel["eligible_decision_count"], funnel["scored_decision_count"],
            funnel["selected_decision_count"], funnel["executed_decision_count"],
            funnel["closed_decision_count"]) == (3, 2, 1, 1, 1)
    assert report["latency"]["publisher_observation_latency"]["median_seconds"] == 1.5
    assert report["latency"]["eligible_to_observed_latency"]["max_seconds"] == 3
    assert report["latency"]["scheduler_delay"]["status"] == "unavailable"


def test_missing_classification_and_soak_insufficient() -> None:
    report = build_monitor_report(**fixture_inputs())
    assert report["postfix_population"]["missing_details"] == [
        {"decision_event_id": "event-3", "classification": "exact_publisher_row_unmatched_cause_unproven"}
    ]
    assert report["soak_a"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert report["soak_b"]["status"] == "INSUFFICIENT_EVIDENCE"


def test_orphan_trade_blocks_funnel() -> None:
    data = fixture_inputs()
    data["treatment_trades"].append({
        "id": 11, "open_date": "2026-09-25T00:03:00Z", "close_date": None,
        "is_open": 1, "enter_tag": "decision_event_id=other",
    })
    report = build_monitor_report(**data)
    assert report["status"] == "blocked"
    assert report["causal_funnel"]["orphan_trade_ids"] == [11]


def test_duplicate_trade_and_population_partition_fail_closed() -> None:
    data = fixture_inputs()
    data["treatment_trades"].append(deepcopy(data["treatment_trades"][0]))
    with pytest.raises(EvidenceError, match="duplicate"):
        build_monitor_report(**data)
    data = fixture_inputs()
    data["foundation"]["post_fix_counters"]["missing_event_ids"] = ["event-1"]
    with pytest.raises(EvidenceError, match="partition"):
        build_monitor_report(**data)


def test_restart_does_not_prove_continuity_and_safety() -> None:
    report = build_monitor_report(**fixture_inputs())
    assert report["restart_gap_evidence"]["observed_restarts"][0]["gap_start_utc"] is None
    assert report["restart_gap_evidence"]["observed_restarts"][0]["between_known_starts"]["eligible_count"] == 3
    assert report["restart_gap_evidence"]["continuity_proven"] is False
    assert report["safety"]["operational_authority"] is False
    assert report["safety"]["sends_orders"] is False
    assert report["write_performed"] is False


def test_wrong_pair_or_pre_observation_trade_is_not_attributed() -> None:
    data = fixture_inputs()
    data["treatment_trades"][0]["pair"] = "ETH/USDT:USDT"
    report = build_monitor_report(**data)
    assert report["status"] == "blocked"
    assert report["causal_funnel"]["executed_decision_count"] == 0
    assert report["causal_funnel"]["inconsistent_trade_ids"] == [10]
    data = fixture_inputs()
    data["treatment_trades"][0]["open_date"] = "2026-09-25T00:00:01Z"
    report = build_monitor_report(**data)
    assert report["causal_funnel"]["inconsistent_trade_ids"] == [10]


def test_soak_gates_pass_only_with_actual_postfix_coverage() -> None:
    data = fixture_inputs()
    post = data["foundation"]["post_fix_counters"]
    post["eligible_event_ids"] = ["event-1", "event-2"] + [f"e-{i}" for i in range(98)]
    post["scored_event_ids"] = post["eligible_event_ids"]
    post["missing_event_ids"] = []
    post.update(eligible=100, scored=100, misses=0, coverage=1.0)
    data["scored_rows"].update({
        event: {"selected": False, "signal_timestamp_utc": "2026-09-25T00:01:01Z",
                "first_observed_at_utc": "2026-09-25T00:01:03Z",
                "valid_until": "2026-09-25T00:10:00Z"}
        for event in post["eligible_event_ids"][2:]
    })
    data["treatment_rows"].extend({"operational_decision_event_id": event, "status": "scored"}
                                  for event in post["eligible_event_ids"][2:])
    data["operational_rows"].update({
        event: {"decision_timestamp": "2026-09-25T00:01:00Z",
                "pair": "BTC/USDT:USDT", "side": "long"}
        for event in post["eligible_event_ids"][2:]
    })
    report = build_monitor_report(**data)
    assert report["soak_a"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert "scheduler_miss_evidence_unavailable" in report["soak_a"]["blockers"]
    assert report["soak_b"]["status"] == "PASS"
    assert report["latency_comparison"]["status"] == "available"


def test_cli_default_is_no_write(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    captured = {}

    def fake_run_monitor(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "blocked", "write_performed": False}

    monkeypatch.setattr(
        "scripts.build_paper_b_postfix_latency_coverage_monitor_v1.run_monitor", fake_run_monitor
    )
    assert main(["--runtime-root", "C:/runtime", "--json"]) == 2
    assert captured["write_report"] is False
    assert '"write_performed":false' in capsys.readouterr().out


def test_branch01_baseline_identity_and_counts_are_anchored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "smartcrypto.research.canonical_treatment.postfix_latency_coverage_monitor.validate_baseline",
        lambda payload: None,
    )
    population = {"eligible": 109, "scored": 98, "misses": 11}
    baseline = {"baseline_sha256": BRANCH01_BASELINE_SHA256, "baseline_population": population}
    assert BRANCH01_BASELINE_COUNTS == (109, 98, 11)
    validate_historical_baseline(baseline)
    with pytest.raises(EvidenceError, match="branch01_baseline_identity_or_counts_changed"):
        validate_historical_baseline({**baseline, "baseline_sha256": "0" * 64})
    with pytest.raises(EvidenceError, match="branch01_baseline_identity_or_counts_changed"):
        validate_historical_baseline({**baseline, "baseline_population": {**population, "eligible": 98}})


def test_epoch2_foundation_uses_inclusive_activation_without_historical_carryover() -> None:
    activation = "2026-10-06T19:08:33.155007Z"
    registration = {
        "epoch_id": "paper-b-epoch-2", "registration_sha256": "a" * 64,
        "epoch_manifest": {"formal_activation_utc": activation, "epoch_manifest_sha256": "b" * 64},
        "epoch_baseline": {"epoch_baseline_sha256": "c" * 64},
    }
    boundary = datetime.fromisoformat(activation.replace("Z", "+00:00"))
    snapshot = Snapshot(
        generated_at_utc=activation, formal_activation_utc=activation,
        causal_manifest_sha256="b" * 64, parity_audit_sha256="d" * 64,
        eligible_event_times={"at-boundary": boundary, "later": boundary.replace(second=34)},
        scored_rows={"at-boundary": {}}, current_missing_event_ids=("later",),
        decision_ledger_row_count=10, v3_signal_count=5, v3_outcome_count=0,
    )
    foundation = build_epoch2_foundation(registration, snapshot)
    assert foundation["post_fix_counters"]["eligible_event_ids"] == ["at-boundary", "later"]
    assert foundation["historical_debt"]["eligible"] == 0
    assert "b17_cumulative_counters" not in foundation
    bad = Snapshot(**{**snapshot.__dict__, "eligible_event_times": {
        "old": datetime(2026, 10, 6, 19, 8, 32, tzinfo=UTC)}})
    with pytest.raises(EvidenceError, match="epoch2_pre_activation_population"):
        build_epoch2_foundation(registration, bad)


def test_epoch2_monitor_ignores_exact_old_trades_but_blocks_unknown_orphans() -> None:
    data = fixture_inputs()
    data["foundation"]["epoch"] = {
        "epoch_id": "paper-b-epoch-2", "formal_activation_utc": FIX,
        "registration_sha256": "a" * 64,
        "epoch_baseline_sha256": "b" * 64,
        "epoch_manifest_sha256": "c" * 64,
    }
    data["foundation"]["historical_debt"] = {
        "eligible": 0, "scored_at_fix": 0, "misses_at_fix": 0,
        "missing_event_ids": [], "late_resolved_after_fix_event_ids": [],
    }
    data["foundation"].pop("b17_cumulative_counters")
    data["source_hashes"] = {
        "epoch_registration_sha256": "a" * 64,
        "baseline_sha256": "b" * 64,
        "causal_manifest_sha256": "c" * 64,
    }
    data["pre_activation_event_ids"] = {"old-event"}
    data["treatment_trades"].append({
        "id": 11, "open_date": "2026-09-25T00:03:00Z", "close_date": None,
        "is_open": 1, "enter_tag": "decision_event_id=old-event",
    })
    report = build_monitor_report(**data)
    assert report["status"] == "ok"
    assert report["epoch"] == data["foundation"]["epoch"]
    assert "b17_cumulative" not in report
    assert report["causal_funnel"]["orphan_trade_ids"] == []
    data["treatment_trades"][-1]["enter_tag"] = "decision_event_id=unknown"
    assert build_monitor_report(**data)["causal_funnel"]["orphan_trade_ids"] == [11]
    data["foundation"]["historical_debt"]["eligible"] = 1
    with pytest.raises(EvidenceError, match="epoch2_cross_epoch_identity_or_carryover"):
        build_monitor_report(**data)


def test_epoch2_registration_validation_and_drift_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    registration = {
        "epoch_id": "paper-b-epoch-2",
        "registration_sha256": EPOCH2_CANONICAL_REGISTRATION_SHA256,
        "epoch_manifest": {"formal_activation_utc": EPOCH2_CANONICAL_ACTIVATION_UTC},
    }
    calls: list[str] = []
    monkeypatch.setattr(
        "smartcrypto.research.canonical_treatment.postfix_latency_coverage_monitor.read_object",
        lambda _path: registration,
    )
    monkeypatch.setattr(
        "smartcrypto.research.canonical_treatment.postfix_latency_coverage_monitor.epoch2.validate_registration",
        lambda _registration: calls.append("registration"),
    )
    monkeypatch.setattr(
        "smartcrypto.research.canonical_treatment.postfix_latency_coverage_monitor.epoch2.validate_current_epoch2_runtime",
        lambda _registration, _audit: calls.append("runtime"),
    )
    assert load_epoch2_registration(tmp_path / "registration.json", {}) is registration
    assert calls == ["registration", "runtime"]

    for changed in (
        {**registration, "registration_sha256": "0" * 64},
        {
            **registration,
            "epoch_manifest": {
                **registration["epoch_manifest"],
                "formal_activation_utc": "2026-10-06T19:08:33.155008Z",
            },
        },
    ):
        monkeypatch.setattr(
            "smartcrypto.research.canonical_treatment.postfix_latency_coverage_monitor.read_object",
            lambda _path, payload=changed: payload,
        )
        with pytest.raises(EvidenceError, match="epoch2_canonical_registration_identity_mismatch"):
            load_epoch2_registration(tmp_path / "registration.json", {})

    monkeypatch.setattr(
        "smartcrypto.research.canonical_treatment.postfix_latency_coverage_monitor.read_object",
        lambda _path: registration,
    )

    def drift(_registration: object, _audit: object) -> None:
        raise CausalEpoch2FoundationError("epoch2_runtime_drift")

    monkeypatch.setattr(
        "smartcrypto.research.canonical_treatment.postfix_latency_coverage_monitor.epoch2.validate_current_epoch2_runtime",
        drift,
    )
    with pytest.raises(EvidenceError, match="epoch2_runtime_drift"):
        load_epoch2_registration(tmp_path / "registration.json", {})


def test_epoch2_report_path_cannot_overwrite_legacy_report(tmp_path: Path) -> None:
    relative = Path("data/reports/canonical_treatment/report.json")
    assert report_output_path(tmp_path, relative, None) == tmp_path / relative
    assert report_output_path(tmp_path, relative, tmp_path / "registration.json") == (
        tmp_path / "data/reports/canonical_treatment/paper-b-epoch-2/report.json"
    )


def test_cli_passes_explicit_epoch_registration_without_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_monitor(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "ok", "write_performed": False}

    monkeypatch.setattr(
        "scripts.build_paper_b_postfix_latency_coverage_monitor_v1.run_monitor", fake_run_monitor
    )
    assert main(["--runtime-root", "C:/runtime", "--epoch-registration", "C:/epoch.json"]) == 0
    assert captured["epoch_registration"] == Path("C:/epoch.json")
    assert captured["write_report"] is False
