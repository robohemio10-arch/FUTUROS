from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_master_divergence_oos_real_slice_computation.real_slice_computation import (
    OOS_SLICE_DIMENSIONS,
    build_oos_real_slice_computation_report,
    compute_oos_real_slice_metrics,
    normalize_trade_row,
)


def _fixture_rows() -> list[dict[str, object]]:
    return [
        {
            "symbol": "ETH/USDT:USDT",
            "side": "long",
            "exit_reason": "stop_loss",
            "open_time": "2026-06-05T10:00:00Z",
            "close_time": "2026-06-05T10:12:00Z",
            "pnl": -2.5,
            "lb_10m_ret_close": -0.005,
            "lb_30m_ret_close": -0.008,
            "covered_feature_subset": True,
        },
        {
            "symbol": "ETH/USDT:USDT",
            "side": "long",
            "exit_reason": "roi",
            "open_time": "2026-06-06T10:00:00Z",
            "close_time": "2026-06-06T11:15:00Z",
            "pnl": 1.2,
            "lb_10m_ret_close": -0.001,
            "lb_30m_ret_close": -0.002,
            "covered_feature_subset": True,
        },
        {
            "symbol": "BTC/USDT:USDT",
            "side": "short",
            "exit_reason": "stop_loss",
            "open_time": "2026-06-06T12:00:00Z",
            "close_time": "2026-06-06T12:45:00Z",
            "pnl": -0.7,
            "lb_10m_ret_close": -0.006,
            "lb_30m_ret_close": -0.007,
            "covered_feature_subset": True,
        },
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "exit_reason": "roi",
            "open_time": "2026-06-07T12:00:00Z",
            "close_time": "2026-06-07T14:15:00Z",
            "pnl": 2.0,
            "covered_feature_subset": False,
        },
    ]


def test_default_report_is_blocked_without_runtime_rows() -> None:
    report = build_oos_real_slice_computation_report(project_root=".", allow_runtime_read=False)

    assert report["schema_version"] == "paper_master_divergence_oos_real_slice_computation_v1"
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["allow_runtime_read"] is False
    assert report["real_sources_loaded"] is False
    assert report["real_slice_metrics_created"] is True
    assert report["real_slice_metrics_computed"] is False
    assert report["oos_slice_metrics_computed"] is False
    assert report["oos_validation_required"] is True
    assert report["oos_validated"] is False
    assert report["ready_for_candidate_registry"] is False
    assert report["remediation_application_allowed"] is False


def test_default_report_preserves_all_safety_flags() -> None:
    report = build_oos_real_slice_computation_report(project_root=".")

    safety_flags = [
        "research_only",
        "read_only",
        "paper_only",
        "shadow_only",
    ]
    for flag in safety_flags:
        assert report[flag] is True

    blocked_flags = [
        "operational_authority",
        "can_apply_to_freqtrade",
        "can_apply_to_risk_manager",
        "can_promote_rules",
        "can_promote_model",
        "updates_freqtrade",
        "updates_risk_manager",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "sends_orders",
        "exchange_private_access",
        "live_trading_enabled",
        "live_release_allowed",
        "canary_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "writes_data",
        "writes_parquet",
        "writes_reports",
        "writes_runtime",
        "writes_sqlite",
    ]
    for flag in blocked_flags:
        assert report[flag] is False


def test_normalize_trade_row_derives_day_duration_and_bucket() -> None:
    record = normalize_trade_row(_fixture_rows()[0], source="paper")

    assert record.source == "paper"
    assert record.symbol == "ETH/USDT:USDT"
    assert record.side == "long"
    assert record.exit_reason == "stop_loss"
    assert record.day == "2026-06-05"
    assert record.duration_minutes == 12.0
    assert record.duration_bucket == "<15m"
    assert record.pnl == -2.5
    assert record.covered_feature_subset is True


def test_compute_oos_real_slice_metrics_covers_required_dimensions() -> None:
    records = [normalize_trade_row(row, source="paper") for row in _fixture_rows()]
    metrics = compute_oos_real_slice_metrics(records)

    assert metrics
    first = metrics[0]
    assert set(first["slice"]) == set(OOS_SLICE_DIMENSIONS)
    assert {row["hypothesis_id"] for row in metrics} == {"H1", "H2", "H6"}


