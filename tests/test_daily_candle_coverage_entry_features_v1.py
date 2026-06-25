from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_candle_coverage_entry_features import (
    DAILY_CANDLE_COVERAGE_ENTRY_FEATURES_SCHEMA_VERSION,
    build_daily_candle_coverage_entry_features_report,
    calculate_candle_coverage,
    materialize_entry_features,
    validate_daily_candle_coverage_entry_features_report,
)
from smartcrypto.research.daily_learning_contracts import SAFETY_FLAGS


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "smartcrypto/research/daily_candle_coverage_entry_features.py"
CLI = ROOT / "scripts/build_daily_candle_coverage_entry_features_v1.py"
DOC = ROOT / "docs/DAILY_CANDLE_COVERAGE_ENTRY_FEATURES_V1.md"
TEST_FILE = ROOT / "tests/test_daily_candle_coverage_entry_features_v1.py"
NEW_FILES = (MODULE, CLI, DOC, TEST_FILE)


def sample_trade() -> dict[str, object]:
    return {
        "trade_id": "t1",
        "symbol": "BTCUSDT",
        "side": "buy",
        "open_time": "2026-06-01T00:05:00Z",
        "net_pnl": 99.0,
    }


def sample_candles(count: int = 21) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        rows.append(
            {
                "timestamp": f"2026-06-01T00:{index // 4:02d}:{(index % 4) * 15:02d}Z",
                "open": 99.5 + index,
                "high": 101.0 + index,
                "low": 99.0 + index,
                "close": 100.0 + index,
                "volume": 10.0,
            }
        )
    return rows


def test_report_none_inputs_uses_no_runtime_rows_loaded() -> None:
    report = build_daily_candle_coverage_entry_features_report(ROOT)
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["status"] == "blocked"
    assert report["write_performed"] is False


def test_report_empty_lists_is_blocked() -> None:
    report = build_daily_candle_coverage_entry_features_report(
        ROOT,
        trades=[],
        candles_by_symbol={},
    )
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["coverage"]["trade_count"] == 0
    assert report["entry_features"]["feature_row_count"] == 0


def test_coverage_with_covered_trade() -> None:
    coverage = calculate_candle_coverage(
        [sample_trade()],
        {"BTCUSDT": sample_candles()},
        (5, 10, 30),
        15,
    )
    assert coverage["trade_count"] == 1
    assert coverage["covered_trade_count"] == 1
    assert coverage["uncovered_trade_count"] == 0
    assert coverage["coverage_rate_pct"] == 100.0
    assert coverage["covered_trade_ids_sample"] == ["t1"]


def test_coverage_with_trade_without_symbol() -> None:
    trade = dict(sample_trade())
    trade.pop("symbol")
    coverage = calculate_candle_coverage([trade], {"BTCUSDT": sample_candles()}, (5,), 15)
    assert coverage["missing_symbol_count"] == 1
    assert coverage["uncovered_trade_count"] == 1


def test_coverage_with_trade_without_entry_time() -> None:
    trade = dict(sample_trade())
    trade.pop("open_time")
    coverage = calculate_candle_coverage([trade], {"BTCUSDT": sample_candles()}, (5,), 15)
    assert coverage["missing_entry_time_count"] == 1
    assert coverage["uncovered_trade_count"] == 1


def test_coverage_with_missing_candle_symbol() -> None:
    coverage = calculate_candle_coverage([sample_trade()], {}, (5,), 15)
    assert coverage["missing_candle_symbol_count"] == 1
    assert coverage["uncovered_trade_count"] == 1


def test_coverage_by_window_5_10_30() -> None:
    coverage = calculate_candle_coverage(
        [sample_trade()],
        {"BTCUSDT": sample_candles()},
        (5, 10, 30),
        15,
    )
    assert set(coverage["coverage_by_window"]) == {"5", "10", "30"}
    assert coverage["coverage_by_window"]["5"]["expected_candle_count"] == 21
    assert coverage["coverage_by_window"]["10"]["expected_candle_count"] == 41
    assert coverage["coverage_by_window"]["30"]["expected_candle_count"] == 121


def test_feature_row_uses_only_candles_before_or_at_entry_time() -> None:
    future_candle = {
        "timestamp": "2026-06-01T00:05:15Z",
        "open": 999.0,
        "high": 999.0,
        "low": 999.0,
        "close": 999.0,
        "volume": 999.0,
    }
    features = materialize_entry_features(
        [sample_trade()],
        {"BTCUSDT": [*sample_candles(), future_candle]},
        (5,),
        15,
    )
    row = features["feature_rows_sample"][0]
    assert row["entry_close"] == 120.0
    assert row["entry_volume"] == 10.0


def test_window_return_range_and_volume_sum() -> None:
    row = materialize_entry_features(
        [sample_trade()],
        {"BTCUSDT": sample_candles()},
        (5,),
        15,
    )["feature_rows_sample"][0]
    assert row["lb_5m_candle_count"] == 21
    assert row["lb_5m_expected_candle_count"] == 21
    assert row["lb_5m_coverage_ratio"] == 1.0
    assert abs(row["lb_5m_ret_close"] - 0.2) < 1e-12
    assert row["lb_5m_high_low_range_pct"] == (121.0 / 99.0) - 1
    assert row["lb_5m_volume_sum"] == 210.0


def test_sma_20_and_distance() -> None:
    row = materialize_entry_features(
        [sample_trade()],
        {"BTCUSDT": sample_candles()},
        (5,),
        15,
    )["feature_rows_sample"][0]
    assert row["sma_20"] == 110.5
    assert row["dist_sma_20_pct"] == (120.0 / 110.5) - 1


