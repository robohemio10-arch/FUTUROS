from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_mistake_winner_catalog import (
    CATALOG_SCOPE,
    DAILY_MISTAKE_WINNER_CATALOG_SCHEMA_VERSION,
    build_daily_mistake_winner_catalog_report,
    build_mistake_winner_catalog,
    classify_trade_outcome,
    summarize_catalog,
    validate_daily_mistake_winner_catalog_report,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "smartcrypto/research/daily_mistake_winner_catalog.py"
CLI = ROOT / "scripts/build_daily_mistake_winner_catalog_v1.py"
DOC = ROOT / "docs/DAILY_MISTAKE_WINNER_CATALOG_V1.md"
TEST_FILE = ROOT / "tests/test_daily_mistake_winner_catalog_v1.py"
NEW_FILES = (MODULE, CLI, DOC, TEST_FILE)


def winner_trade() -> dict[str, object]:
    return {
        "trade_id": "w1",
        "symbol": "BTCUSDT",
        "side": "long",
        "net_pnl": 10.0,
        "duration_minutes": 45,
        "exit_reason": "roi",
    }


def loss_trade() -> dict[str, object]:
    return {
        "trade_id": "m1",
        "symbol": "ETHUSDT",
        "side": "short",
        "net_pnl": -7.5,
        "duration_minutes": 18,
        "exit_reason": "stop_loss",
    }


def loss_feature_row() -> dict[str, object]:
    return {
        "trade_id": "m1",
        "rsi_14": 74.0,
        "lb_10m_ret_close": -0.01,
        "lb_30m_ret_close": -0.02,
    }


def test_report_is_blocked_without_loading_runtime_rows() -> None:
    payload = build_daily_mistake_winner_catalog_report(ROOT)

    assert payload["schema_version"] == DAILY_MISTAKE_WINNER_CATALOG_SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["catalog"]["entry_count"] == 0
    assert payload["write_performed"] is False
    assert payload["validation_errors"] == []


def test_report_preserves_all_paper_shadow_safety_flags() -> None:
    payload = build_daily_mistake_winner_catalog_report(ROOT, trades=[])

    for key, expected in SAFETY_FLAGS.items():
        assert payload[key] is expected
    assert payload["operator_decision"]["live_release_allowed"] is False
    assert payload["operator_decision"]["shadow_rule_promotion_allowed"] is False


def test_classifies_winner_with_positive_label_and_momentum_evidence() -> None:
    entry = classify_trade_outcome(
        winner_trade(),
        {
            "trade_id": "w1",
            "lb_5m_ret_close": 0.01,
            "lb_10m_ret_close": 0.02,
        },
    )

    assert entry["classification"] == "winner"
    assert entry["subclassification"] == "profitable_trade"
    assert entry["severity"] == "none"
    assert "positive_net_pnl_label" in entry["evidence"]
    assert "positive_pre_entry_momentum" in entry["evidence"]


def test_classifies_stop_loss_mistake_with_contextual_evidence() -> None:
    entry = classify_trade_outcome(loss_trade(), loss_feature_row())

    assert entry["classification"] == "mistake"
    assert entry["subclassification"] == "stop_loss_loss"
    assert entry["severity"] == "high"
    assert "negative_net_pnl_label" in entry["evidence"]
    assert "stop_exit_reason" in entry["evidence"]
    assert "fast_loss_under_30m" in entry["evidence"]
    assert "overextended_entry_rsi" in entry["evidence"]
    assert "weak_pre_entry_momentum" in entry["evidence"]


def test_classifies_flat_and_missing_pnl_without_operational_action() -> None:
    flat = classify_trade_outcome({"trade_id": "f1", "net_pnl": 0.0})
    missing = classify_trade_outcome({"trade_id": "x1"})

    assert flat["classification"] == "neutral"
    assert flat["subclassification"] == "flat_trade"
    assert missing["classification"] == "insufficient_evidence"
    assert missing["subclassification"] == "missing_pnl"
    for entry in (flat, missing):
        assert entry["uses_future_data"] is False
        assert entry["uses_net_pnl_as_label"] is True
        assert entry["uses_net_pnl_as_feature"] is False
        assert entry["creates_candidate_rule"] is False
        assert entry["operational_action_allowed"] is False


def test_catalog_counts_symbol_side_summary_and_sample_limit() -> None:
    trades: list[dict[str, object]] = [
        winner_trade(),
        loss_trade(),
        {"trade_id": "n1", "symbol": "BTCUSDT", "side": "buy", "net_pnl": 0.0},
        {"trade_id": "u1", "symbol": "ETH/USDT", "side": "sell"},
    ]
    trades.extend(
        {
            "trade_id": f"extra_{index}",
            "symbol": "BTCUSDT",
            "side": "long",
            "net_pnl": 1.0,
        }
        for index in range(25)
    )

    catalog = build_mistake_winner_catalog(trades, [loss_feature_row()])

    assert catalog["entry_count"] == 29
    assert catalog["winner_count"] == 26
    assert catalog["mistake_count"] == 1
    assert catalog["neutral_count"] == 1
    assert catalog["insufficient_evidence_count"] == 1
    assert len(catalog["catalog_entries_sample"]) == 20
    assert catalog["symbol_summary"]["BTCUSDT"]["winner"] == 26
    assert catalog["symbol_summary"]["BTCUSDT"]["neutral"] == 1
    assert catalog["side_summary"]["long"]["winner"] == 26
    assert catalog["side_summary"]["short"]["mistake"] == 1
    assert catalog["catalog_scope"] == CATALOG_SCOPE


def test_catalog_keeps_alignment_evidence_read_only() -> None:
    catalog = build_mistake_winner_catalog(
        [winner_trade()],
        alignment_summary={"evidence_by_trade_id": {"w1": ["matched_to_entry_candle"]}},
    )

    entry = catalog["catalog_entries"][0]
    assert entry["classification"] == "winner"
    assert "matched_to_entry_candle" in entry["evidence"]
    assert entry["operational_action_allowed"] is False


def test_summarize_catalog_is_deterministic() -> None:
    entries = [
        {"classification": "winner", "subclassification": "a", "severity": "none"},
        {"classification": "winner", "subclassification": "a", "severity": "none"},
        {"classification": "mistake", "subclassification": "b", "severity": "high"},
    ]

    summary = summarize_catalog(entries)

    assert summary["entry_count"] == 3
    assert summary["classification_counts"] == {"mistake": 1, "winner": 2}
    assert summary["subclassification_counts"] == {"a": 2, "b": 1}
    assert summary["severity_counts"] == {"high": 1, "none": 2}


def test_validation_rejects_any_scope_or_safety_relaxation() -> None:
    payload = build_daily_mistake_winner_catalog_report(ROOT, trades=[winner_trade()])

    assert validate_daily_mistake_winner_catalog_report(payload) == []

    mutated = dict(payload)
    mutated["research_only"] = False
    mutated["operational_authority"] = True
    mutated["writes_data"] = True
    mutated["catalog_scope"] = dict(payload["catalog_scope"])
    mutated["catalog_scope"]["registers_candidate_rules"] = True
    mutated["catalog_scope"]["mines_patterns"] = True
    mutated["catalog_scope"]["uses_net_pnl_as_feature"] = True

    errors = validate_daily_mistake_winner_catalog_report(mutated)
    assert "research_only_must_be_true" in errors
    assert "operational_authority_must_be_false" in errors
    assert "writes_data_must_be_false" in errors
    assert "catalog_scope_registers_candidate_rules_mismatch" in errors
    assert "catalog_scope_mines_patterns_mismatch" in errors
    assert "catalog_scope_uses_net_pnl_as_feature_mismatch" in errors


def test_scope_blocks_pattern_mining_rule_registry_and_oos_validation() -> None:
    payload = build_daily_mistake_winner_catalog_report(ROOT, trades=[])
    scope = payload["catalog_scope"]

    assert scope["mines_patterns"] is False
    assert scope["registers_candidate_rules"] is False
    assert scope["runs_oos_validation"] is False
    assert scope["updates_models"] is False
    assert scope["updates_risk"] is False
    assert scope["updates_execution"] is False
    assert "criar pattern mining research em branch futura" in payload["allowed_next_steps"]
    assert "promover regra candidata" in payload["forbidden_actions"]


def test_cli_no_write_json_does_not_create_output(tmp_path: Path) -> None:
    output = tmp_path / "catalog.json"

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
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert payload["cli_reason"] == "no_write_default"
    assert not output.exists()


def test_cli_writes_only_to_explicit_safe_output(tmp_path: Path) -> None:
    output = tmp_path / "catalog.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(ROOT),
            "--output",
            str(output),
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
    assert payload["cli_reason"] == "explicit_output_written"
    assert disk_payload["write_performed"] is True


def test_cli_blocks_output_under_runtime_data_scope() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(ROOT),
            "--output",
            str(ROOT / "data/reports/daily_mistake_winner_catalog_v1.json"),
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
    assert not (ROOT / "data/reports/daily_mistake_winner_catalog_v1.json").exists()


def test_new_files_do_not_import_runtime_or_private_exchange_modules() -> None:
    forbidden = (
        "cc" + "xt",
        "import " + "freqtrade",
        "from " + "freqtrade",
        "import " + "risk",
        "from " + "smartcrypto.risk",
        "request" + "s.",
        "import " + "requests",
        "from " + "requests",
        "http" + "x",
        "aio" + "http",
        "to_" + "parquet",
        "read_" + "parquet",
        "read_" + "excel",
        "send_" + "order",
        "submit_" + "order",
    )
    for path in NEW_FILES:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text
