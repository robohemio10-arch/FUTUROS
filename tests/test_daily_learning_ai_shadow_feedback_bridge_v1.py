from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from smartcrypto.research.daily_learning_ai_shadow_feedback_bridge import (
    DAILY_LEARNING_AI_SHADOW_FEEDBACK_BRIDGE_SCHEMA_VERSION,
    build_ai_shadow_feedback_bridge,
    build_daily_learning_ai_shadow_feedback_bridge_report,
    build_feedback_event,
    classify_feedback_event,
    validate_daily_learning_ai_shadow_feedback_bridge_report,
)


def _oos_result(
    *,
    candidate_rule_id: str = "rule_1",
    rule_kind: str = "block_candidate",
    oos_status: str = "oos_research_pass",
    target: str = "mistake",
    out_of_sample_confidence: float = 0.75,
) -> dict[str, object]:
    return {
        "candidate_rule_id": candidate_rule_id,
        "target": target,
        "rule_kind": rule_kind,
        "conditions": ["side_long", "lb_10m_negative"],
        "in_sample_count": 10,
        "out_of_sample_count": 6,
        "in_sample_match_count": 5,
        "out_of_sample_match_count": 3,
        "in_sample_target_match_count": 4,
        "out_of_sample_target_match_count": 2,
        "in_sample_confidence": 0.8,
        "out_of_sample_confidence": out_of_sample_confidence,
        "in_sample_baseline_rate": 0.4,
        "out_of_sample_baseline_rate": 0.35,
        "in_sample_lift": 2.0,
        "out_of_sample_lift": 1.8,
        "confidence_degradation": 0.05,
        "support_status": "sufficient_oos_support",
        "oos_status": oos_status,
        "research_validation_passed": oos_status == "oos_research_pass",
        "promotion_status": "blocked",
        "application_status": "not_applied",
        "operational_action_allowed": False,
        "applies_to_ai_shadow_runtime": False,
        "applies_to_freqtrade": False,
        "applies_to_risk_manager": False,
        "requires_manual_go_no_go": True,
        "requires_30_day_gap_free_soak": True,
        "promotion_allowed": False,
        "blockers": ["research_only_oos_validation"],
    }


def _candidate(
    *,
    candidate_rule_id: str = "rule_1",
    rule_kind: str = "block_candidate",
    target: str = "mistake",
) -> dict[str, object]:
    return {
        "candidate_rule_id": candidate_rule_id,
        "source_pattern_id": "pattern_1",
        "source_pattern_type": "single_bucket",
        "target": target,
        "conditions": ["side_long", "lb_10m_negative"],
        "support_count": 5,
        "target_count": 4,
        "confidence": 0.8,
        "baseline_rate": 0.4,
        "lift": 2.0,
        "coverage_pct": 50.0,
        "rule_family": "shadow_filter_candidate",
        "rule_kind": rule_kind,
        "candidate_status": "research_candidate",
        "registry_status": "registered_research_only",
        "promotion_status": "blocked",
        "application_status": "not_applied",
        "blockers": ["not_oos_validated"],
        "safety_flags": {"operational_action_allowed": False},
        "research_interpretation": "research only",
        "creates_candidate_rule": True,
        "registers_candidate_rule": True,
        "operational_action_allowed": False,
        "requires_oos_validation": True,
        "promotion_allowed": False,
        "writes_runtime": False,
        "writes_data": False,
    }


def test_report_with_none_inputs_uses_no_runtime_mode() -> None:
    report = build_daily_learning_ai_shadow_feedback_bridge_report(project_root=".")
    assert report["schema_version"] == DAILY_LEARNING_AI_SHADOW_FEEDBACK_BRIDGE_SCHEMA_VERSION
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["validation_errors"] == []


def test_report_with_empty_list_stays_blocked() -> None:
    report = build_daily_learning_ai_shadow_feedback_bridge_report(oos_validation_results=[])
    assert report["input_mode"] == "in_memory_inputs"
    assert report["status"] == "blocked"
    assert report["feedback_bridge"]["feedback_event_count"] == 0


