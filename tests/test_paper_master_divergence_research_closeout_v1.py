from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_master_divergence_research_closeout import (
    SAFETY_FLAGS,
    build_paper_master_divergence_research_closeout,
    validate_paper_master_divergence_research_closeout,
)


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/build_paper_master_divergence_research_closeout_v1.py"
MODULE = ROOT / "smartcrypto/research/paper_master_divergence_research_closeout.py"
DOC = ROOT / "docs/PAPER_MASTER_DIVERGENCE_RESEARCH_CLOSEOUT_V1.md"
TEST_FILE = ROOT / "tests/test_paper_master_divergence_research_closeout_v1.py"
NEW_FILES = (MODULE, CLI, DOC, TEST_FILE)


def test_payload_has_canonical_schema_status_and_decision() -> None:
    payload = build_paper_master_divergence_research_closeout()
    assert payload["schema_version"] == "paper_master_divergence_research_closeout_v1"
    assert payload["project_name"] == "SMART FUTUROS"
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["reason"] == "paper_does_not_replicate_legacy_dataset_edge"
    assert payload["research_only"] is True
    assert payload["operational_authority"] is False


def test_all_safety_flags_are_preserved() -> None:
    payload = build_paper_master_divergence_research_closeout()
    for key, expected in SAFETY_FLAGS.items():
        assert payload[key] is expected
    assert payload["paper_only"] is True
    assert payload["shadow_only"] is True
    assert payload["live_trading_enabled"] is False
    assert payload["order_submission_enabled"] is False
    assert payload["real_order_submission_enabled"] is False
    assert payload["exchange_private_access"] is False
    assert payload["sends_orders"] is False


def test_canonical_paper_vs_master_metrics_are_exact() -> None:
    metrics = build_paper_master_divergence_research_closeout()[
        "paper_vs_master_metrics"
    ]
    assert metrics["paper_trade_count"] == 239
    assert metrics["paper_net_pnl"] == -21.35477552
    assert metrics["paper_profit_factor"] == 0.8033314207
    assert metrics["paper_win_rate_pct"] == 40.5858
    assert metrics["paper_avg_duration_hours"] == 3.4234
    assert metrics["master_trade_count"] == 243
    assert metrics["master_net_pnl"] == 143.166332
    assert metrics["master_profit_factor"] == 2.0725730333
    assert metrics["master_win_rate_pct"] == 70.7819
    assert metrics["master_avg_duration_hours"] == 0.1937
    assert metrics["paper_minus_master_net_pnl"] == -164.52110752
    assert metrics["paper_minus_master_win_rate_pct_points"] == -30.1961
    assert metrics["conclusion"] == "paper_freqtrade_does_not_replicate_master_edge"


def test_root_cause_temporal_coverage_and_candidate_rule_metrics() -> None:
    payload = build_paper_master_divergence_research_closeout()
    root_cause = payload["root_cause_findings"]
    assert root_cause["roi_trade_count"] == 97
    assert root_cause["roi_net_pnl"] == 87.22777285
    assert root_cause["stop_loss_trade_count"] == 142
    assert root_cause["stop_loss_net_pnl"] == -108.58254837
    assert root_cause["remove_stop_loss_under_30m_simulated_net_pnl"] == 13.56136734
    assert root_cause["remove_stop_loss_under_30m_delta"] == 34.91614286

    temporal = payload["temporal_alignment_findings"]
    assert temporal["matches_15m"] == 13
    assert temporal["matches_30m"] == 31
    assert temporal["matches_60m"] == 42
    assert temporal["opposite_side_30m"] == 18
    assert temporal["opposite_side_60m"] == 28
    assert temporal["paper_stop_after_master_win_30m"] == 26
    assert temporal["paper_stop_after_master_win_60m"] == 40

    coverage = payload["coverage_findings"]
    assert coverage["paper_trades_total"] == 239
    assert coverage["entry_candle_covered_trades"] == 192
    assert coverage["entry_candle_covered_pct"] == 80.33
    assert coverage["entry_candle_uncovered_trades"] == 47
    assert coverage["entry_candle_uncovered_pct"] == 19.67
    assert coverage["full_feature_materialization_allowed"] is False
    assert coverage["partial_feature_materialization_allowed"] is True

    candidate = payload["candidate_shadow_rules_summary"]
    assert candidate["best_rule"]["lb_10m_ret_close_lte"] == -0.0038501215827868
    assert candidate["best_rule"]["lb_30m_ret_close_lte"] == -0.0060685748963285
    assert candidate["flagged_count"] == 32
    assert candidate["target_flagged"] == 21
    assert candidate["baseline_flagged"] == 11
    assert candidate["precision_pct"] == 65.625
    assert candidate["recall_pct"] == 41.176
    assert candidate["simulated_removed_pnl_delta"] == 8.9745
    assert candidate["can_review_candidate_shadow_rules"] is True
    assert candidate["can_promote_rules"] is False


