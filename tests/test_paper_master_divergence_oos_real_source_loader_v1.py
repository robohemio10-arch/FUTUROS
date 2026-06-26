from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_master_divergence_oos_real_source_loader import (
    MINIMUM_NORMALIZED_COLUMNS,
    OOS_SLICE_DIMENSIONS,
    build_paper_master_divergence_oos_real_source_loader_report,
    load_trade_source,
    normalize_trade_rows,
)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _sample_rows() -> list[dict[str, object]]:
    return [
        {
            "id": "t1",
            "pair": "ETH/USDT:USDT",
            "side": "long",
            "open_date": "2026-06-05 10:00:00",
            "close_date": "2026-06-05 10:12:00",
            "profit_abs": "-1.25",
            "sell_reason": "stop_loss",
            "duration_minutes": "12",
            "covered_feature_subset": "true",
        },
        {
            "id": "t2",
            "pair": "BTC/USDT:USDT",
            "side": "short",
            "open_date": "2026-06-06 10:00:00",
            "close_date": "2026-06-06 11:45:00",
            "profit_abs": "2.50",
            "sell_reason": "roi",
            "duration_minutes": "105",
            "covered_feature_subset": "false",
        },
    ]


def test_default_report_blocks_runtime_loading_and_preserves_safety_flags() -> None:
    report = build_paper_master_divergence_oos_real_source_loader_report(project_root=".")

    assert report["schema_version"] == "paper_master_divergence_oos_real_source_loader_v1"
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["real_source_loader_created"] is True
    assert report["real_sources_loaded"] is False
    assert report["allow_runtime_read"] is False
    assert report["oos_ready_for_slice_metrics"] is False
    assert report["write_performed"] is False
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False
    assert report["can_apply_to_freqtrade"] is False
    assert report["can_apply_to_risk_manager"] is False
    assert report["can_promote_rules"] is False
    assert report["can_promote_model"] is False
    assert report["updates_freqtrade"] is False
    assert report["updates_risk_manager"] is False
    assert report["updates_qlib_runtime"] is False
    assert report["updates_ai_shadow_runtime"] is False


def test_schema_and_oos_dimensions_are_declared() -> None:
    report = build_paper_master_divergence_oos_real_source_loader_report(project_root=".")

    assert report["minimum_normalized_columns"] == MINIMUM_NORMALIZED_COLUMNS
    assert report["oos_slice_dimensions"] == OOS_SLICE_DIMENSIONS
    assert report["hypothesis_scope"] == ["H1", "H2", "H6"]
    assert report["oos_validation_required"] is True
    assert report["oos_validated"] is False
    assert report["oos_slice_metrics_computed"] is False


def test_source_paths_do_not_load_without_explicit_allow_runtime_read(tmp_path: Path) -> None:
    source = tmp_path / "paper.csv"
    _write_csv(source, _sample_rows())

    result = load_trade_source(source, source_role="paper", allow_runtime_read=False)

    assert result.source_status == "blocked_runtime_read_not_allowed"
    assert result.row_count_normalized == 0
    assert result.normalized_rows == []
    assert "explicit_allow_runtime_read_required" in result.validation_errors


def test_csv_sources_load_when_runtime_read_is_explicitly_allowed(tmp_path: Path) -> None:
    paper_source = tmp_path / "paper.csv"
    master_source = tmp_path / "master.csv"
    _write_csv(paper_source, _sample_rows())
    _write_csv(master_source, _sample_rows())

    report = build_paper_master_divergence_oos_real_source_loader_report(
        project_root=".",
        paper_source=paper_source,
        master_source=master_source,
        allow_runtime_read=True,
    )

    assert report["status"] == "blocked"
    assert report["input_mode"] == "real_sources_loaded_read_only"
    assert report["real_sources_loaded"] is True
    assert report["oos_ready_for_slice_metrics"] is True
    assert report["source_summary"]["paper"]["source_status"] == "loaded"
    assert report["source_summary"]["master"]["source_status"] == "loaded"
    assert report["source_summary"]["paper"]["row_count_normalized"] == 2
    assert report["loaded_rows_report"]["common_symbols"] == ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    assert report["loaded_rows_report"]["common_sides"] == ["long", "short"]
    assert report["remediation_application_allowed"] is False
    assert report["ready_for_candidate_registry"] is False


def test_json_source_rows_load_when_payload_contains_rows(tmp_path: Path) -> None:
    source = tmp_path / "paper.json"
    source.write_text(json.dumps({"rows": _sample_rows()}), encoding="utf-8")

    result = load_trade_source(source, source_role="paper", allow_runtime_read=True)

    assert result.source_status == "loaded"
    assert result.row_count_raw == 2
    assert result.row_count_normalized == 2
    assert result.symbols == ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    assert result.sides == ["long", "short"]


