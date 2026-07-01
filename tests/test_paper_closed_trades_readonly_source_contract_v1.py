from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_closed_trades_readonly_source_contract import (
    SCHEMA_VERSION,
    build_paper_closed_trades_readonly_source_contract_report,
    normalize_closed_trade_rows,
)


def _valid_rows() -> list[dict[str, object]]:
    return [
        {
            "trade_id": "101",
            "order_id": "ord-101",
            "internal_order_id": "int-101",
            "symbol": "BTC/USDT",
            "side": "long",
            "open_time_utc": "2026-06-01T10:00:00Z",
            "close_time_utc": "2026-06-01T10:15:00Z",
            "open_rate": 100.0,
            "close_rate": 105.0,
            "amount": 0.01,
            "stake_amount": 10.0,
            "profit_abs": 5.0,
            "profit_ratio": 0.05,
            "fee": 0.1,
        },
        {
            "trade_id": "102",
            "order_id": "ord-102",
            "internal_order_id": "int-102",
            "symbol": "ETHUSDT",
            "side": "short",
            "open_time_utc": "2026-06-01T11:00:00Z",
            "close_time_utc": "2026-06-01T11:20:00Z",
            "open_rate": 200.0,
            "close_rate": 190.0,
            "amount": 0.2,
            "stake_amount": 40.0,
            "profit_abs": 10.0,
            "profit_ratio": 0.25,
            "fee": 0.2,
        },
    ]


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_no_runtime_read_by_default_blocks_safely(tmp_path: Path) -> None:
    report = build_paper_closed_trades_readonly_source_contract_report(project_root=tmp_path)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["reason"] == "closed_trades_source_contract_requires_explicit_runtime_read_or_in_memory_inputs"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False
    assert report["write_performed"] is False


def test_missing_sources_are_reported_structurally(tmp_path: Path) -> None:
    report = build_paper_closed_trades_readonly_source_contract_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        source_paths=["data/reports/missing_closed_trades.csv"],
    )

    assert report["status"] == "blocked"
    assert report["source_status"] == "blocked"
    assert report["source_contract_status"] == "blocked"
    assert report["candidate_sources_checked"] == ["data/reports/missing_closed_trades.csv"]
    assert report["candidate_sources_present"] == []
    assert report["candidate_sources_missing"] == ["data/reports/missing_closed_trades.csv"]


def test_detects_supported_closed_trades_schema(tmp_path: Path) -> None:
    source = tmp_path / "data" / "reports" / "closed.csv"
    _write_csv(source, _valid_rows())

    report = build_paper_closed_trades_readonly_source_contract_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        source_paths=["data/reports/closed.csv"],
    )

    assert report["source_contract_status"] == "ok"
    assert report["canonical_field_mapping"]["symbol"] == "symbol"
    assert report["canonical_field_mapping"]["entry_price"] == "open_rate"
    assert report["canonical_field_mapping"]["pnl"] == "profit_abs"
    assert report["missing_required_fields"] == []
    assert report["normalized_closed_trade_count"] == 2


def test_normalizes_closed_trade_rows_from_fixture() -> None:
    normalized, rejected, mapping = normalize_closed_trade_rows(_valid_rows(), source_path="fixture.csv", source_sha256="abc")

    assert rejected == []
    assert mapping["close_time"] == "close_time_utc"
    assert normalized[0]["trade_id"] == "101"
    assert normalized[0]["order_id"] == "ord-101"
    assert normalized[0]["symbol"] == "BTCUSDT"
    assert normalized[0]["side"] == "long"
    assert normalized[0]["entry_price"] == 100.0
    assert normalized[0]["exit_price"] == 105.0
    assert normalized[0]["pnl"] == 5.0
    assert normalized[0]["source_path"] == "fixture.csv"
    assert normalized[0]["source_sha256"] == "abc"
    assert normalized[0]["row_fingerprint"]


def test_rejects_rows_missing_required_fields() -> None:
    rows = _valid_rows()
    rows[0].pop("symbol")
    rows[0].pop("pair", None)
    normalized, rejected, _mapping = normalize_closed_trade_rows(rows)

    assert len(normalized) == 1
    assert len(rejected) == 1
    assert "symbol" in rejected[0]["missing_required_fields"]


