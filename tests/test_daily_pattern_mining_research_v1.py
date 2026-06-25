from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_pattern_mining_research import (
    DAILY_PATTERN_MINING_RESEARCH_SCHEMA_VERSION,
    build_daily_pattern_mining_research_report,
    build_feature_bins,
    mine_descriptive_patterns,
    score_pattern,
    validate_daily_pattern_mining_research_report,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "smartcrypto/research/daily_pattern_mining_research.py"
CLI = ROOT / "scripts/build_daily_pattern_mining_research_v1.py"
DOC = ROOT / "docs/DAILY_PATTERN_MINING_RESEARCH_V1.md"
TEST_FILE = ROOT / "tests/test_daily_pattern_mining_research_v1.py"
NEW_FILES = (MODULE, CLI, DOC, TEST_FILE)


def sample_catalog_entries() -> list[dict[str, object]]:
    return [
        {
            "trade_id": "m1",
            "classification": "mistake",
            "subclassification": "stop_loss_loss",
            "severity": "high",
            "confidence": 0.8,
            "evidence": ["fast_loss_under_30m", "stop_exit_reason"],
            "symbol": "BTCUSDT",
            "side": "long",
            "uses_future_data": False,
            "uses_net_pnl_as_label": True,
            "uses_net_pnl_as_feature": False,
            "creates_candidate_rule": False,
            "operational_action_allowed": False,
        },
        {
            "trade_id": "m2",
            "classification": "mistake",
            "subclassification": "stop_loss_loss",
            "severity": "high",
            "confidence": 0.8,
            "evidence": ["fast_loss_under_30m", "stop_exit_reason"],
            "symbol": "BTCUSDT",
            "side": "long",
            "uses_future_data": False,
            "uses_net_pnl_as_label": True,
            "uses_net_pnl_as_feature": False,
            "creates_candidate_rule": False,
            "operational_action_allowed": False,
        },
        {
            "trade_id": "w1",
            "classification": "winner",
            "subclassification": "profitable_trade",
            "severity": "none",
            "confidence": 0.8,
            "evidence": ["positive_net_pnl_label"],
            "symbol": "ETHUSDT",
            "side": "short",
            "uses_future_data": False,
            "uses_net_pnl_as_label": True,
            "uses_net_pnl_as_feature": False,
            "creates_candidate_rule": False,
            "operational_action_allowed": False,
        },
        {
            "trade_id": "w2",
            "classification": "winner",
            "subclassification": "profitable_trade",
            "severity": "none",
            "confidence": 0.8,
            "evidence": ["positive_net_pnl_label"],
            "symbol": "ETHUSDT",
            "side": "short",
            "uses_future_data": False,
            "uses_net_pnl_as_label": True,
            "uses_net_pnl_as_feature": False,
            "creates_candidate_rule": False,
            "operational_action_allowed": False,
        },
    ]


def sample_feature_rows() -> list[dict[str, object]]:
    return [
        {
            "trade_id": "m1",
            "symbol": "BTCUSDT",
            "side": "long",
            "rsi_14": 74.0,
            "dist_sma_20_pct": 0.5,
            "pre_entry_volatility_20": 0.03,
            "lb_10m_ret_close": -0.01,
            "lb_30m_ret_close": -0.02,
        },
        {
            "trade_id": "m2",
            "symbol": "BTCUSDT",
            "side": "long",
            "rsi_14": 76.0,
            "dist_sma_20_pct": 0.7,
            "pre_entry_volatility_20": 0.04,
            "lb_10m_ret_close": -0.015,
            "lb_30m_ret_close": -0.025,
        },
        {
            "trade_id": "w1",
            "symbol": "ETHUSDT",
            "side": "short",
            "rsi_14": 44.0,
            "dist_sma_20_pct": -0.5,
            "pre_entry_volatility_20": 0.003,
            "lb_10m_ret_close": 0.012,
            "lb_30m_ret_close": 0.018,
        },
        {
            "trade_id": "w2",
            "symbol": "ETHUSDT",
            "side": "short",
            "rsi_14": 48.0,
            "dist_sma_20_pct": -0.4,
            "pre_entry_volatility_20": 0.004,
            "lb_10m_ret_close": 0.011,
            "lb_30m_ret_close": 0.014,
        },
    ]


def pattern_by_condition(
    patterns: list[dict[str, object]],
    target: str,
    *conditions: str,
) -> dict[str, object]:
    expected = list(conditions)
    for pattern in patterns:
        if pattern["target"] == target and pattern["conditions"] == expected:
            return pattern
    raise AssertionError(f"pattern not found: {target} {expected}")


def test_report_with_none_inputs_uses_no_runtime_rows_loaded() -> None:
    payload = build_daily_pattern_mining_research_report(ROOT)

    assert payload["schema_version"] == DAILY_PATTERN_MINING_RESEARCH_SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["reason"] == "pattern_mining_research_only_without_operational_authority"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["pattern_mining"]["entry_count"] == 0
    assert payload["write_performed"] is False
    assert payload["validation_errors"] == []


def test_empty_in_memory_report_stays_blocked() -> None:
    payload = build_daily_pattern_mining_research_report(
        ROOT,
        catalog_entries=[],
        feature_rows=[],
    )

    assert payload["status"] == "blocked"
    assert payload["input_mode"] == "in_memory_pattern_inputs"
    assert payload["pattern_mining"]["pattern_count"] == 0
    assert "no_entries_available" in payload["pattern_mining"]["pattern_quality_notes"]


def test_build_feature_bins_covers_requested_buckets() -> None:
    bins = build_feature_bins(
        {
            "symbol": "BTC/USDT",
            "side": "buy",
            "rsi_14": 75.0,
            "dist_sma_20_pct": 0.3,
            "pre_entry_volatility_20": 0.03,
            "lb_10m_ret_close": -0.01,
            "lb_30m_ret_close": -0.02,
        }
    )

    assert bins["rsi_14"] == "rsi_high"
    assert bins["dist_sma_20_pct"] == "above_sma"
    assert bins["pre_entry_volatility_20"] == "vol_high"
    assert bins["lb_10m_ret_close"] == "lb_10m_negative"
    assert bins["lb_30m_ret_close"] == "lb_30m_negative"
    assert bins["side"] == "side_long"
    assert bins["symbol"] == "symbol_BTCUSDT"


def test_single_pair_and_winner_patterns_are_mined() -> None:
    result = mine_descriptive_patterns(
        sample_catalog_entries(),
        sample_feature_rows(),
        min_support_count=2,
        min_confidence=0.5,
    )
    patterns = result["patterns"]

    rsi_pattern = pattern_by_condition(patterns, "mistake", "rsi_high")
    pair_pattern = pattern_by_condition(
        patterns,
        "mistake",
        "lb_10m_negative",
        "rsi_high",
    )
    winner_pattern = pattern_by_condition(patterns, "winner", "lb_10m_positive")

    assert rsi_pattern["pattern_type"] == "single_bucket"
    assert pair_pattern["pattern_type"] == "bucket_pair"
    assert winner_pattern["target"] == "winner"
    assert rsi_pattern["support_count"] == 2
    assert rsi_pattern["target_count"] == 2
    assert rsi_pattern["confidence"] == 1.0
    assert rsi_pattern["baseline_rate"] == 0.5
    assert rsi_pattern["lift"] == 2.0
    assert rsi_pattern["coverage_pct"] == 50.0


def test_classification_concentration_pattern_is_descriptive_only() -> None:
    result = mine_descriptive_patterns(
        sample_catalog_entries(),
        sample_feature_rows(),
    )
    pattern = pattern_by_condition(result["patterns"], "mistake", "sub_stop_loss_loss")

    assert pattern["pattern_type"] == "classification_concentration"
    assert pattern["creates_candidate_rule"] is False
    assert pattern["operational_action_allowed"] is False
    assert pattern["requires_oos_validation"] is True
    assert pattern["promotion_allowed"] is False


def test_min_support_and_confidence_filters_are_applied() -> None:
    high_support = mine_descriptive_patterns(
        sample_catalog_entries(),
        sample_feature_rows(),
        min_support_count=5,
    )
    assert high_support["pattern_count"] == 0

    mixed_entries = [
        {"trade_id": "a", "classification": "mistake", "side": "long"},
        {"trade_id": "b", "classification": "mistake", "side": "long"},
        {"trade_id": "c", "classification": "winner", "side": "long"},
        {"trade_id": "d", "classification": "winner", "side": "long"},
    ]
    mixed_features = [
        {"trade_id": "a", "side": "long"},
        {"trade_id": "b", "side": "long"},
        {"trade_id": "c", "side": "long"},
        {"trade_id": "d", "side": "long"},
    ]
    high_confidence = mine_descriptive_patterns(
        mixed_entries,
        mixed_features,
        min_support_count=2,
        min_confidence=0.75,
    )
    assert high_confidence["pattern_count"] == 0


def test_score_pattern_calculates_metrics() -> None:
    scored = score_pattern(
        {
            "pattern_type": "single_bucket",
            "target": "mistake",
            "conditions": ["rsi_high"],
            "support_count": 4,
            "target_count": 3,
            "examples_sample": ["a", "b", "c"],
        },
        total_count=10,
        target_count=5,
    )

    assert scored["non_target_count"] == 1
    assert scored["confidence"] == 0.75
    assert scored["baseline_rate"] == 0.5
    assert scored["lift"] == 1.5
    assert scored["coverage_pct"] == 40.0
    assert scored["examples_sample"] == ["a", "b", "c"]


def test_examples_sample_and_patterns_sample_are_limited() -> None:
    entries = [
        {
            "trade_id": f"m{index}",
            "classification": "mistake",
            "subclassification": "stop_loss_loss",
            "severity": "high",
            "symbol": "BTCUSDT",
            "side": "long",
            "evidence": ["fast_loss_under_30m"],
        }
        for index in range(30)
    ]
    features = [
        {
            "trade_id": f"m{index}",
            "symbol": "BTCUSDT",
            "side": "long",
            "rsi_14": 72,
            "lb_10m_ret_close": -0.01,
            "lb_30m_ret_close": -0.02,
        }
        for index in range(30)
    ]

    result = mine_descriptive_patterns(entries, features, min_support_count=2)

    assert len(result["patterns_sample"]) <= 20
    assert all(len(pattern["examples_sample"]) <= 5 for pattern in result["patterns"])


def test_report_scope_readiness_and_safety_flags_are_preserved() -> None:
    payload = build_daily_pattern_mining_research_report(
        ROOT,
        catalog_entries=sample_catalog_entries(),
        feature_rows=sample_feature_rows(),
    )
    scope = payload["pattern_scope"]

    for key, expected in SAFETY_FLAGS.items():
        assert payload[key] is expected
    assert scope["mines_patterns"] is True
    assert scope["creates_candidate_rules"] is False
    assert scope["registers_candidate_rules"] is False
    assert scope["runs_oos_validation"] is False
    assert scope["uses_net_pnl_as_feature"] is False
    assert payload["readiness_policy"]["pattern_mining_is_not_readiness_evidence"] is True
    assert payload["operator_decision"]["live_release_allowed"] is False


def test_validate_report_accepts_valid_payload_and_rejects_relaxed_flags() -> None:
    payload = build_daily_pattern_mining_research_report(
        ROOT,
        catalog_entries=sample_catalog_entries(),
        feature_rows=sample_feature_rows(),
    )
    assert validate_daily_pattern_mining_research_report(payload) == []

    mutated = dict(payload)
    mutated["research_only"] = False
    mutated["operational_authority"] = True
    mutated["writes_data"] = True
    mutated["pattern_scope"] = dict(payload["pattern_scope"])
    mutated["pattern_scope"]["creates_candidate_rules"] = True
    mutated["pattern_scope"]["registers_candidate_rules"] = True
    mutated["pattern_scope"]["runs_oos_validation"] = True
    mutated["pattern_mining"] = dict(payload["pattern_mining"])
    mutated["pattern_mining"]["patterns"] = [
        dict(payload["pattern_mining"]["patterns"][0])
    ]
    mutated["pattern_mining"]["patterns"][0]["operational_action_allowed"] = True

    errors = validate_daily_pattern_mining_research_report(mutated)
    assert "research_only_must_be_true" in errors
    assert "operational_authority_must_be_false" in errors
    assert "writes_data_must_be_false" in errors
    assert "pattern_scope_creates_candidate_rules_mismatch" in errors
    assert "pattern_scope_registers_candidate_rules_mismatch" in errors
    assert "pattern_scope_runs_oos_validation_mismatch" in errors
    assert "pattern_0_operational_action_allowed_must_be_false" in errors


def test_cli_no_write_json_returns_payload_without_file(tmp_path: Path) -> None:
    output = tmp_path / "pattern.json"
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
    output = tmp_path / "pattern.json"
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
    assert disk_payload["write_performed"] is True


def test_cli_blocks_output_under_data_or_runtime() -> None:
    for restricted in ("data/reports/pattern.json", "runtime/pattern.json"):
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
