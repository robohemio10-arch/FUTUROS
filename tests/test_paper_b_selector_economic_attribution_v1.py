from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scripts.build_paper_b_selector_economic_attribution_v1 import main
from smartcrypto.learning.qlib_v3_prospective.contracts import EvidenceError
from smartcrypto.research.canonical_treatment.selector_economic_attribution import (
    build_attribution_report,
    run_attribution,
)

NOW = "2026-10-01T20:00:00Z"


def _inputs() -> dict[str, Any]:
    events = [f"decision-event:{i:040x}" for i in range(4)]
    operational = {
        event: {"decision_timestamp": "2026-09-24T21:00:00Z" if i == 0
                else "2026-09-25T21:00:00Z", "pair": "BTC/USDT:USDT",
                "symbol": "BTCUSDT", "side": "long" if i % 2 == 0 else "short"}
        for i, event in enumerate(events)
    }
    scored = {
        event: {"selected": i >= 2, "qlib_score": [-0.2, 0.1, 0.3, -0.4][i],
                "score_margin": None, "first_observed_at_utc": "2026-09-25T21:00:01Z",
                "signal_timestamp_utc": "2026-09-25T21:00:01Z",
                "valid_until": "2026-09-25T22:00:00Z"}
        for i, event in enumerate(events)
    }
    control = [_trade(event, i + 1, [-3.0, 5.0, 4.0, -2.0][i], operational[event]["side"])
               for i, event in enumerate(events)]
    treatment = [_trade(events[2], 10, 6.0, "long"), _trade(events[3], 11, -1.0, "short")]
    return {
        "foundation": {"status": "ok", "baseline_sha256": "baseline",
                       "pre_fix_eligible_event_ids": [events[0]],
                       "historical_debt": {"eligible": 1, "scored_at_fix": 1,
                                           "scored_event_ids_at_fix": [events[0]],
                                           "missing_event_ids": []},
                       "post_fix_counters": {"eligible_event_ids": events[1:]}},
        "coverage": {"status": "ok", "report_sha256": "coverage", "causal_funnel": {},
                     "postfix_population": {"eligible_event_ids": events[1:],
                                            "scored_event_ids": events[1:]}},
        "operational": operational, "scored": scored,
        "control_trades": control, "treatment_trades": treatment,
        "generated_at_utc": NOW,
    }


def _trade(event: str, trade_id: int, pnl: float, side: str) -> dict[str, Any]:
    return {"id": trade_id, "enter_tag": f"decision_event_id={event}", "pair": "BTC/USDT:USDT",
            "is_short": int(side == "short"), "is_open": 0,
            "open_date": "2026-09-25 21:00:02" if trade_id >= 10 else "2026-09-25 21:00:03",
            "close_date": "2026-09-25 21:10:00", "close_profit_abs": pnl}


def test_exact_closed_matrix_and_paired_metrics() -> None:
    report = build_attribution_report(**_inputs())
    s = report["summary"]
    assert (s["eligible_count"], s["resolved_count"], s["allow_count"], s["abstain_count"]) == (4, 4, 2, 2)
    assert (s["good_abstention_count"], s["false_abstention_count"],
            s["good_selection_count"], s["bad_selection_count"]) == (1, 1, 1, 1)
    assert (s["avoided_loss_usdt"], s["missed_profit_usdt"],
            s["selected_profit_usdt"], s["selected_loss_usdt"]) == (3, 5, 6, -1)
    assert s["net_selector_value_usdt"] == 3
    assert s["control_net_pnl"] == 4
    assert s["treatment_net_pnl"] == 5
    assert s["delta_net_pnl"] == 1
    assert report["by"]["epoch"]["PRE_FIX"]["good_abstention_count"] == 1
    assert report["by"]["direction"]["SHORT"]["bad_selection_count"] == 1
    assert report["score_margin_status"] == "UNAVAILABLE"
    assert all(value == "PENDING_SAMPLE" for value in report["checkpoints"].values())
    assert report["safety"]["sends_orders"] is False