def test_generates_stable_row_fingerprint() -> None:
    first, _rejected, _mapping = normalize_closed_trade_rows(_valid_rows())
    second, _rejected_again, _mapping_again = normalize_closed_trade_rows(_valid_rows())

    assert first[0]["row_fingerprint"] == second[0]["row_fingerprint"]
    assert first[1]["row_fingerprint"] == second[1]["row_fingerprint"]


def test_detects_duplicate_join_keys(tmp_path: Path) -> None:
    rows = _valid_rows()
    rows[1]["order_id"] = "ord-101"
    report = build_paper_closed_trades_readonly_source_contract_report(
        project_root=tmp_path,
        source_rows=rows,
    )

    order_candidate = next(item for item in report["join_key_candidates"] if item["field"] == "order_id")
    assert order_candidate["duplicate_count"] == 1
    assert order_candidate["is_unique"] is False
    assert report["recommended_join_key"] == "internal_order_id"


def test_recommends_join_key_when_contract_is_complete(tmp_path: Path) -> None:
    report = build_paper_closed_trades_readonly_source_contract_report(
        project_root=tmp_path,
        source_rows=_valid_rows(),
    )

    assert report["source_contract_status"] == "ok"
    assert report["recommended_join_key"] == "order_id"
    assert report["replay_ready"] is True
    assert report["attribution_ready"] is True
    assert report["paper_observation_allowed"] is False


def test_replay_and_attribution_remain_false_without_complete_contract(tmp_path: Path) -> None:
    rows = _valid_rows()
    rows[0]["close_rate"] = ""
    rows[1]["close_rate"] = ""
    report = build_paper_closed_trades_readonly_source_contract_report(
        project_root=tmp_path,
        source_rows=rows,
    )

    assert report["source_contract_status"] == "blocked"
    assert report["replay_ready"] is False
    assert report["attribution_ready"] is False
    assert "exit_price" in report["missing_required_fields"]


def test_write_requires_explicit_flag_and_only_writes_reports(tmp_path: Path) -> None:
    no_write = build_paper_closed_trades_readonly_source_contract_report(
        project_root=tmp_path,
        source_rows=_valid_rows(),
    )
    assert no_write["write_performed"] is False
    assert not (tmp_path / "data").exists()

    written = build_paper_closed_trades_readonly_source_contract_report(
        project_root=tmp_path,
        source_rows=_valid_rows(),
        write=True,
        no_write=False,
    )

    json_report = tmp_path / "data" / "reports" / "paper_closed_trades_readonly_source_contract_v1.json"
    markdown_report = tmp_path / "data" / "reports" / "paper_closed_trades_readonly_source_contract_v1.md"
    assert written["write_performed"] is True
    assert json_report.exists()
    assert markdown_report.exists()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))
    assert json.loads(json_report.read_text(encoding="utf-8"))["decision"] == "MANTER_EM_RESEARCH"


def test_no_operational_authority_flags(tmp_path: Path) -> None:
    report = build_paper_closed_trades_readonly_source_contract_report(
        project_root=tmp_path,
        source_rows=_valid_rows(),
    )

    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["operational_authority"] is False
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False
    assert report["can_promote_rules"] is False
    assert report["can_apply_to_freqtrade"] is False
    assert report["can_apply_to_risk_manager"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False


def test_does_not_write_runtime_sqlite_or_parquet(tmp_path: Path) -> None:
    build_paper_closed_trades_readonly_source_contract_report(
        project_root=tmp_path,
        source_rows=_valid_rows(),
        write=True,
        no_write=False,
    )

    assert not (tmp_path / "data" / "runtime").exists()
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.db"))
    assert not list(tmp_path.rglob("*.parquet"))


def test_cli_no_runtime_json_executes() -> None:
    script = Path("scripts/build_paper_closed_trades_readonly_source_contract_v1.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--no-write", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["paper_observation_allowed"] is False
    assert payload["ready_for_shadow_observation"] is False
    assert payload["operational_authority"] is False
    assert payload["can_promote_rules"] is False
    assert payload["can_apply_to_freqtrade"] is False
    assert payload["can_apply_to_risk_manager"] is False
    assert payload["sends_orders"] is False
    assert payload["changes_risk"] is False
    assert payload["writes_runtime"] is False
    assert payload["writes_sqlite"] is False
    assert payload["writes_parquet"] is False
    assert payload["write_performed"] is False
