from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_shadow_rule_oos_validation import (
    DAILY_SHADOW_RULE_OOS_VALIDATION_SCHEMA_VERSION,
    MANDATORY_OOS_BLOCKERS,
    build_daily_shadow_rule_oos_validation_report,
    entry_matches_candidate_rule,
    entry_matches_target,
    evaluate_candidate_rule_oos,
    evaluate_candidate_rules_oos,
    split_entries_for_oos,
    validate_daily_shadow_rule_oos_validation_report,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "smartcrypto/research/daily_shadow_rule_oos_validation.py"
CLI = ROOT / "scripts/build_daily_shadow_rule_oos_validation_v1.py"
DOC = ROOT / "docs/DAILY_SHADOW_RULE_OOS_VALIDATION_V1.md"
TEST_FILE = ROOT / "tests/test_daily_shadow_rule_oos_validation_v1.py"
NEW_FILES = (MODULE, CLI, DOC, TEST_FILE)


def candidate_rule(
    *,
    conditions: list[str] | None = None,
    target: str = "mistake",
    rule_id: str = "c1",
) -> dict[str, object]:
    return {
        "candidate_rule_id": rule_id,
        "source_pattern_id": "p1",
        "source_pattern_type": "single_bucket",
        "target": target,
        "conditions": conditions or ["rsi_high", "side_long"],
        "support_count": 4,
        "target_count": 3,
        "confidence": 0.75,
        "baseline_rate": 0.5,
        "lift": 1.5,
        "coverage_pct": 50.0,
        "rule_family": "shadow_filter_candidate",
        "rule_kind": "block_candidate",
        "candidate_status": "research_candidate",
        "registry_status": "registered_research_only",
        "promotion_status": "blocked",
        "application_status": "not_applied",
        "blockers": ["research_only_candidate"],
        "safety_flags": {},
        "research_interpretation": "descriptive only",
        "creates_candidate_rule": True,
        "registers_candidate_rule": True,
        "operational_action_allowed": False,
        "requires_oos_validation": True,
        "promotion_allowed": False,
        "writes_runtime": False,
        "writes_data": False,
    }


def catalog_entry(
    trade_id: str,
    classification: str,
    *,
    entry_time: str,
    side: str = "long",
    symbol: str = "BTCUSDT",
    subclassification: str = "stop_loss_loss",
    severity: str = "high",
    evidence: list[str] | None = None,
) -> dict[str, object]:
    return {
        "trade_id": trade_id,
        "classification": classification,
        "subclassification": subclassification,
        "severity": severity,
        "confidence": 0.8,
        "evidence": evidence or ["fast_loss_under_30m", "stop_loss_loss"],
        "symbol": symbol,
        "side": side,
        "entry_time": entry_time,
        "uses_future_data": False,
        "uses_net_pnl_as_label": True,
        "uses_net_pnl_as_feature": False,
        "creates_candidate_rule": False,
        "operational_action_allowed": False,
    }


def feature_row(
    trade_id: str,
    *,
    rsi: float = 74.0,
    side: str = "long",
    symbol: str = "BTCUSDT",
) -> dict[str, object]:
    return {
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "rsi_14": rsi,
        "dist_sma_20_pct": 0.4,
        "lb_10m_ret_close": -0.01,
        "lb_30m_ret_close": -0.02,
        "pre_entry_volatility_20": 0.03,
    }


def sample_entries_for_pass() -> list[dict[str, object]]:
    return [
        catalog_entry("t1", "mistake", entry_time="2026-01-01T00:00:00Z"),
        catalog_entry("t2", "winner", entry_time="2026-01-02T00:00:00Z"),
        catalog_entry("t3", "mistake", entry_time="2026-01-03T00:00:00Z"),
        catalog_entry("t4", "mistake", entry_time="2026-01-04T00:00:00Z"),
        catalog_entry("t5", "mistake", entry_time="2026-01-05T00:00:00Z"),
    ]


def sample_features(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    return [feature_row(str(entry["trade_id"])) for entry in entries]


def test_report_with_none_inputs_uses_no_runtime_rows_loaded() -> None:
    payload = build_daily_shadow_rule_oos_validation_report(ROOT)

    assert payload["schema_version"] == DAILY_SHADOW_RULE_OOS_VALIDATION_SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["oos_validation"]["candidate_count"] == 0
    assert payload["write_performed"] is False
    assert payload["validation_errors"] == []


def test_empty_report_stays_blocked() -> None:
    payload = build_daily_shadow_rule_oos_validation_report(
        ROOT,
        candidate_rules=[],
        catalog_entries=[],
        feature_rows=[],
    )

    assert payload["status"] == "blocked"
    assert payload["input_mode"] == "in_memory_oos_validation_inputs"
    assert payload["oos_validation"]["validated_candidate_count"] == 0
    assert "no_candidate_rules_available" in payload["oos_validation"]["validation_quality_notes"]


def test_split_temporal_tail_and_insufficient_entries() -> None:
    entries = [
        catalog_entry("t3", "mistake", entry_time="2026-01-03T00:00:00Z"),
        catalog_entry("t1", "mistake", entry_time="2026-01-01T00:00:00Z"),
        catalog_entry("t2", "winner", entry_time="2026-01-02T00:00:00Z"),
        catalog_entry("t4", "mistake", entry_time="2026-01-04T00:00:00Z"),
    ]

    split = split_entries_for_oos(entries, oos_fraction=0.5)
    insufficient = split_entries_for_oos(entries[:1])

    assert split["split_status"] == "ok"
    assert split["in_sample_count"] == 2
    assert split["out_of_sample_count"] == 2
    assert [entry["trade_id"] for entry in split["out_of_sample_entries"]] == ["t3", "t4"]
    assert insufficient["split_status"] == "insufficient_entries"
    assert insufficient["out_of_sample_count"] == 0


def test_entry_matches_conditions_from_catalog_and_features() -> None:
    entry = catalog_entry("t1", "mistake", entry_time="2026-01-01T00:00:00Z")
    features = feature_row("t1")
    rule = candidate_rule(
        conditions=[
            "side_long",
            "symbol_BTCUSDT",
            "sub_stop_loss_loss",
            "severity_high",
            "rsi_high",
            "evidence_fast_loss_under_30m",
        ]
    )

    assert entry_matches_candidate_rule(rule, entry, features) is True


def test_target_matching_covers_requested_targets() -> None:
    mistake = catalog_entry("m", "mistake", entry_time="2026-01-01T00:00:00Z")
    winner = catalog_entry(
        "w",
        "winner",
        entry_time="2026-01-02T00:00:00Z",
        subclassification="profitable_trade",
        evidence=["positive_net_pnl_label"],
    )

    assert entry_matches_target("mistake", mistake) is True
    assert entry_matches_target("winner", winner) is True
    assert entry_matches_target("stop_loss_loss", mistake) is True
    assert entry_matches_target("fast_loss_under_30m", mistake) is True
    assert entry_matches_target("profitable_trade", winner) is True


def test_candidate_with_good_oos_passes_research_but_stays_blocked() -> None:
    entries = sample_entries_for_pass()
    features = {str(row["trade_id"]): row for row in sample_features(entries)}
    split = split_entries_for_oos(entries, oos_fraction=0.4)

    result = evaluate_candidate_rule_oos(
        candidate_rule(),
        split["in_sample_entries"],
        split["out_of_sample_entries"],
        features,
        min_oos_support_count=2,
        min_oos_confidence=0.5,
        max_confidence_degradation=0.25,
    )

    assert result["oos_status"] == "oos_research_pass"
    assert result["research_validation_passed"] is True
    assert result["promotion_status"] == "blocked"
    assert result["promotion_allowed"] is False
    assert result["operational_action_allowed"] is False
    assert result["applies_to_ai_shadow_runtime"] is False
    for blocker in MANDATORY_OOS_BLOCKERS:
        assert blocker in result["blockers"]


def test_insufficient_oos_support_and_low_confidence_failures() -> None:
    entries = sample_entries_for_pass()
    features = {str(row["trade_id"]): row for row in sample_features(entries)}
    split = split_entries_for_oos(entries, oos_fraction=0.4)

    insufficient = evaluate_candidate_rule_oos(
        candidate_rule(),
        split["in_sample_entries"],
        split["out_of_sample_entries"],
        features,
        min_oos_support_count=3,
    )
    low_confidence_entries = [
        catalog_entry("t1", "mistake", entry_time="2026-01-01T00:00:00Z"),
        catalog_entry("t2", "mistake", entry_time="2026-01-02T00:00:00Z"),
        catalog_entry("t3", "winner", entry_time="2026-01-03T00:00:00Z"),
        catalog_entry("t4", "winner", entry_time="2026-01-04T00:00:00Z"),
    ]
    low_features = {str(row["trade_id"]): row for row in sample_features(low_confidence_entries)}
    low_split = split_entries_for_oos(low_confidence_entries, oos_fraction=0.5)
    low_confidence = evaluate_candidate_rule_oos(
        candidate_rule(),
        low_split["in_sample_entries"],
        low_split["out_of_sample_entries"],
        low_features,
        min_oos_support_count=2,
        min_oos_confidence=0.75,
    )

    assert insufficient["oos_status"] == "insufficient_oos_support"
    assert low_confidence["oos_status"] == "oos_research_fail"


def test_confidence_degradation_and_lift_are_calculated() -> None:
    entries = [
        catalog_entry("t1", "mistake", entry_time="2026-01-01T00:00:00Z"),
        catalog_entry("t2", "mistake", entry_time="2026-01-02T00:00:00Z"),
        catalog_entry("t3", "mistake", entry_time="2026-01-03T00:00:00Z"),
        catalog_entry("t4", "winner", entry_time="2026-01-04T00:00:00Z"),
    ]
    features = {str(row["trade_id"]): row for row in sample_features(entries)}
    split = split_entries_for_oos(entries, oos_fraction=0.5)

    result = evaluate_candidate_rule_oos(
        candidate_rule(),
        split["in_sample_entries"],
        split["out_of_sample_entries"],
        features,
        min_oos_support_count=2,
        min_oos_confidence=0.1,
    )

    assert result["in_sample_confidence"] == 1.0
    assert result["out_of_sample_confidence"] == 0.5
    assert result["confidence_degradation"] == 0.5
    assert result["out_of_sample_lift"] == 1.0


def test_evaluate_candidate_rules_oos_summary_and_scope() -> None:
    entries = sample_entries_for_pass()
    result = evaluate_candidate_rules_oos(
        [candidate_rule()],
        entries,
        sample_features(entries),
        oos_fraction=0.4,
    )
    scope = result["validation_scope"]

    assert result["candidate_count"] == 1
    assert result["validated_candidate_count"] == 1
    assert result["oos_pass_count"] == 1
    assert len(result["validation_results_sample"]) <= 20
    assert scope["runs_oos_validation"] is True
    assert scope["research_oos_only"] is True
    assert scope["applies_candidate_rules"] is False
    assert scope["updates_ai_shadow_runtime"] is False
    assert scope["promotes_rules"] is False


def test_report_preserves_safety_flags_and_extra_branch_flags() -> None:
    entries = sample_entries_for_pass()
    payload = build_daily_shadow_rule_oos_validation_report(
        ROOT,
        candidate_rules=[candidate_rule()],
        catalog_entries=entries,
        feature_rows=sample_features(entries),
    )

    for key, expected in SAFETY_FLAGS.items():
        assert payload[key] is expected
    assert payload["runs_oos_validation"] is True
    assert payload["applies_shadow_rules"] is False
    assert payload["promotes_shadow_rules"] is False
    assert payload["readiness_policy"]["oos_validation_is_not_readiness_evidence"] is True
    assert payload["operator_decision"]["shadow_rule_promotion_allowed"] is False


def test_validate_report_accepts_valid_payload_and_rejects_relaxed_contracts() -> None:
    entries = sample_entries_for_pass()
    payload = build_daily_shadow_rule_oos_validation_report(
        ROOT,
        candidate_rules=[candidate_rule()],
        catalog_entries=entries,
        feature_rows=sample_features(entries),
    )
    assert validate_daily_shadow_rule_oos_validation_report(payload) == []

    mutated = dict(payload)
    mutated["research_only"] = False
    mutated["operational_authority"] = True
    mutated["writes_data"] = True
    mutated["promotes_shadow_rules"] = True
    mutated["validation_scope"] = dict(payload["validation_scope"])
    mutated["validation_scope"]["applies_candidate_rules"] = True
    mutated["validation_scope"]["updates_ai_shadow_runtime"] = True
    mutated["validation_scope"]["promotes_rules"] = True
    mutated["oos_validation"] = dict(payload["oos_validation"])
    mutated["oos_validation"]["validation_results"] = [
        dict(payload["oos_validation"]["validation_results"][0])
    ]
    mutated["oos_validation"]["validation_results"][0][
        "operational_action_allowed"
    ] = True
    mutated["oos_validation"]["validation_results"][0]["promotion_allowed"] = True

    errors = validate_daily_shadow_rule_oos_validation_report(mutated)
    assert "research_only_must_be_true" in errors
    assert "operational_authority_must_be_false" in errors
    assert "writes_data_must_be_false" in errors
    assert "promotes_shadow_rules_must_be_false" in errors
    assert "validation_scope_applies_candidate_rules_mismatch" in errors
    assert "validation_scope_updates_ai_shadow_runtime_mismatch" in errors
    assert "validation_scope_promotes_rules_mismatch" in errors
    assert "result_0_operational_action_allowed_must_be_false" in errors
    assert "result_0_promotion_allowed_must_be_false" in errors


def test_cli_no_write_json_returns_payload_without_file(tmp_path: Path) -> None:
    output = tmp_path / "oos.json"
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
    output = tmp_path / "oos.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(ROOT),
            "--output",
            str(output),
            "--oos-fraction",
            "0.4",
            "--min-oos-support-count",
            "3",
            "--min-oos-confidence",
            "0.7",
            "--max-confidence-degradation",
            "0.2",
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
    assert payload["oos_fraction"] == 0.4
    assert payload["min_oos_support_count"] == 3
    assert payload["min_oos_confidence"] == 0.7
    assert payload["max_confidence_degradation"] == 0.2
    assert disk_payload["write_performed"] is True


def test_cli_blocks_output_under_data_or_runtime() -> None:
    for restricted in ("data/reports/oos.json", "runtime/oos.json"):
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