def test_allow_without_closed_treatment_is_unresolved_not_control_fallback() -> None:
    inputs = _inputs()
    inputs["treatment_trades"] = []
    report = build_attribution_report(**inputs)
    assert report["summary"]["selected_closed_count"] == 0
    assert report["summary"]["resolved_count"] == 2


def test_open_trade_and_zero_pnl_do_not_invent_outcome() -> None:
    inputs = _inputs()
    inputs["control_trades"][0]["close_profit_abs"] = 0.0
    inputs["treatment_trades"][0]["is_open"] = 1
    inputs["treatment_trades"][0]["close_date"] = None
    report = build_attribution_report(**inputs)
    assert report["summary"]["resolved_count"] == 2
    assert report["summary"]["good_selection_count"] == 0


def test_zero_pnl_selected_trade_counts_for_sample_not_good_or_bad() -> None:
    inputs = _inputs()
    inputs["treatment_trades"][0]["close_profit_abs"] = 0.0
    report = build_attribution_report(**inputs)
    assert report["summary"]["selected_closed_count"] == 2
    assert report["summary"]["good_selection_count"] == 0
    assert report["summary"]["bad_selection_count"] == 1


def test_missing_historical_scored_is_not_late_reclassified() -> None:
    inputs = _inputs()
    inputs["foundation"]["historical_debt"]["scored_event_ids_at_fix"] = []
    inputs["foundation"]["historical_debt"]["scored_at_fix"] = 0
    inputs["foundation"]["historical_debt"]["missing_event_ids"] = [
        inputs["foundation"]["pre_fix_eligible_event_ids"][0]
    ]
    report = build_attribution_report(**inputs)
    assert report["by"]["epoch"]["PRE_FIX"]["resolved_count"] == 0


def test_duplicate_trade_and_side_mismatch_fail_closed() -> None:
    inputs = _inputs()
    inputs["control_trades"].append(deepcopy(inputs["control_trades"][0]))
    with pytest.raises(EvidenceError, match="attribution_trade_id_invalid_or_duplicate"):
        build_attribution_report(**inputs)
    inputs = _inputs()
    inputs["control_trades"][0]["is_short"] = 1
    with pytest.raises(EvidenceError, match="attribution_trade_causal_identity_invalid"):
        build_attribution_report(**inputs)


def test_missing_crosswalk_or_blocked_coverage_fails_closed() -> None:
    inputs = _inputs()
    inputs["coverage"]["status"] = "blocked"
    with pytest.raises(EvidenceError, match="upstream_foundation_or_coverage_blocked"):
        build_attribution_report(**inputs)
    inputs = _inputs()
    del inputs["scored"][next(iter(inputs["scored"]))]
    report = build_attribution_report(**inputs)
    assert report["summary"]["resolved_count"] == 3


def test_score_margin_only_when_explicitly_present() -> None:
    inputs = _inputs()
    event = list(inputs["scored"])[2]
    inputs["scored"][event]["score_margin"] = 0.2
    report = build_attribution_report(**inputs)
    assert report["score_margin_status"] == "AVAILABLE"
    assert report["by"]["score_margin_bucket"]["POSITIVE"]["resolved_count"] == 1


def test_missing_postfix_coverage_event_fails_closed() -> None:
    inputs = _inputs()
    inputs["coverage"]["postfix_population"]["eligible_event_ids"] = []
    with pytest.raises(EvidenceError, match="attribution_coverage_cohort_mismatch"):
        build_attribution_report(**inputs)


def test_control_trade_before_v3_observation_is_not_abstention_outcome() -> None:
    inputs = _inputs()
    inputs["control_trades"][1]["open_date"] = "2026-09-25 21:00:00"
    report = build_attribution_report(**inputs)
    assert report["summary"]["false_abstention_count"] == 0
    assert report["summary"]["resolved_count"] == 3


def test_coverage_scored_identity_mismatch_fails_closed() -> None:
    inputs = _inputs()
    inputs["coverage"]["postfix_population"]["scored_event_ids"] = []
    with pytest.raises(EvidenceError, match="attribution_coverage_scored_mismatch"):
        build_attribution_report(**inputs)