@pytest.mark.parametrize(
    ("oos_status", "rule_kind", "expected_type", "expected_direction"),
    [
        ("oos_research_pass", "block_candidate", "candidate_negative_signal", "reinforce"),
        ("oos_research_pass", "allow_candidate", "candidate_positive_signal", "reinforce"),
        ("oos_research_fail", "block_candidate", "needs_review", "deprioritize"),
        ("insufficient_oos_support", "block_candidate", "insufficient_evidence", "observe"),
        ("no_oos_data", "block_candidate", "insufficient_evidence", "observe"),
    ],
)
def test_feedback_classification_matrix(
    oos_status: str,
    rule_kind: str,
    expected_type: str,
    expected_direction: str,
) -> None:
    result = _oos_result(oos_status=oos_status, rule_kind=rule_kind)
    classification = classify_feedback_event(result, _candidate(rule_kind=rule_kind))
    assert classification["feedback_type"] == expected_type
    assert classification["feedback_direction"] == expected_direction
    assert classification["review_required"] is True


def test_missing_candidate_id_is_insufficient_evidence() -> None:
    classification = classify_feedback_event({"oos_status": "oos_research_pass"})
    assert classification["feedback_type"] == "insufficient_evidence"
    assert classification["feedback_direction"] == "none"


def test_feedback_event_is_record_only_and_not_applied() -> None:
    event = build_feedback_event(_oos_result(), _candidate(), index=3)
    assert event["feedback_status"] == "record_only"
    assert event["feedback_application_status"] == "not_applied"
    assert event["ai_shadow_runtime_update_allowed"] is False
    assert event["ai_shadow_sqlite_write_allowed"] is False
    assert event["ai_shadow_threshold_update_allowed"] is False
    assert event["ai_shadow_policy_update_allowed"] is False
    assert event["operational_action_allowed"] is False
    assert event["promotion_allowed"] is False
    assert "research_only_feedback" in event["blockers"]
    assert "live_canary_blocked" in event["blockers"]


def test_bridge_summary_counts_by_type_direction_rule_kind_and_target() -> None:
    bridge = build_ai_shadow_feedback_bridge(
        [
            _oos_result(candidate_rule_id="rule_1", rule_kind="block_candidate", target="mistake"),
            _oos_result(
                candidate_rule_id="rule_2",
                rule_kind="allow_candidate",
                target="winner",
                oos_status="oos_research_pass",
            ),
            _oos_result(
                candidate_rule_id="rule_3",
                rule_kind="block_candidate",
                target="mistake",
                oos_status="oos_research_fail",
            ),
        ],
        candidate_rules=[
            _candidate(candidate_rule_id="rule_1", rule_kind="block_candidate", target="mistake"),
            _candidate(candidate_rule_id="rule_2", rule_kind="allow_candidate", target="winner"),
            _candidate(candidate_rule_id="rule_3", rule_kind="block_candidate", target="mistake"),
        ],
    )
    summary = bridge["feedback_summary"]
    assert summary["feedback_event_count"] == 3
    assert summary["feedback_type_counts"]["candidate_negative_signal"] == 1
    assert summary["feedback_type_counts"]["candidate_positive_signal"] == 1
    assert summary["feedback_type_counts"]["needs_review"] == 1
    assert summary["feedback_direction_counts"]["reinforce"] == 2
    assert summary["rule_kind_counts"]["block_candidate"] == 2
    assert summary["target_counts"]["mistake"] == 2


def test_feedback_events_sample_limited_to_20() -> None:
    results = [_oos_result(candidate_rule_id=f"rule_{index}") for index in range(25)]
    bridge = build_ai_shadow_feedback_bridge(results)
    assert bridge["feedback_event_count"] == 25
    assert len(bridge["feedback_events_sample"]) == 20


def test_feedback_scope_is_inert() -> None:
    report = build_daily_learning_ai_shadow_feedback_bridge_report(oos_validation_results=[_oos_result()])
    scope = report["feedback_scope"]
    assert scope["builds_ai_shadow_feedback"] is True
    assert scope["research_feedback_only"] is True
    assert scope["feedback_record_only"] is True
    assert scope["applies_feedback_to_ai_shadow"] is False
    assert scope["writes_ai_shadow_sqlite"] is False
    assert scope["updates_ai_shadow_runtime"] is False
    assert scope["updates_ai_shadow_thresholds"] is False
    assert scope["updates_ai_shadow_policy"] is False
    assert scope["promotes_rules"] is False


