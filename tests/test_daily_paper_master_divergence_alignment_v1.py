from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_paper_master_divergence_alignment import (
    DAILY_PAPER_MASTER_DIVERGENCE_ALIGNMENT_SCHEMA_VERSION,
    DEFAULT_ALIGNMENT_WINDOWS_MINUTES,
    build_daily_paper_master_divergence_alignment_report,
    calculate_aggregate_divergence,
    calculate_temporal_alignment,
    normalize_trade_for_alignment,
    validate_daily_paper_master_divergence_alignment_report,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "smartcrypto/research/daily_paper_master_divergence_alignment.py"
CLI = ROOT / "scripts/build_daily_paper_master_divergence_alignment_v1.py"
DOC = ROOT / "docs/DAILY_PAPER_MASTER_DIVERGENCE_ALIGNMENT_V1.md"
TEST_FILE = ROOT / "tests/test_daily_paper_master_divergence_alignment_v1.py"
NEW_FILES = (MODULE, CLI, DOC, TEST_FILE)


def paper_trades() -> list[dict[str, object]]:
    return [
        {
            "trade_id": "p1",
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time": "2026-06-01T00:10:00Z",
            "close_time": "2026-06-01T00:20:00Z",
            "net_pnl": -5.0,
            "exit_reason": "stop_loss",
            "duration_minutes": 10,
        },
        {
            "trade_id": "p2",
            "symbol": "ETHUSDT",
            "side": "sell",
            "open_time": "2026-06-01T01:25:00Z",
            "close_time": "2026-06-01T01:40:00Z",
            "net_pnl": 2.0,
            "exit_reason": "roi",
            "duration_minutes": 15,
        },
        {
            "trade_id": "p3",
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time": "2026-06-01T05:00:00Z",
            "close_time": "2026-06-01T05:20:00Z",
            "net_pnl": -3.0,
            "exit_reason": "stop_loss",
            "duration_minutes": 20,
        },
    ]


def master_trades() -> list[dict[str, object]]:
    return [
        {
            "trade_id": "m1",
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time": "2026-06-01T00:00:00Z",
            "close_time": "2026-06-01T00:05:00Z",
            "net_pnl": 8.0,
            "exit_reason": "roi",
            "duration_minutes": 5,
        },
        {
            "trade_id": "m2",
            "symbol": "ETHUSDT",
            "side": "long",
            "open_time": "2026-06-01T01:00:00Z",
            "close_time": "2026-06-01T01:10:00Z",
            "net_pnl": 4.0,
            "exit_reason": "roi",
            "duration_minutes": 10,
        },
        {
            "trade_id": "m3",
            "symbol": "ETHUSDT",
            "side": "short",
            "open_time": "2026-06-01T08:00:00Z",
            "close_time": "2026-06-01T08:10:00Z",
            "net_pnl": 7.0,
            "exit_reason": "roi",
            "duration_minutes": 10,
        },
    ]


def test_report_with_empty_lists_is_blocked() -> None:
    report = build_daily_paper_master_divergence_alignment_report(
        ROOT,
        paper_trades=[],
        master_trades=[],
    )
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["aggregate_divergence"]["paper_kpis"]["trade_count"] == 0
    assert report["temporal_alignment"]["paper_trade_count"] == 0


def test_input_mode_is_no_runtime_rows_loaded_when_inputs_are_none() -> None:
    report = build_daily_paper_master_divergence_alignment_report(ROOT)
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["write_performed"] is False


def test_aggregate_divergence_paper_worse_than_master() -> None:
    result = calculate_aggregate_divergence(
        [{"net_pnl": -5.0}, {"net_pnl": 1.0}],
        [{"net_pnl": 4.0}, {"net_pnl": 2.0}],
    )
    assert result["net_pnl_delta"] == -10
    assert result["trade_count_delta"] == 0
    assert result["comparison_scope"] == "aggregate_divergence_only"


def test_aggregate_divergence_paper_better_than_master() -> None:
    result = calculate_aggregate_divergence(
        [{"net_pnl": 10.0}, {"net_pnl": -1.0}],
        [{"net_pnl": 3.0}, {"net_pnl": -2.0}],
    )
    assert result["net_pnl_delta"] == 8
    assert result["gross_profit_delta"] == 7
    assert result["gross_loss_abs_delta"] == -1


def test_temporal_alignment_match_inside_15_minutes() -> None:
    result = calculate_temporal_alignment(paper_trades(), master_trades(), (15,))
    window = result["windows"][0]
    assert window["window_minutes"] == 15
    assert window["matched_count"] == 1
    assert window["same_side_match_count"] == 1
    assert window["paper_stop_after_master_win_count"] == 1
    assert window["paper_entry_after_master_exit_count"] == 1


def test_temporal_alignment_match_only_in_30_and_60_minutes() -> None:
    result = calculate_temporal_alignment(paper_trades(), master_trades())
    by_window = {item["window_minutes"]: item for item in result["windows"]}
    assert by_window[15]["matched_count"] == 1
    assert by_window[30]["matched_count"] == 2
    assert by_window[60]["matched_count"] == 2
    assert by_window[30]["opposite_side_match_count"] == 1


def test_same_side_and_opposite_side_counts() -> None:
    window = calculate_temporal_alignment(paper_trades(), master_trades(), (30,))[
        "windows"
    ][0]
    assert window["same_side_match_count"] == 1
    assert window["opposite_side_match_count"] == 1
    assert window["opposite_side_rate_pct"] == 50.0


def test_paper_stop_after_master_win() -> None:
    window = calculate_temporal_alignment(paper_trades(), master_trades(), (15,))[
        "windows"
    ][0]
    assert window["paper_stop_after_master_win_count"] == 1
    sample = window["matched_pairs_sample"][0]
    assert sample["paper_stop_after_master_win"] is True


def test_paper_entry_after_master_exit() -> None:
    window = calculate_temporal_alignment(paper_trades(), master_trades(), (15,))[
        "windows"
    ][0]
    assert window["paper_entry_after_master_exit_count"] == 1
    assert window["matched_pairs_sample"][0]["paper_entry_after_master_exit"] is True


def test_master_winner_missed_and_paper_loser_without_master_match() -> None:
    window = calculate_temporal_alignment(paper_trades(), master_trades(), (15,))[
        "windows"
    ][0]
    assert window["master_winner_missed_count"] == 2
    assert window["paper_loser_without_master_match_count"] == 1


def test_default_windows_are_present() -> None:
    report = build_daily_paper_master_divergence_alignment_report(
        ROOT,
        paper_trades=paper_trades(),
        master_trades=master_trades(),
    )
    assert tuple(report["alignment_windows_minutes"]) == DEFAULT_ALIGNMENT_WINDOWS_MINUTES
    assert [item["window_minutes"] for item in report["temporal_alignment"]["windows"]] == [
        15,
        30,
        60,
    ]


def test_matched_pairs_sample_is_limited_and_not_massive() -> None:
    many_paper = [
        {
            "trade_id": f"p{i}",
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time": f"2026-06-01T00:{i:02d}:00Z",
            "net_pnl": 1.0,
        }
        for i in range(25)
    ]
    many_master = [
        {
            "trade_id": f"m{i}",
            "symbol": "BTCUSDT",
            "side": "long",
            "open_time": f"2026-06-01T00:{i:02d}:00Z",
            "net_pnl": 1.0,
        }
        for i in range(25)
    ]
    window = calculate_temporal_alignment(many_paper, many_master, (1,))["windows"][0]
    assert window["matched_count"] == 25
    assert len(window["matched_pairs_sample"]) == 20


def test_normalize_trade_for_alignment_handles_aliases() -> None:
    normalized = normalize_trade_for_alignment(
        {
            "trade_id": "x",
            "symbol": "eth/usdt",
            "side": "buy",
            "entry_time": "2026-06-01T00:00:00Z",
            "exit_time": "2026-06-01T00:10:00Z",
            "net_pnl": "1.5",
        },
        "paper",
        0,
    )
    assert normalized["symbol"] == "ETHUSDT"
    assert normalized["side"] == "long"
    assert normalized["valid_for_alignment"] is True


def test_report_schema_status_and_decision() -> None:
    report = build_daily_paper_master_divergence_alignment_report(ROOT)
    assert report["schema_version"] == (
        DAILY_PAPER_MASTER_DIVERGENCE_ALIGNMENT_SCHEMA_VERSION
    )
    assert report["project_name"] == "SMART FUTUROS"
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["reason"] == (
        "divergence_alignment_research_only_without_operational_authority"
    )


def test_safety_flags_are_preserved() -> None:
    report = build_daily_paper_master_divergence_alignment_report(ROOT)
    for key, expected in SAFETY_FLAGS.items():
        assert report[key] is expected
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["operational_authority"] is False
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    assert report["sends_orders"] is False


def test_alignment_scope_does_not_create_features_or_rules() -> None:
    scope = build_daily_paper_master_divergence_alignment_report(ROOT)[
        "alignment_scope"
    ]
    assert scope["computes_aggregate_divergence"] is True
    assert scope["computes_temporal_alignment"] is True
    assert scope["loads_runtime_trade_rows"] is False
    assert scope["loads_excel_rows"] is False
    assert scope["loads_sqlite_rows"] is False
    assert scope["loads_candle_rows"] is False
    assert scope["computes_candle_coverage"] is False
    assert scope["computes_entry_features"] is False
    assert scope["mines_patterns"] is False
    assert scope["registers_candidate_rules"] is False


def test_validate_returns_empty_list_for_valid_payload() -> None:
    report = build_daily_paper_master_divergence_alignment_report(ROOT)
    assert validate_daily_paper_master_divergence_alignment_report(report) == []
    assert report["validation_errors"] == []


def test_validation_fails_if_research_only_false() -> None:
    report = build_daily_paper_master_divergence_alignment_report(ROOT)
    report["research_only"] = False
    errors = validate_daily_paper_master_divergence_alignment_report(report)
    assert "research_only_must_be_true" in errors


def test_validation_fails_if_operational_authority_true() -> None:
    report = build_daily_paper_master_divergence_alignment_report(ROOT)
    report["operational_authority"] = True
    errors = validate_daily_paper_master_divergence_alignment_report(report)
    assert "operational_authority_must_be_false" in errors


def test_validation_fails_if_writes_data_true() -> None:
    report = build_daily_paper_master_divergence_alignment_report(ROOT)
    report["writes_data"] = True
    errors = validate_daily_paper_master_divergence_alignment_report(report)
    assert "writes_data_must_be_false" in errors


def test_validation_fails_if_registers_candidate_rules_true() -> None:
    report = build_daily_paper_master_divergence_alignment_report(ROOT)
    report["alignment_scope"]["registers_candidate_rules"] = True
    errors = validate_daily_paper_master_divergence_alignment_report(report)
    assert "alignment_scope_registers_candidate_rules_mismatch" in errors


def test_cli_no_write_json_returns_valid_payload_and_writes_nothing(tmp_path: Path) -> None:
    output = tmp_path / "alignment.json"
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
    assert payload["read_only"] is True
    assert payload["operational_authority"] is False
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert not output.exists()


def test_cli_output_in_temp_writes_valid_payload(tmp_path: Path) -> None:
    output = tmp_path / "alignment.json"
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


def test_cli_blocks_output_under_data_or_runtime() -> None:
    for directory in ("data", "runtime"):
        output = ROOT / directory / "daily_paper_master_alignment.json"
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
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        assert completed.returncode == 1
        assert payload["status"] == "blocked"
        assert payload["reason"] == "output_path_in_runtime_or_data_scope"
        assert payload["write_performed"] is False
        assert not output.exists()


def test_new_files_do_not_contain_forbidden_operational_tokens() -> None:
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
        "BINANCE" + "_SECRET",
        "BINANCE" + "_API_KEY",
    )
    for path in NEW_FILES:
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token.lower() not in text, path


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
        "docs/DAILY_PAPER_MASTER_DIVERGENCE_ALIGNMENT_V1.md",
        "scripts/build_daily_paper_master_divergence_alignment_v1.py",
        "smartcrypto/research/daily_paper_master_divergence_alignment.py",
        "tests/test_daily_paper_master_divergence_alignment_v1.py",
    }
