from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS
from smartcrypto.research.daily_paper_master_kpi_pack import (
    DAILY_PAPER_MASTER_KPI_PACK_SCHEMA_VERSION,
    build_daily_paper_master_kpi_pack,
    calculate_trade_kpis,
    compare_kpi_summaries,
    validate_daily_paper_master_kpi_pack,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "smartcrypto/research/daily_paper_master_kpi_pack.py"
CLI = ROOT / "scripts/build_daily_paper_master_kpi_pack_v1.py"
DOC = ROOT / "docs/DAILY_PAPER_MASTER_KPI_PACK_V1.md"
TEST_FILE = ROOT / "tests/test_daily_paper_master_kpi_pack_v1.py"
NEW_FILES = (MODULE, CLI, DOC, TEST_FILE)


def sample_paper_trades() -> list[dict[str, object]]:
    return [
        {
            "trade_id": "p1",
            "symbol": "BTCUSDT",
            "side": "long",
            "net_pnl": 10.0,
            "duration_minutes": 5,
            "exit_reason": "roi",
        },
        {
            "trade_id": "p2",
            "symbol": "ETHUSDT",
            "side": "short",
            "net_pnl": -4.0,
            "duration_minutes": 15,
            "exit_reason": "stop_loss",
        },
        {
            "trade_id": "p3",
            "symbol": "BTCUSDT",
            "side": "long",
            "net_pnl": 0.0,
            "duration_minutes": 10,
            "exit_reason": "flat",
        },
        {
            "trade_id": "p4",
            "symbol": "ETHUSDT",
            "side": "long",
            "net_pnl": 6.0,
            "duration_minutes": 20,
            "exit_reason": "roi",
        },
    ]


def sample_master_trades() -> list[dict[str, object]]:
    return [
        {"symbol": "BTCUSDT", "side": "long", "net_pnl": 20.0, "exit_reason": "roi"},
        {"symbol": "ETHUSDT", "side": "short", "net_pnl": -5.0, "exit_reason": "sl"},
    ]


def test_calculate_trade_kpis_empty_list() -> None:
    kpis = calculate_trade_kpis([])
    assert kpis["trade_count"] == 0
    assert kpis["win_count"] == 0
    assert kpis["loss_count"] == 0
    assert kpis["flat_count"] == 0
    assert kpis["win_rate_pct"] is None
    assert kpis["profit_factor"] is None
    assert kpis["expectancy"] is None
    assert kpis["max_drawdown"] == 0.0


def test_calculate_trade_kpis_with_wins_losses_and_flats() -> None:
    kpis = calculate_trade_kpis(sample_paper_trades())
    assert kpis["trade_count"] == 4
    assert kpis["win_count"] == 2
    assert kpis["loss_count"] == 1
    assert kpis["flat_count"] == 1
    assert kpis["win_rate_pct"] == 50.0
    assert kpis["loss_rate_pct"] == 25.0
    assert kpis["gross_profit"] == 16.0
    assert kpis["gross_loss_abs"] == 4.0
    assert kpis["net_pnl"] == 12.0
    assert kpis["best_trade"] == 10.0
    assert kpis["worst_trade"] == -4.0


def test_profit_factor_and_expectancy() -> None:
    kpis = calculate_trade_kpis(sample_paper_trades())
    assert kpis["profit_factor"] == 4.0
    assert kpis["profit_factor_reason"] == "finite_profit_factor"
    assert kpis["expectancy"] == 3.0
    no_loss = calculate_trade_kpis([{"net_pnl": 3.0}, {"net_pnl": 2.0}])
    assert no_loss["profit_factor"] == "inf"
    assert no_loss["profit_factor_reason"] == "no_losses_with_positive_profit"


def test_max_drawdown_is_deterministic() -> None:
    kpis = calculate_trade_kpis(
        [
            {"net_pnl": 5.0},
            {"net_pnl": -7.0},
            {"net_pnl": 2.0},
            {"net_pnl": -1.0},
        ]
    )
    assert kpis["max_drawdown"] == -7.0


def test_counts_by_symbol_side_and_exit_reason() -> None:
    kpis = calculate_trade_kpis(sample_paper_trades())
    assert kpis["symbol_counts"] == {"BTCUSDT": 2, "ETHUSDT": 2}
    assert kpis["side_counts"] == {"long": 3, "short": 1}
    assert kpis["exit_reason_counts"] == {"flat": 1, "roi": 2, "stop_loss": 1}
    assert kpis["avg_duration_minutes"] == 12.5


def test_compare_kpi_summaries() -> None:
    paper = calculate_trade_kpis(sample_paper_trades())
    master = calculate_trade_kpis(sample_master_trades())
    comparison = compare_kpi_summaries(paper, master)
    assert comparison["paper_trade_count"] == 4
    assert comparison["master_trade_count"] == 2
    assert comparison["trade_count_delta"] == 2
    assert comparison["paper_net_pnl"] == 12.0
    assert comparison["master_net_pnl"] == 15.0
    assert comparison["net_pnl_delta"] == -3
    assert comparison["grants_operational_authority"] is False


def test_report_schema_status_and_decision() -> None:
    report = build_daily_paper_master_kpi_pack(
        ROOT,
        paper_trades=sample_paper_trades(),
        master_trades=sample_master_trades(),
    )
    assert report["schema_version"] == DAILY_PAPER_MASTER_KPI_PACK_SCHEMA_VERSION
    assert report["project_name"] == "SMART FUTUROS"
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["reason"] == "kpi_pack_research_only_without_operational_authority"


def test_safety_flags_are_preserved() -> None:
    report = build_daily_paper_master_kpi_pack(ROOT)
    for key, expected in SAFETY_FLAGS.items():
        assert report[key] is expected
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["operational_authority"] is False
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    assert report["sends_orders"] is False


def test_kpi_scope_does_not_do_out_of_scope_work() -> None:
    scope = build_daily_paper_master_kpi_pack(ROOT)["kpi_scope"]
    assert scope["computes_aggregate_kpis"] is True
    assert scope["loads_runtime_trade_rows"] is False
    assert scope["loads_excel_rows"] is False
    assert scope["loads_sqlite_rows"] is False
    assert scope["computes_temporal_alignment"] is False
    assert scope["computes_candle_coverage"] is False
    assert scope["computes_entry_features"] is False
    assert scope["mines_patterns"] is False
    assert scope["registers_candidate_rules"] is False
    assert scope["updates_models"] is False
    assert scope["updates_risk"] is False
    assert scope["updates_execution"] is False


def test_report_without_real_inputs_uses_no_runtime_rows_loaded() -> None:
    report = build_daily_paper_master_kpi_pack(ROOT)
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["paper_kpis"]["trade_count"] == 0
    assert report["master_kpis"]["trade_count"] == 0


def test_validate_returns_empty_list_for_valid_payload() -> None:
    report = build_daily_paper_master_kpi_pack(ROOT)
    assert validate_daily_paper_master_kpi_pack(report) == []
    assert report["validation_errors"] == []


def test_validation_fails_if_research_only_false() -> None:
    report = build_daily_paper_master_kpi_pack(ROOT)
    report["research_only"] = False
    errors = validate_daily_paper_master_kpi_pack(report)
    assert "research_only_must_be_true" in errors


def test_validation_fails_if_operational_authority_true() -> None:
    report = build_daily_paper_master_kpi_pack(ROOT)
    report["operational_authority"] = True
    errors = validate_daily_paper_master_kpi_pack(report)
    assert "operational_authority_must_be_false" in errors


def test_validation_fails_if_writes_data_true() -> None:
    report = build_daily_paper_master_kpi_pack(ROOT)
    report["writes_data"] = True
    errors = validate_daily_paper_master_kpi_pack(report)
    assert "writes_data_must_be_false" in errors


def test_cli_no_write_json_returns_valid_payload_and_writes_nothing(tmp_path: Path) -> None:
    output = tmp_path / "kpi_pack.json"
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
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert not output.exists()


def test_cli_output_in_temp_writes_valid_payload(tmp_path: Path) -> None:
    output = tmp_path / "kpi_pack.json"
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
        output = ROOT / directory / "daily_paper_master_kpi_pack.json"
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
        "docs/DAILY_PAPER_MASTER_KPI_PACK_V1.md",
        "scripts/build_daily_paper_master_kpi_pack_v1.py",
        "smartcrypto/research/daily_paper_master_kpi_pack.py",
        "tests/test_daily_paper_master_kpi_pack_v1.py",
    }