def test_allowed_and_forbidden_next_steps_exist() -> None:
    payload = build_paper_master_divergence_research_closeout()
    assert "criar OOS validation" in payload["allowed_next_steps"]
    assert "alterar RiskManager" in payload["forbidden_next_steps"]
    assert "habilitar live" in payload["forbidden_next_steps"]
    assert "enviar ordem real" in payload["forbidden_next_steps"]


def test_validation_returns_empty_list_for_valid_payload() -> None:
    payload = build_paper_master_divergence_research_closeout()
    assert validate_paper_master_divergence_research_closeout(payload) == []
    assert payload["validation_errors"] == []


def test_validation_fails_when_critical_safety_flag_is_relaxed() -> None:
    payload = build_paper_master_divergence_research_closeout()
    payload["order_submission_enabled"] = True
    payload["live_trading_enabled"] = True
    errors = validate_paper_master_divergence_research_closeout(payload)
    assert "order_submission_enabled_must_be_false" in errors
    assert "live_trading_enabled_must_be_false" in errors


def test_cli_json_no_write_returns_valid_payload_without_file(tmp_path: Path) -> None:
    output = tmp_path / "closeout.json"
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
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["research_only"] is True
    assert payload["operational_authority"] is False
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert not output.exists()


def test_cli_output_in_temp_writes_valid_file(tmp_path: Path) -> None:
    output = tmp_path / "closeout.json"
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
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    stdout_payload = json.loads(completed.stdout)
    file_payload = json.loads(output.read_text(encoding="utf-8"))
    assert stdout_payload["write_requested"] is True
    assert stdout_payload["write_performed"] is True
    assert file_payload["write_performed"] is True
    assert file_payload["validation_errors"] == []


def test_cli_blocks_output_under_project_data(tmp_path: Path) -> None:
    blocked_output = ROOT / "data" / "reports" / "paper_master_closeout.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(ROOT),
            "--output",
            str(blocked_output),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["status"] == "blocked"
    assert payload["reason"] == "output_path_in_runtime_or_data_scope"
    assert payload["write_performed"] is False
    assert not blocked_output.exists()


def test_new_files_do_not_contain_forbidden_operational_tokens() -> None:
    forbidden = (
        "request" + "s",
        "http" + "x",
        "aio" + "http",
        "cc" + "xt",
        "sqlite3" + ".connect",
        "pandas" + ".read_",
        "to_" + "parquet",
        "to_" + "excel",
        "to_" + "sql",
        "create_" + "order",
        "cancel_" + "order",
        "fetch_" + "balance",
        "send_" + "order",
        "TELE" + "GRAM",
        "NT" + "FY",
        "BINANCE" + "_SECRET",
        "BINANCE" + "_API_KEY",
    )
    for path in NEW_FILES:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for token in forbidden:
            assert token.lower() not in lowered, path


def test_no_runtime_outputs_are_versioned() -> None:
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--",
            "data",
            "runtime",
            "reports",
            "*.sqlite",
            "*.parquet",
            "*.xlsx",
            "*.csv",
            "*.zip",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert tracked.stdout.strip() == ""


def test_current_dirty_files_are_limited_to_branch_scope() -> None:
    completed = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = {
        line[3:].strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    }
    assert paths <= {
        "PROJECT_MANIFEST_CLEAN.json",
        "smartcrypto/research/paper_master_divergence_research_closeout.py",
        "scripts/build_paper_master_divergence_research_closeout_v1.py",
        "tests/test_paper_master_divergence_research_closeout_v1.py",
        "docs/PAPER_MASTER_DIVERGENCE_RESEARCH_CLOSEOUT_V1.md",
    }