def test_normalize_trade_rows_maps_aliases_and_duration_buckets() -> None:
    normalized_rows, errors = normalize_trade_rows(_sample_rows(), source_role="paper")

    assert errors == []
    assert normalized_rows[0]["source_role"] == "paper"
    assert normalized_rows[0]["trade_id"] == "t1"
    assert normalized_rows[0]["symbol"] == "ETH/USDT:USDT"
    assert normalized_rows[0]["side"] == "long"
    assert normalized_rows[0]["day"] == "2026-06-05"
    assert normalized_rows[0]["pnl"] == -1.25
    assert normalized_rows[0]["exit_reason"] == "stop_loss"
    assert normalized_rows[0]["duration_bucket"] == "under_15m"
    assert normalized_rows[0]["covered_vs_uncovered"] == "covered"
    assert normalized_rows[1]["duration_bucket"] == "1h_to_3h"
    assert normalized_rows[1]["covered_vs_uncovered"] == "uncovered"


def test_invalid_schema_reports_validation_errors(tmp_path: Path) -> None:
    source = tmp_path / "invalid.csv"
    _write_csv(source, [{"foo": "bar"}])

    result = load_trade_source(source, source_role="paper", allow_runtime_read=True)

    assert result.source_status == "invalid_schema_or_empty_source"
    assert result.row_count_raw == 1
    assert result.row_count_normalized == 0
    assert result.validation_errors
    assert "missing_symbol" in result.validation_errors[0]
    assert "missing_pnl" in result.validation_errors[0]


def test_missing_and_unsupported_sources_are_blocked(tmp_path: Path) -> None:
    missing = load_trade_source(tmp_path / "missing.csv", source_role="paper", allow_runtime_read=True)
    assert missing.source_status == "missing_source"
    assert missing.source_exists is False

    unsupported_path = tmp_path / "paper.txt"
    unsupported_path.write_text("not supported", encoding="utf-8")
    unsupported = load_trade_source(unsupported_path, source_role="paper", allow_runtime_read=True)
    assert unsupported.source_status == "unsupported_source_type"
    assert unsupported.source_exists is True
    assert unsupported.source_hash_sha256 is not None


def test_gate_summary_has_no_failed_gates() -> None:
    report = build_paper_master_divergence_oos_real_source_loader_report(project_root=".")

    assert report["gate_summary"]["gate_count"] == 7
    assert report["gate_summary"]["passed_gate_count"] == 7
    assert report["gate_summary"]["failed_gate_count"] == 0
    assert report["gate_summary"]["critical_failed_gate_ids"] == []


def test_cli_no_write_json_default_does_not_create_report() -> None:
    command = [
        sys.executable,
        "scripts/build_paper_master_divergence_oos_real_source_loader_v1.py",
        "--project-root",
        ".",
        "--no-write",
        "--json",
    ]
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    report = json.loads(completed.stdout)

    assert report["schema_version"] == "paper_master_divergence_oos_real_source_loader_v1"
    assert report["write_performed"] is False
    assert report["output_path"] is None
    assert report["writes_reports"] is False
    assert report["writes_runtime"] is False
    assert report["writes_data"] is False


def test_cli_can_load_temp_sources_with_explicit_opt_in(tmp_path: Path) -> None:
    paper_source = tmp_path / "paper.csv"
    master_source = tmp_path / "master.csv"
    _write_csv(paper_source, _sample_rows())
    _write_csv(master_source, _sample_rows())

    command = [
        sys.executable,
        "scripts/build_paper_master_divergence_oos_real_source_loader_v1.py",
        "--project-root",
        ".",
        "--paper-source",
        str(paper_source),
        "--master-source",
        str(master_source),
        "--allow-runtime-read",
        "--no-write",
        "--json",
    ]
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    report = json.loads(completed.stdout)

    assert report["real_sources_loaded"] is True
    assert report["input_mode"] == "real_sources_loaded_read_only"
    assert report["source_summary"]["paper"]["row_count_normalized"] == 2
    assert report["source_summary"]["master"]["row_count_normalized"] == 2
    assert report["write_performed"] is False


def test_include_loaded_rows_is_opt_in(tmp_path: Path) -> None:
    paper_source = tmp_path / "paper.csv"
    master_source = tmp_path / "master.csv"
    _write_csv(paper_source, _sample_rows())
    _write_csv(master_source, _sample_rows())

    without_rows = build_paper_master_divergence_oos_real_source_loader_report(
        paper_source=paper_source,
        master_source=master_source,
        allow_runtime_read=True,
        include_loaded_rows=False,
    )
    with_rows = build_paper_master_divergence_oos_real_source_loader_report(
        paper_source=paper_source,
        master_source=master_source,
        allow_runtime_read=True,
        include_loaded_rows=True,
    )

    assert "normalized_rows" not in without_rows["source_summary"]["paper"]
    assert len(with_rows["source_summary"]["paper"]["normalized_rows"]) == 2
