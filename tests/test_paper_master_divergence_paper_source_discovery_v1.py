from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_master_divergence_paper_source_discovery import (
    OOS_SLICE_DIMENSIONS,
    SCHEMA_VERSION,
    build_paper_master_divergence_paper_source_discovery_report,
    discover_paper_source_candidates,
    inspect_candidate_path,
)


def _write_csv(path: Path) -> None:
    rows = [
        {
            "id": "t1",
            "pair": "ETH/USDT:USDT",
            "side": "long",
            "close_date": "2026-06-05 10:12:00",
            "profit_abs": "-1.25",
            "sell_reason": "stop_loss",
            "duration_minutes": "12",
        },
        {
            "id": "t2",
            "pair": "BTC/USDT:USDT",
            "side": "short",
            "close_date": "2026-06-06 11:45:00",
            "profit_abs": "2.50",
            "sell_reason": "roi",
            "duration_minutes": "105",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_default_report_blocks_runtime_discovery_and_preserves_safety() -> None:
    report = build_paper_master_divergence_paper_source_discovery_report(project_root=".")

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["input_mode"] == "no_runtime_discovery"
    assert report["allow_runtime_read"] is False
    assert report["paper_source_discovery_created"] is True
    assert report["paper_source_candidates_discovered"] is False
    assert report["paper_source_selected"] is False
    assert report["ready_for_real_slice_computation"] is False
    assert report["real_slice_metrics_computed"] is False
    assert report["write_performed"] is False
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False
    assert report["updates_freqtrade"] is False
    assert report["updates_risk_manager"] is False
    assert report["updates_qlib_runtime"] is False
    assert report["updates_ai_shadow_runtime"] is False


def test_declares_oos_scope_and_divergence_contract() -> None:
    report = build_paper_master_divergence_paper_source_discovery_report(project_root=".")

    assert report["hypothesis_scope"] == ["H1", "H2", "H6"]
    assert report["oos_slice_dimensions"] == OOS_SLICE_DIMENSIONS
    assert report["oos_validation_required"] is True
    assert report["oos_validated"] is False
    assert report["paper_replicates_master_edge"] is False
    assert report["divergence_metrics"]["paper_minus_master_net_pnl"] == -164.52110752


def test_candidate_source_is_not_scanned_without_runtime_read(tmp_path: Path) -> None:
    source = tmp_path / "paper_trades.csv"
    _write_csv(source)

    candidates = discover_paper_source_candidates(
        project_root=tmp_path,
        allow_runtime_read=False,
        candidate_sources=[source],
    )

    assert candidates == []


def test_csv_candidate_is_discovered_with_high_confidence(tmp_path: Path) -> None:
    source = tmp_path / "paper_trades_closed.csv"
    _write_csv(source)

    report = build_paper_master_divergence_paper_source_discovery_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        candidate_sources=[source],
        discovery_roots=[],
    )

    assert report["input_mode"] == "runtime_discovery_read_only"
    assert report["paper_source_candidates_discovered"] is True
    assert report["paper_source_candidate_count"] == 1
    assert report["best_paper_source_candidate"]["confidence"] == "high"
    assert report["best_paper_source_candidate"]["row_count_estimate"] == 2
    assert report["best_paper_source_candidate"]["schema_status"] == "candidate_trade_schema"
    assert report["paper_source_selected"] is False
    assert report["ready_for_real_slice_computation"] is False


def test_json_candidate_rows_are_discovered(tmp_path: Path) -> None:
    source = tmp_path / "freqtrade_paper_history.json"
    source.write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "pair": "ETH/USDT:USDT",
                        "side": "long",
                        "close_date": "2026-06-05",
                        "profit_abs": -1.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    candidate = inspect_candidate_path(tmp_path, source)

    assert candidate.exists is True
    assert candidate.row_count_estimate == 1
    assert candidate.schema_status == "candidate_trade_schema"
    assert candidate.confidence in {"high", "medium"}


def test_jsonl_candidate_rows_are_discovered(tmp_path: Path) -> None:
    source = tmp_path / "paper_closed_trades.jsonl"
    source.write_text(
        json.dumps({"pair": "BTC/USDT:USDT", "close_date": "2026-06-05", "profit_abs": 1.0})
        + "\n",
        encoding="utf-8",
    )

    candidate = inspect_candidate_path(tmp_path, source)

    assert candidate.row_count_estimate == 1
    assert candidate.schema_status == "candidate_trade_schema"
    assert candidate.discovery_status == "candidate"


def test_sqlite_candidate_is_metadata_scanned_read_only(tmp_path: Path) -> None:
    source = tmp_path / "freqtrade_paper.sqlite"
    connection = sqlite3.connect(source)
    try:
        connection.execute(
            "CREATE TABLE trades (id TEXT, pair TEXT, side TEXT, close_date TEXT, profit_abs REAL)"
        )
        connection.execute(
            "INSERT INTO trades VALUES ('t1', 'ETH/USDT:USDT', 'long', '2026-06-05', -1.2)"
        )
        connection.commit()
    finally:
        connection.close()

    candidate = inspect_candidate_path(tmp_path, source)

    assert candidate.source_type == "sqlite"
    assert candidate.row_count_estimate == 1
    assert candidate.schema_status == "candidate_trade_schema"
    assert candidate.confidence in {"high", "medium"}


def test_master_like_source_is_penalized(tmp_path: Path) -> None:
    source = tmp_path / "trades_master.xlsx"
    source.write_bytes(b"not-a-real-xlsx-but-metadata-is-enough")

    candidate = inspect_candidate_path(tmp_path, source)

    assert "master" in candidate.negative_signals
    assert candidate.score < 40
    assert candidate.requires_manual_review is True


def test_discovery_roots_are_scanned_when_allowed(tmp_path: Path) -> None:
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    _write_csv(reports / "paper_runtime_closed_trades.csv")

    report = build_paper_master_divergence_paper_source_discovery_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        discovery_roots=["data/reports"],
    )

    assert report["candidate_count"] == 1
    assert report["best_paper_source_candidate"]["path"].replace("\\", "/") == (
        "data/reports/paper_runtime_closed_trades.csv"
    )


def test_missing_and_unsupported_candidates_are_rejected(tmp_path: Path) -> None:
    missing = inspect_candidate_path(tmp_path, tmp_path / "missing.csv")
    assert missing.discovery_status == "missing_source"
    assert missing.confidence == "rejected"

    unsupported_path = tmp_path / "paper.txt"
    unsupported_path.write_text("x", encoding="utf-8")
    unsupported = inspect_candidate_path(tmp_path, unsupported_path)
    assert unsupported.discovery_status == "unsupported_source_type"
    assert unsupported.confidence == "rejected"


def test_gate_summary_has_no_failed_gates() -> None:
    report = build_paper_master_divergence_paper_source_discovery_report(project_root=".")

    assert report["gate_summary"]["gate_count"] == 6
    assert report["gate_summary"]["passed_gate_count"] == 6
    assert report["gate_summary"]["failed_gate_count"] == 0
    assert report["gate_summary"]["critical_failed_gate_ids"] == []


def test_cli_no_write_json_default() -> None:
    command = [
        sys.executable,
        "scripts/build_paper_master_divergence_paper_source_discovery_v1.py",
        "--project-root",
        ".",
        "--no-write",
        "--json",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["write_performed"] is False
    assert payload["allow_runtime_read"] is False
    assert payload["paper_source_selected"] is False