def test_h1_fast_stop_metrics_remove_loser_without_touching_roi_winner() -> None:
    records = [normalize_trade_row(row, source="paper") for row in _fixture_rows()]
    metrics = compute_oos_real_slice_metrics(records)
    h1_eth_fast_stop = [
        row
        for row in metrics
        if row["hypothesis_id"] == "H1"
        and row["slice"]["symbol"] == "ETH/USDT:USDT"
        and row["slice"]["exit_reason"] == "stop_loss"
        and row["slice"]["duration_bucket"] == "<15m"
    ][0]

    row_metrics = h1_eth_fast_stop["metrics"]
    assert row_metrics["trade_count"] == 1
    assert row_metrics["triggered_count"] == 1
    assert row_metrics["true_positive_count"] == 1
    assert row_metrics["false_positive_count"] == 0
    assert row_metrics["simulated_removed_pnl_delta"] == 2.5
    assert row_metrics["winner_pnl_removed"] == 0


def test_h2_eth_long_metrics_are_slice_specific() -> None:
    records = [normalize_trade_row(row, source="paper") for row in _fixture_rows()]
    metrics = compute_oos_real_slice_metrics(records)
    eth_h2_rows = [
        row
        for row in metrics
        if row["hypothesis_id"] == "H2"
        and row["slice"]["symbol"] == "ETH/USDT:USDT"
    ]

    assert eth_h2_rows
    assert all(row["metrics"]["triggered_count"] == row["metrics"]["trade_count"] for row in eth_h2_rows)


def test_h6_candidate_rule_uses_feature_thresholds() -> None:
    records = [normalize_trade_row(row, source="paper") for row in _fixture_rows()]
    metrics = compute_oos_real_slice_metrics(records)
    h6_rows = [row for row in metrics if row["hypothesis_id"] == "H6"]
    triggered = sum(row["metrics"]["triggered_count"] for row in h6_rows)

    assert triggered == 2


def test_report_with_explicit_csv_sources_computes_metrics(tmp_path: Path) -> None:
    paper_csv = tmp_path / "paper.csv"
    paper_csv.write_text(
        "\n".join(
            [
                "symbol,side,exit_reason,open_time,close_time,pnl,lb_10m_ret_close,lb_30m_ret_close,covered_feature_subset",
                "ETH/USDT:USDT,long,stop_loss,2026-06-05T10:00:00Z,2026-06-05T10:12:00Z,-2.5,-0.005,-0.008,true",
                "BTC/USDT:USDT,short,roi,2026-06-06T10:00:00Z,2026-06-06T11:12:00Z,1.0,-0.001,-0.001,true",
            ]
        ),
        encoding="utf-8",
    )
    master_csv = tmp_path / "master.csv"
    master_csv.write_text(
        "\n".join(
            [
                "symbol,side,exit_reason,open_time,close_time,pnl",
                "ETH/USDT:USDT,long,roi,2026-06-05T10:00:00Z,2026-06-05T10:30:00Z,3.0",
            ]
        ),
        encoding="utf-8",
    )

    report = build_oos_real_slice_computation_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        paper_source=str(paper_csv),
        master_source=str(master_csv),
    )

    assert report["input_mode"] == "real_sources_loaded_read_only"
    assert report["real_sources_loaded"] is True
    assert report["paper_source_rows"] == 2
    assert report["master_source_rows"] == 1
    assert report["real_slice_metrics_computed"] is True
    assert report["slice_count"] > 0
    assert report["write_performed"] is False
    assert report["ready_for_candidate_registry"] is False


def test_report_with_sources_still_blocks_promotion(tmp_path: Path) -> None:
    paper_csv = tmp_path / "paper.csv"
    paper_csv.write_text(
        "symbol,side,exit_reason,open_time,close_time,pnl\n"
        "ETH/USDT:USDT,long,stop_loss,2026-06-05T10:00:00Z,2026-06-05T10:12:00Z,-2.5\\n",
        encoding="utf-8",
    )

    report = build_oos_real_slice_computation_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        paper_source=str(paper_csv),
    )

    assert report["real_slice_metrics_computed"] is True
    assert report["oos_validation_required"] is True
    assert report["oos_validated"] is False
    assert report["can_promote_rules"] is False
    assert report["can_promote_model"] is False
    assert report["remediation_application_allowed"] is False


def test_gate_summary_has_no_failures() -> None:
    report = build_oos_real_slice_computation_report(project_root=".")

    assert report["gate_summary"]["gate_count"] == 7
    assert report["gate_summary"]["failed_gate_count"] == 0
    assert report["gate_summary"]["critical_failed_gate_ids"] == []


def test_cli_json_no_write_mode() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_paper_master_divergence_oos_real_slice_computation_v1.py",
            "--project-root",
            ".",
            "--no-write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == "paper_master_divergence_oos_real_slice_computation_v1"
    assert payload["status"] == "blocked"
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert payload["sends_orders"] is False