def test_selected_closed_checkpoints_use_closed_opportunities() -> None:
    inputs = _inputs()
    for i in range(100, 148):
        event = f"decision-event:{i:040x}"
        inputs["operational"][event] = {
            "decision_timestamp": "2026-09-25T21:00:00Z", "pair": "BTC/USDT:USDT",
            "symbol": "BTCUSDT", "side": "long",
        }
        inputs["scored"][event] = deepcopy(next(
            row for row in inputs["scored"].values() if row["selected"]
        ))
        inputs["foundation"]["post_fix_counters"]["eligible_event_ids"].append(event)
        inputs["coverage"]["postfix_population"]["eligible_event_ids"].append(event)
        inputs["coverage"]["postfix_population"]["scored_event_ids"].append(event)
        inputs["control_trades"].append(_trade(event, i + 100, 1.0, "long"))
        inputs["treatment_trades"].append(_trade(event, i + 200, 1.0, "long"))
    report = build_attribution_report(**inputs)
    assert report["summary"]["selected_closed_count"] == 50
    assert set(report["checkpoints"].values()) == {"PASS"}


def test_missing_runtime_sources_are_blocked_without_default_write(tmp_path: Path) -> None:
    report = run_attribution(project_root=tmp_path, runtime_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["summary"] is None
    assert report["write_performed"] is False
    assert not (tmp_path / "data/reports").exists()


def _epoch2_inputs() -> dict[str, Any]:
    inputs = _inputs()
    old = inputs["foundation"]["pre_fix_eligible_event_ids"][0]
    inputs["operational"].pop(old)
    inputs["scored"].pop(old)
    inputs["control_trades"] = [row for row in inputs["control_trades"] if old not in row["enter_tag"]]
    inputs["foundation"]["pre_fix_eligible_event_ids"] = []
    inputs["foundation"]["historical_debt"] = {
        "eligible": 0, "scored_at_fix": 0, "scored_event_ids_at_fix": [], "missing_event_ids": [],
    }
    identity = {
        "epoch_id": "paper-b-epoch-2", "formal_activation_utc": "2026-09-25T21:00:00Z",
        "registration_sha256": "a" * 64, "epoch_baseline_sha256": "b" * 64,
    }
    inputs["foundation"]["epoch"] = identity
    inputs["foundation"]["baseline_sha256"] = identity["epoch_baseline_sha256"]
    inputs["coverage"]["epoch"] = identity.copy()
    return inputs


def test_epoch2_attribution_contains_only_post_activation_opportunities() -> None:
    inputs = _epoch2_inputs()
    report = build_attribution_report(**inputs)
    assert report["epoch"] == inputs["foundation"]["epoch"]
    assert report["summary"]["eligible_count"] == 3
    assert set(report["by"]["epoch"]) == {"EPOCH_2"}
    assert all(row["epoch"] == "EPOCH_2" for row in report["rows"])
    assert not any(row["decision_event_id"] == _inputs()["foundation"]["pre_fix_eligible_event_ids"][0]
                   for row in report["rows"])


def test_epoch2_attribution_rejects_boundary_and_identity_mismatch() -> None:
    inputs = _epoch2_inputs()
    inputs["coverage"]["epoch"]["registration_sha256"] = "c" * 64
    with pytest.raises(EvidenceError, match="attribution_epoch2_identity_or_boundary_invalid"):
        build_attribution_report(**inputs)
    inputs = _epoch2_inputs()
    first = next(iter(inputs["operational"]))
    inputs["operational"][first]["decision_timestamp"] = "2026-09-25T20:59:59Z"
    with pytest.raises(EvidenceError, match="attribution_epoch2_identity_or_boundary_invalid"):
        build_attribution_report(**inputs)


def test_epoch2_attribution_cli_forwards_registration_without_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_attribution(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "ok", "write_performed": False}

    monkeypatch.setattr(
        "scripts.build_paper_b_selector_economic_attribution_v1.run_attribution",
        fake_run_attribution,
    )
    assert main(["--runtime-root", "C:/runtime", "--epoch-registration", "C:/epoch.json"]) == 0
    assert captured["epoch_registration"] == Path("C:/epoch.json")
    assert captured["write_report"] is False