def test_safety_flags_preserved() -> None:
    report = build_daily_learning_ai_shadow_feedback_bridge_report(oos_validation_results=[_oos_result()])
    assert report["research_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["read_only"] is True
    assert report["operational_authority"] is False
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False
    assert report["order_submission_enabled"] is False
    assert report["exchange_private_access"] is False


def test_validate_valid_report_returns_empty_list() -> None:
    report = build_daily_learning_ai_shadow_feedback_bridge_report(oos_validation_results=[_oos_result()])
    assert validate_daily_learning_ai_shadow_feedback_bridge_report(report) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("research_only", False),
        ("operational_authority", True),
        ("writes_data", True),
        ("applies_feedback_to_ai_shadow", True),
        ("writes_ai_shadow_sqlite", True),
        ("updates_ai_shadow_runtime", True),
        ("updates_ai_shadow_thresholds", True),
        ("promotes_shadow_rules", True),
    ],
)
def test_validation_fails_for_unsafe_report_flags(field: str, value: object) -> None:
    report = build_daily_learning_ai_shadow_feedback_bridge_report(oos_validation_results=[_oos_result()])
    report[field] = value
    errors = validate_daily_learning_ai_shadow_feedback_bridge_report(report)
    assert errors


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operational_action_allowed", True),
        ("promotion_allowed", True),
        ("ai_shadow_runtime_update_allowed", True),
        ("ai_shadow_sqlite_write_allowed", True),
        ("ai_shadow_threshold_update_allowed", True),
    ],
)
def test_validation_fails_for_unsafe_feedback_event_flags(field: str, value: object) -> None:
    report = build_daily_learning_ai_shadow_feedback_bridge_report(oos_validation_results=[_oos_result()])
    report["feedback_bridge"]["feedback_events"][0][field] = value
    errors = validate_daily_learning_ai_shadow_feedback_bridge_report(report)
    assert errors


def test_cli_no_write_json_returns_valid_payload() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_daily_learning_ai_shadow_feedback_bridge_v1.py",
            "--project-root",
            ".",
            "--no-write",
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["write_performed"] is False


def test_cli_output_to_temp_path_writes_payload(tmp_path: Path) -> None:
    output = tmp_path / "feedback_bridge.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_daily_learning_ai_shadow_feedback_bridge_v1.py",
            "--project-root",
            ".",
            "--output",
            str(output),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["write_performed"] is True
    assert output.exists()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["status"] == "blocked"


def test_cli_blocks_output_under_data_or_runtime() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_daily_learning_ai_shadow_feedback_bridge_v1.py",
            "--project-root",
            ".",
            "--output",
            "data/forbidden.json",
            "--json",
        ],
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert "blocked_output_path" in payload["validation_errors"]


def test_static_new_files_do_not_use_forbidden_tokens() -> None:
    forbidden = [
        "requests",
        "httpx",
        "aio" + "http",
        "ccxt",
        "pan" + "das",
        "open" + "pyxl",
        "sql" + "ite3",
        "to_" + "parquet",
        "to_" + "excel",
        "to_" + "sql",
        "create_" + "order",
        "cancel_" + "order",
        "fetch_" + "balance",
        "send_" + "order",
        "TELE" + "GRAM",
        "N" + "TFY",
        "BINANCE_" + "SECRET",
        "BINANCE_" + "API_KEY",
    ]
    paths = [
        Path("smartcrypto/research/daily_learning_ai_shadow_feedback_bridge.py"),
        Path("scripts/build_daily_learning_ai_shadow_feedback_bridge_v1.py"),
        Path("docs/DAILY_LEARNING_AI_SHADOW_FEEDBACK_BRIDGE_V1.md"),
    ]
    for path in paths:
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in content, f"{token} found in {path}"
