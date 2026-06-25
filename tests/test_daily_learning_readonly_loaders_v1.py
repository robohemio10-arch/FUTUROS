from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_learning_contracts import (
    DAILY_LEARNING_SCHEMA_VERSION,
    SAFETY_FLAGS,
    SOURCE_MAP_SCHEMA_VERSION,
    build_daily_learning_source_map,
)
from smartcrypto.research.daily_learning_readonly_loaders import (
    READONLY_LOADERS_SCHEMA_VERSION,
    build_daily_learning_readonly_loader_report,
    inspect_source_readonly,
    validate_daily_learning_readonly_loader_report,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "smartcrypto/research/daily_learning_readonly_loaders.py"
CLI = ROOT / "scripts/build_daily_learning_readonly_loaders_v1.py"
DOC = ROOT / "docs/DAILY_LEARNING_READONLY_LOADERS_V1.md"
TEST_FILE = ROOT / "tests/test_daily_learning_readonly_loaders_v1.py"
NEW_FILES = (MODULE, CLI, DOC, TEST_FILE)


def test_report_schema_references_contracts_and_source_map() -> None:
    report = build_daily_learning_readonly_loader_report(ROOT)
    assert report["schema_version"] == READONLY_LOADERS_SCHEMA_VERSION
    assert report["source_map_schema_version"] == SOURCE_MAP_SCHEMA_VERSION
    assert report["contracts_schema_version"] == DAILY_LEARNING_SCHEMA_VERSION
    assert report["project_name"] == "SMART FUTUROS"


def test_report_is_blocked_and_research_only() -> None:
    report = build_daily_learning_readonly_loader_report(ROOT)
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["reason"] == "readonly_loaders_do_not_grant_operational_authority"
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["operational_authority"] is False


def test_safety_flags_are_preserved() -> None:
    report = build_daily_learning_readonly_loader_report(ROOT)
    for key, expected in SAFETY_FLAGS.items():
        assert report[key] is expected
    assert report["live_trading_enabled"] is False
    assert report["canary_release_allowed"] is False
    assert report["live_release_allowed"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    assert report["sends_orders"] is False


def test_loader_scope_does_not_compute_or_update_anything() -> None:
    scope = build_daily_learning_readonly_loader_report(ROOT)["loader_scope"]
    assert scope["loads_source_metadata"] is True
    assert scope["loads_trade_rows"] is False
    assert scope["loads_candle_rows"] is False
    assert scope["loads_excel_rows"] is False
    assert scope["loads_sqlite_rows"] is False
    assert scope["computes_kpis"] is False
    assert scope["computes_divergence"] is False
    assert scope["computes_alignment"] is False
    assert scope["computes_features"] is False
    assert scope["writes_reports"] is False
    assert scope["updates_models"] is False
    assert scope["updates_risk"] is False
    assert scope["updates_execution"] is False


def test_all_source_map_sources_appear_in_report(tmp_path: Path) -> None:
    source_map = build_daily_learning_source_map(tmp_path)
    report = build_daily_learning_readonly_loader_report(tmp_path)
    expected = {source["source_id"] for source in source_map["sources"]}
    observed = {source["source_id"] for source in report["sources"]}
    assert observed == expected


def test_missing_required_and_optional_sources_are_classified(tmp_path: Path) -> None:
    report = build_daily_learning_readonly_loader_report(tmp_path)
    statuses = {source["source_id"]: source["status"] for source in report["sources"]}
    assert statuses["freqtrade_paper_trades_db"] == "missing_required"
    assert statuses["trades_master_xlsx"] == "missing_required"
    assert statuses["ai_shadow_decision_logger_report"] == "missing_optional"
    assert "freqtrade_paper_trades_db" in report["missing_required_source_ids"]
    assert "ai_shadow_decision_logger_report" in report["optional_missing_source_ids"]


def test_existing_paths_are_metadata_only_without_row_loading(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/PAPER_MASTER_DIVERGENCE_RESEARCH_CLOSEOUT_V1.md").write_text(
        "research closeout\n",
        encoding="utf-8",
    )
    btc_dir = tmp_path / "data/raw/binance_futures_klines_15s/BTCUSDT"
    btc_dir.mkdir(parents=True)
    (btc_dir / "part-001.csv").write_text("not parsed\n", encoding="utf-8")
    optional_report = tmp_path / "data/reports/market_data_health_audit_report.json"
    optional_report.parent.mkdir(parents=True)
    optional_report.write_text('{"status":"ok"}\n', encoding="utf-8")

    report = build_daily_learning_readonly_loader_report(tmp_path)
    sources = _sources_by_id(report)
    assert sources["paper_master_divergence_research_closeout"]["status"] == (
        "metadata_only"
    )
    assert sources["btc_15s_candles"]["status"] == "metadata_only"
    assert sources["market_data_health_audit_report"]["status"] == "metadata_only"
    assert sources["btc_15s_candles"]["metadata"]["file_count"] == 1
    assert sources["market_data_health_audit_report"]["metadata"]["size_bytes"] > 0
    for source in report["sources"]:
        assert source["read_attempted"] is False
        assert source["write_attempted"] is False
        assert source["sample_rows_loaded"] == 0


def test_invalid_path_is_reported_without_exception(tmp_path: Path) -> None:
    source = {
        "source_id": "bad",
        "category": "test",
        "expected_path": "",
        "required": True,
        "freshness_policy": "daily",
        "current_branch_reads_source": False,
        "loader_allowed_future_branch": True,
    }
    result = inspect_source_readonly(tmp_path, source)
    assert result["status"] == "invalid_path"
    assert result["read_attempted"] is False
    assert result["write_attempted"] is False


def test_no_source_item_writes_or_loads_rows(tmp_path: Path) -> None:
    report = build_daily_learning_readonly_loader_report(tmp_path)
    for source in report["sources"]:
        assert source["write_attempted"] is False
        assert source["sample_rows_loaded"] == 0
        assert source["kpis_computed"] is False
        assert source["financial_metrics_computed"] is False
        assert source["alignment_computed"] is False
        assert source["features_computed"] is False


def test_validate_report_returns_empty_list_for_valid_payload(tmp_path: Path) -> None:
    report = build_daily_learning_readonly_loader_report(tmp_path)
    assert validate_daily_learning_readonly_loader_report(report) == []
    assert report["validation_errors"] == []


def test_validation_fails_if_research_only_false(tmp_path: Path) -> None:
    report = build_daily_learning_readonly_loader_report(tmp_path)
    report["research_only"] = False
    errors = validate_daily_learning_readonly_loader_report(report)
    assert "research_only_must_be_true" in errors


def test_validation_fails_if_operational_authority_true(tmp_path: Path) -> None:
    report = build_daily_learning_readonly_loader_report(tmp_path)
    report["operational_authority"] = True
    errors = validate_daily_learning_readonly_loader_report(report)
    assert "operational_authority_must_be_false" in errors


def test_validation_fails_if_any_source_attempts_write(tmp_path: Path) -> None:
    report = build_daily_learning_readonly_loader_report(tmp_path)
    report["sources"][0]["write_attempted"] = True
    errors = validate_daily_learning_readonly_loader_report(report)
    assert "freqtrade_paper_trades_db_write_attempted_must_be_false" in errors


def test_cli_no_write_json_returns_valid_payload_and_writes_nothing(tmp_path: Path) -> None:
    output = tmp_path / "readonly_loaders.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(tmp_path),
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
    output = tmp_path / "readonly_loaders.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--project-root",
            str(tmp_path),
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
        output = ROOT / directory / "daily_learning_readonly_loaders.json"
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
        "docs/DAILY_LEARNING_READONLY_LOADERS_V1.md",
        "scripts/build_daily_learning_readonly_loaders_v1.py",
        "smartcrypto/research/daily_learning_readonly_loaders.py",
        "tests/test_daily_learning_readonly_loaders_v1.py",
    }


def _sources_by_id(report: dict[str, object]) -> dict[str, dict[str, object]]:
    sources = report["sources"]
    assert isinstance(sources, list)
    return {
        source["source_id"]: source
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("source_id"), str)
    }