def test_rsi_14_with_enough_data() -> None:
    row = materialize_entry_features(
        [sample_trade()],
        {"BTCUSDT": sample_candles()},
        (5,),
        15,
    )["feature_rows_sample"][0]
    assert row["rsi_14"] == 100.0


def test_rsi_14_none_with_insufficient_data() -> None:
    row = materialize_entry_features(
        [sample_trade()],
        {"BTCUSDT": sample_candles(10)},
        (5,),
        15,
    )["feature_rows_sample"][0]
    assert row["rsi_14"] is None


def test_pre_entry_volatility_20() -> None:
    row = materialize_entry_features(
        [sample_trade()],
        {"BTCUSDT": sample_candles()},
        (5,),
        15,
    )["feature_rows_sample"][0]
    assert row["pre_entry_volatility_20"] is not None
    assert row["pre_entry_volatility_20"] >= 0


def test_features_do_not_include_net_pnl() -> None:
    features = materialize_entry_features(
        [sample_trade()],
        {"BTCUSDT": sample_candles()},
        (5,),
        15,
    )
    row = features["feature_rows_sample"][0]
    assert "net_pnl" not in row
    assert "net_pnl" not in features["feature_columns"]


def test_features_computed_true_without_authority() -> None:
    report = build_daily_candle_coverage_entry_features_report(
        ROOT,
        trades=[sample_trade()],
        candles_by_symbol={"BTCUSDT": sample_candles()},
    )
    assert report["entry_features"]["features_computed"] is True
    assert report["operational_authority"] is False


def test_report_schema_status_and_decision() -> None:
    report = build_daily_candle_coverage_entry_features_report(ROOT)
    assert report["schema_version"] == DAILY_CANDLE_COVERAGE_ENTRY_FEATURES_SCHEMA_VERSION
    assert report["project_name"] == "SMART FUTUROS"
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["reason"] == (
        "candle_coverage_entry_features_research_only_without_operational_authority"
    )


def test_safety_flags_are_preserved() -> None:
    report = build_daily_candle_coverage_entry_features_report(ROOT)
    for key, expected in SAFETY_FLAGS.items():
        assert report[key] is expected
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["operational_authority"] is False
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["exchange_private_access"] is False


def test_feature_scope_blocks_out_of_scope_work() -> None:
    scope = build_daily_candle_coverage_entry_features_report(ROOT)["feature_scope"]
    assert scope["computes_candle_coverage"] is True
    assert scope["computes_entry_features"] is True
    assert scope["loads_runtime_trade_rows"] is False
    assert scope["loads_excel_rows"] is False
    assert scope["loads_sqlite_rows"] is False
    assert scope["loads_real_candle_rows"] is False
    assert scope["uses_only_in_memory_inputs"] is True
    assert scope["computes_labels"] is False
    assert scope["uses_net_pnl_as_feature"] is False
    assert scope["mines_patterns"] is False
    assert scope["registers_candidate_rules"] is False
    assert scope["runs_oos_validation"] is False
    assert scope["updates_models"] is False
    assert scope["updates_risk"] is False
    assert scope["updates_execution"] is False


def test_validate_returns_empty_list_for_valid_payload() -> None:
    report = build_daily_candle_coverage_entry_features_report(ROOT)
    assert validate_daily_candle_coverage_entry_features_report(report) == []
    assert report["validation_errors"] == []


def test_validation_fails_if_research_only_false() -> None:
    report = build_daily_candle_coverage_entry_features_report(ROOT)
    report["research_only"] = False
    errors = validate_daily_candle_coverage_entry_features_report(report)
    assert "research_only_must_be_true" in errors


def test_validation_fails_if_operational_authority_true() -> None:
    report = build_daily_candle_coverage_entry_features_report(ROOT)
    report["operational_authority"] = True
    errors = validate_daily_candle_coverage_entry_features_report(report)
    assert "operational_authority_must_be_false" in errors


def test_validation_fails_if_writes_data_true() -> None:
    report = build_daily_candle_coverage_entry_features_report(ROOT)
    report["writes_data"] = True
    errors = validate_daily_candle_coverage_entry_features_report(report)
    assert "writes_data_must_be_false" in errors


def test_validation_fails_if_registers_candidate_rules_true() -> None:
    report = build_daily_candle_coverage_entry_features_report(ROOT)
    report["feature_scope"]["registers_candidate_rules"] = True
    errors = validate_daily_candle_coverage_entry_features_report(report)
    assert "feature_scope_registers_candidate_rules_mismatch" in errors


def test_validation_fails_if_uses_net_pnl_as_feature_true() -> None:
    report = build_daily_candle_coverage_entry_features_report(ROOT)
    report["feature_scope"]["uses_net_pnl_as_feature"] = True
    errors = validate_daily_candle_coverage_entry_features_report(report)
    assert "feature_scope_uses_net_pnl_as_feature_mismatch" in errors


def test_cli_no_write_json_returns_valid_payload_and_writes_nothing(tmp_path: Path) -> None:
    output = tmp_path / "features.json"
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
    output = tmp_path / "features.json"
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
        output = ROOT / directory / "daily_candle_features.json"
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
        "docs/DAILY_CANDLE_COVERAGE_ENTRY_FEATURES_V1.md",
        "scripts/build_daily_candle_coverage_entry_features_v1.py",
        "smartcrypto/research/daily_candle_coverage_entry_features.py",
        "tests/test_daily_candle_coverage_entry_features_v1.py",
    }
