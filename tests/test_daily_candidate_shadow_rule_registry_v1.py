from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_candidate_shadow_rule_registry import (
    DAILY_CANDIDATE_SHADOW_RULE_REGISTRY_SCHEMA_VERSION,
    MANDATORY_BLOCKERS,
    build_candidate_shadow_rule_registry,
    build_daily_candidate_shadow_rule_registry_report,
    calculate_candidate_blockers,
    pattern_to_candidate_shadow_rule,
    validate_daily_candidate_shadow_rule_registry_report,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "smartcrypto/research/daily_candidate_shadow_rule_registry.py"
CLI = ROOT / "scripts/build_daily_candidate_shadow_rule_registry_v1.py"
DOC = ROOT / "docs/DAILY_CANDIDATE_SHADOW_RULE_REGISTRY_V1.md"
TEST_FILE = ROOT / "tests/test_daily_candidate_shadow_rule_registry_v1.py"
NEW_FILES = (MODULE, CLI, DOC, TEST_FILE)


def eligible_pattern(
    target: str = "mistake",
    *,
    pattern_id: str = "p1",
    support_count: int = 3,
    confidence: float = 0.75,
    lift: float = 1.5,
) -> dict[str, object]:
    return {
        "pattern_id": pattern_id,
        "pattern_type": "single_bucket",
        "target": target,
        "conditions": ["rsi_high"],
        "support_count": support_count,
        "target_count": 3,
        "non_target_count": 1,
        "confidence": confidence,
        "baseline_rate": 0.5,
        "lift": lift,
        "coverage_pct": 40.0,
        "examples_sample": ["t1", "t2", "t3"],
        "research_interpretation": "descriptive only",
        "creates_candidate_rule": False,
        "operational_action_allowed": False,
        "requires_oos_validation": True,
        "promotion_allowed": False,
    }


def test_report_with_none_inputs_uses_no_runtime_rows_loaded() -> None:
    payload = build_daily_candidate_shadow_rule_registry_report(ROOT)

    assert payload["schema_version"] == DAILY_CANDIDATE_SHADOW_RULE_REGISTRY_SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["candidate_registry"]["candidate_count"] == 0
    assert payload["write_performed"] is False
    assert payload["validation_errors"] == []


def test_empty_registry_stays_blocked() -> None:
    payload = build_daily_candidate_shadow_rule_registry_report(ROOT, patterns=[])

    assert payload["status"] == "blocked"
    assert payload["input_mode"] == "in_memory_candidate_registry_inputs"
    assert payload["candidate_registry"]["candidate_count"] == 0
    assert "no_patterns_available" in payload["candidate_registry"]["registry_quality_notes"]


def test_eligible_pattern_becomes_research_candidate() -> None:
    registry = build_candidate_shadow_rule_registry([eligible_pattern()])
    candidate = registry["candidate_rules"][0]

    assert registry["candidate_count"] == 1
    assert registry["rejected_pattern_count"] == 0
    assert candidate["candidate_status"] == "research_candidate"
    assert candidate["registry_status"] == "registered_research_only"
    assert candidate["promotion_status"] == "blocked"
    assert candidate["application_status"] == "not_applied"
    assert candidate["source_pattern_id"] == "p1"
    assert candidate["conditions"] == ["rsi_high"]


def test_weak_patterns_are_rejected_by_support_confidence_and_lift() -> None:
    patterns = [
        eligible_pattern(pattern_id="low_support", support_count=1),
        eligible_pattern(pattern_id="low_confidence", confidence=0.25),
        eligible_pattern(pattern_id="low_lift", lift=0.5),
    ]

    registry = build_candidate_shadow_rule_registry(patterns)
    rejected = {
        item["pattern_id"]: item["rejection_reasons"]
        for item in registry["rejected_patterns_sample"]
    }

    assert registry["candidate_count"] == 0
    assert registry["rejected_pattern_count"] == 3
    assert "support_below_minimum" in rejected["low_support"]
    assert "confidence_below_minimum" in rejected["low_confidence"]
    assert "lift_below_minimum" in rejected["low_lift"]


def test_rule_kind_mapping_for_all_requested_targets() -> None:
    expected = {
        "mistake": "block_candidate",
        "stop_loss_loss": "block_candidate",
        "fast_loss_under_30m": "block_candidate",
        "winner": "allow_candidate",
        "profitable_trade": "allow_candidate",
    }
    for index, (target, rule_kind) in enumerate(expected.items(), start=1):
        candidate = pattern_to_candidate_shadow_rule(
            eligible_pattern(target, pattern_id=f"p{index}"),
            index,
        )
        assert candidate["rule_kind"] == rule_kind


def test_candidate_contains_mandatory_blockers_and_never_applies() -> None:
    candidate = pattern_to_candidate_shadow_rule(eligible_pattern(), 1)

    for blocker in MANDATORY_BLOCKERS:
        assert blocker in candidate["blockers"]
    assert candidate["operational_action_allowed"] is False
    assert candidate["requires_oos_validation"] is True
    assert candidate["promotion_allowed"] is False
    assert candidate["applies_to_ai_shadow_runtime"] is False
    assert candidate["applies_to_runtime"] is False
    assert candidate["writes_runtime"] is False
    assert candidate["writes_data"] is False


def test_calculate_candidate_blockers_adds_contract_specific_blockers() -> None:
    candidate = dict(pattern_to_candidate_shadow_rule(eligible_pattern(), 1))
    candidate["promotion_allowed"] = True
    candidate["operational_action_allowed"] = True

    blockers = calculate_candidate_blockers(candidate)

    assert "promotion_not_blocked" in blockers
    assert "operational_action_not_blocked" in blockers
    for blocker in MANDATORY_BLOCKERS:
        assert blocker in blockers


def test_registry_scope_and_counts_are_research_only() -> None:
    registry = build_candidate_shadow_rule_registry(
        [
            eligible_pattern("mistake", pattern_id="m"),
            eligible_pattern("winner", pattern_id="w"),
        ]
    )
    scope = registry["registry_scope"]

    assert registry["candidate_counts_by_rule_kind"] == {
        "allow_candidate": 1,
        "block_candidate": 1,
    }
    assert registry["candidate_counts_by_target"] == {"mistake": 1, "winner": 1}
    assert scope["creates_candidate_rules"] is True
    assert scope["registers_candidate_rules"] is True
    assert scope["research_registry_only"] is True
    assert scope["applies_candidate_rules"] is False
    assert scope["updates_ai_shadow_runtime"] is False
    assert scope["runs_oos_validation"] is False


def test_report_preserves_safety_flags_and_extra_branch_flags() -> None:
    payload = build_daily_candidate_shadow_rule_registry_report(
        ROOT,
        patterns=[eligible_pattern()],
    )

    for key, expected in SAFETY_FLAGS.items():
        assert payload[key] is expected
    assert payload["runs_oos_validation"] is False
    assert payload["applies_shadow_rules"] is False
    assert payload["promotes_shadow_rules"] is False
    assert payload["readiness_policy"]["candidate_registry_is_not_readiness_evidence"] is True
    assert payload["operator_decision"]["shadow_rule_promotion_allowed"] is False


def test_report_can_mine_patterns_from_in_memory_catalog_and_features() -> None:
    catalog_entries = [
        {"trade_id": "m1", "classification": "mistake", "side": "long"},
        {"trade_id": "m2", "classification": "mistake", "side": "long"},
    ]
    feature_rows = [
        {"trade_id": "m1", "side": "long", "rsi_14": 72},
        {"trade_id": "m2", "side": "long", "rsi_14": 74},
    ]

    payload = build_daily_candidate_shadow_rule_registry_report(
        ROOT,
        catalog_entries=catalog_entries,
        feature_rows=feature_rows,
    )

    assert payload["input_mode"] == "in_memory_candidate_registry_inputs"
    assert payload["candidate_registry"]["candidate_count"] > 0


def test_validate_report_accepts_valid_payload_and_rejects_relaxed_contracts() -> None:
    payload = build_daily_candidate_shadow_rule_registry_report(
        ROOT,
        patterns=[eligible_pattern()],
    )
    assert validate_daily_candidate_shadow_rule_registry_report(payload) == []

    mutated = dict(payload)
    mutated["research_only"] = False
    mutated["operational_authority"] = True
    mutated["writes_data"] = True
    mutated["registry_scope"] = dict(payload["registry_scope"])
    mutated["registry_scope"]["applies_candidate_rules"] = True
    mutated["registry_scope"]["updates_ai_shadow_runtime"] = True
    mutated["registry_scope"]["runs_oos_validation"] = True
    mutated["candidate_registry"] = dict(payload["candidate_registry"])
    mutated["candidate_registry"]["candidate_rules"] = [
        dict(payload["candidate_registry"]["candidate_rules"][0])
    ]
    mutated["candidate_registry"]["candidate_rules"][0][
        "operational_action_allowed"
    ] = True
    mutated["candidate_registry"]["candidate_rules"][0]["promotion_allowed"] = True

    errors = validate_daily_candidate_shadow_rule_registry_report(mutated)
    assert "research_only_must_be_true" in errors
    assert "operational_authority_must_be_false" in errors
    assert "writes_data_must_be_false" in errors
    assert "registry_scope_applies_candidate_rules_mismatch" in errors
    assert "registry_scope_updates_ai_shadow_runtime_mismatch" in errors
    assert "registry_scope_runs_oos_validation_mismatch" in errors
    assert "candidate_0_operational_action_allowed_must_be_false" in errors
    assert "candidate_0_promotion_allowed_must_be_false" in errors


def test_cli_no_write_json_returns_payload_without_file(tmp_path: Path) -> None:
    output = tmp_path / "registry.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(ROOT),
            "--output",
            str(output),
            "--no-write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["research_only"] is True
    assert payload["read_only"] is True
    assert payload["operational_authority"] is False
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert not output.exists()


def test_cli_writes_only_to_explicit_safe_output(tmp_path: Path) -> None:
    output = tmp_path / "registry.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(ROOT),
            "--output",
            str(output),
            "--min-support-count",
            "3",
            "--min-confidence",
            "0.7",
            "--min-lift",
            "1.25",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    disk_payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["write_requested"] is True
    assert payload["write_performed"] is True
    assert payload["min_support_count"] == 3
    assert payload["min_confidence"] == 0.7
    assert payload["min_lift"] == 1.25
    assert disk_payload["write_performed"] is True


def test_cli_blocks_output_under_data_or_runtime() -> None:
    for restricted in ("data/reports/registry.json", "runtime/registry.json"):
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--project-root",
                str(ROOT),
                "--output",
                str(ROOT / restricted),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        assert completed.returncode == 1
        assert payload["status"] == "blocked"
        assert payload["reason"] == "output_path_in_runtime_or_data_scope"
        assert payload["write_performed"] is False
        assert not (ROOT / restricted).exists()


def test_new_files_do_not_contain_forbidden_runtime_tokens() -> None:
    forbidden = (
        "request" + "s",
        "http" + "x",
        "aio" + "http",
        "cc" + "xt",
        "pan" + "das",
        "open" + "pyxl",
        "sqlite" + "3",
        "to_" + "parquet",
        "to_" + "excel",
        "to_" + "sql",
        "create_" + "order",
        "cancel_" + "order",
        "fetch_" + "balance",
        "send_" + "order",
        "TELE" + "GRAM",
        "NT" + "FY",
        "BINANCE_" + "SECRET",
        "BINANCE_" + "API_KEY",
    )
    for path in NEW_FILES:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text
