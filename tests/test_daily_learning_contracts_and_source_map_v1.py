from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_learning_contracts import (
    DAILY_LEARNING_SCHEMA_VERSION,
    REQUIRED_SOURCE_IDS,
    SAFETY_FLAGS,
    SOURCE_MAP_SCHEMA_VERSION,
    build_daily_learning_contract_payload,
    build_daily_learning_source_map,
    validate_daily_learning_contract_payload,
    validate_daily_learning_source_map,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "smartcrypto/research/daily_learning_contracts.py"
CLI = ROOT / "scripts/build_daily_learning_source_map_v1.py"
DOC = ROOT / "docs/DAILY_LEARNING_CONTRACTS_AND_SOURCE_MAP_V1.md"
TEST_FILE = ROOT / "tests/test_daily_learning_contracts_and_source_map_v1.py"
NEW_FILES = (MODULE, CLI, DOC, TEST_FILE)


def test_source_map_schema_is_canonical() -> None:
    payload = build_daily_learning_source_map(ROOT)
    assert payload["schema_version"] == SOURCE_MAP_SCHEMA_VERSION
    assert payload["project_name"] == "SMART FUTUROS"
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["reason"] == "source_map_defined_without_runtime_readers"


def test_contract_schema_is_canonical_and_blocked() -> None:
    payload = build_daily_learning_contract_payload(ROOT)
    assert payload["schema_version"] == DAILY_LEARNING_SCHEMA_VERSION
    assert payload["source_map_schema_version"] == SOURCE_MAP_SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["reason"] == "contracts_defined_without_runtime_readers"
    assert payload["research_only"] is True
    assert payload["operational_authority"] is False


def test_required_sources_exist_in_source_map() -> None:
    source_map = build_daily_learning_source_map()
    sources = _sources_by_id(source_map)
    assert set(REQUIRED_SOURCE_IDS) <= set(sources)
    assert sources["freqtrade_paper_trades_db"]["expected_path"] == (
        "freqtrade/user_data/tradesv3.dryrun.sqlite"
    )
    assert sources["trades_master_xlsx"]["expected_path"] == (
        "data/processed/trades_master.xlsx"
    )
    assert sources["btc_15s_candles"]["expected_path"] == (
        "data/raw/binance_futures_klines_15s/BTCUSDT"
    )
    assert sources["eth_15s_candles"]["expected_path"] == (
        "data/raw/binance_futures_klines_15s/ETHUSDT"
    )
    assert sources["paper_master_divergence_research_closeout"]["expected_path"] == (
        "docs/PAPER_MASTER_DIVERGENCE_RESEARCH_CLOSEOUT_V1.md"
    )
    assert all(sources[source_id]["required"] is True for source_id in REQUIRED_SOURCE_IDS)


def test_optional_sources_exist_in_source_map() -> None:
    source_map = build_daily_learning_source_map()
    sources = _sources_by_id(source_map)
    expected = {
        "ai_shadow_decision_logger_report",
        "ai_shadow_outcome_tracker_report",
        "ai_selector_observations",
        "market_data_health_audit_report",
        "runtime_evidence_pack",
        "readiness_snapshot",
        "paper_shadow_soak_gap_accounting",
    }
    assert expected <= set(sources)
    assert all(sources[source_id]["required"] is False for source_id in expected)


def test_all_sources_have_unique_ids() -> None:
    source_map = build_daily_learning_source_map()
    source_ids = [source["source_id"] for source in source_map["sources"]]
    assert len(source_ids) == len(set(source_ids))


def test_no_source_is_read_or_written_in_this_branch() -> None:
    source_map = build_daily_learning_source_map()
    for source in source_map["sources"]:
        assert source["current_branch_reads_source"] is False
        assert source["current_branch_writes_source"] is False


def test_safety_flags_are_preserved_on_contract_and_source_map() -> None:
    contract = build_daily_learning_contract_payload(ROOT)
    source_map = contract["source_map"]
    for key, expected in SAFETY_FLAGS.items():
        assert contract[key] is expected
        assert source_map[key] is expected
    assert contract["read_only"] is True
    assert contract["live_trading_enabled"] is False
    assert contract["order_submission_enabled"] is False
    assert contract["real_order_submission_enabled"] is False
    assert contract["exchange_private_access"] is False
    assert contract["sends_orders"] is False


def test_validate_source_map_returns_empty_list_for_valid_payload() -> None:
    source_map = build_daily_learning_source_map(ROOT)
    assert validate_daily_learning_source_map(source_map) == []
    assert source_map["validation_errors"] == []


def test_validate_contract_returns_empty_list_for_valid_payload() -> None:
    contract = build_daily_learning_contract_payload(ROOT)
    assert validate_daily_learning_contract_payload(contract) == []
    assert contract["validation_errors"] == []


def test_validation_fails_if_research_only_is_false() -> None:
    contract = build_daily_learning_contract_payload(ROOT)
    contract["research_only"] = False
    errors = validate_daily_learning_contract_payload(contract)
    assert "research_only_must_be_true" in errors


def test_validation_fails_if_required_source_is_removed() -> None:
    source_map = build_daily_learning_source_map(ROOT)
    source_map["sources"] = [
        source
        for source in source_map["sources"]
        if source["source_id"] != "trades_master_xlsx"
    ]
    errors = validate_daily_learning_source_map(source_map)
    assert "missing_required_source:trades_master_xlsx" in errors


def test_contract_scope_and_readiness_policy_are_fail_closed() -> None:
    contract = build_daily_learning_contract_payload(ROOT)
    scope = contract["daily_learning_scope"]
    assert scope["defines_contracts"] is True
    assert scope["defines_source_map"] is True
    assert scope["loads_sources"] is False
    assert scope["computes_kpis"] is False
    assert scope["writes_reports"] is False
    assert scope["updates_models"] is False
    assert scope["updates_risk"] is False
    assert scope["updates_execution"] is False
    readiness = contract["readiness_policy"]
    assert readiness["source_map_is_not_readiness_evidence"] is True
    assert readiness["daily_learning_outputs_do_not_release_live"] is True
    assert readiness["daily_learning_outputs_do_not_release_canary"] is True
    assert readiness["manual_go_no_go_required"] is True


def test_required_future_branches_are_declared() -> None:
    contract = build_daily_learning_contract_payload(ROOT)
    branches = contract["required_future_branches"]
    assert branches[0] == "codex/daily-learning-readonly-loaders-v1"
    assert branches[-1] == "codex/daily-learning-loop-closeout-handover-v1"
    assert len(branches) == 15


def test_cli_no_write_json_returns_valid_payload_and_writes_nothing(tmp_path: Path) -> None:
    output = tmp_path / "daily_learning_contract.json"
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


def test_cli_output_in_temp_writes_valid_payload(tmp_path: Path) -> None:
    output = tmp_path / "daily_learning_contract.json"
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
        output = ROOT / directory / "daily_learning_contract.json"
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
        "docs/DAILY_LEARNING_CONTRACTS_AND_SOURCE_MAP_V1.md",
        "scripts/build_daily_learning_source_map_v1.py",
        "smartcrypto/research/daily_learning_contracts.py",
        "tests/test_daily_learning_contracts_and_source_map_v1.py",
    }


def _sources_by_id(source_map: dict[str, object]) -> dict[str, dict[str, object]]:
    sources = source_map["sources"]
    assert isinstance(sources, list)
    return {
        source["source_id"]: source
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("source_id"), str)
    }
