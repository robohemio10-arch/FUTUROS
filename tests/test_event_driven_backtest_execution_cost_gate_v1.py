from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.event_driven_backtest_execution_cost_gate import (
    build_event_driven_backtest_execution_cost_gate_v1,
)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def create_required_reports(root: Path, *, qlib_ev: float = 2.0, shadow_ev: float = 5.0) -> None:
    reports = root / "data" / "reports"
    write_json(
        reports / "qlib_institutional_ranking_trainer_v1.json",
        {
            "status": "ok",
            "reason": "research_challenger_trained",
            "dataset_hash": "dataset-hash",
            "feature_contract_hash": "feature-hash",
            "metrics_by_split": [
                {"split_id": "wf_split_001", "selected_top_k_expected_value": qlib_ev, "test_row_count": 10},
                {"split_id": "wf_split_002", "selected_top_k_expected_value": qlib_ev, "test_row_count": 10},
            ],
            "aggregate_metrics": {"selected_top_k_expected_value_total": qlib_ev * 2},
        },
    )
    write_json(
        reports / "ai_shadow_quality_veto_trainer_v1.json",
        {
            "status": "ok",
            "reason": "quality_veto_challenger_trained_research_only",
            "dataset_hash": "dataset-hash",
            "feature_contract_hash": "feature-hash",
            "metrics_by_split": [
                {
                    "split_id": "wf_split_001",
                    "net_ev_delta_if_applied_research_only": shadow_ev,
                    "test_row_count": 10,
                },
                {
                    "split_id": "wf_split_002",
                    "net_ev_delta_if_applied_research_only": shadow_ev,
                    "test_row_count": 10,
                },
            ],
            "aggregate_metrics": {"net_ev_delta_if_applied_research_only_total": shadow_ev * 2},
        },
    )
    write_json(
        reports / "walkforward_anti_leakage_split_engine_v1.json",
        {
            "schema_version": "walkforward_anti_leakage_split_engine_v1",
            "split_count": 2,
            "split_engine_hash": "split-hash",
            "baseline_summary": baseline_payload(),
        },
    )
    write_json(reports / "walkforward_baseline_summary_v1.json", baseline_payload())
    write_json(
        reports / "financial_label_target_store_v1.json",
        {
            "schema_version": "financial_label_target_store_v1",
            "row_count": 4,
            "target_store_hash": "target-hash",
            "target_records": [
                {"symbol_norm": "BTCUSDT", "side": "long", "target_net_pnl": 1.0},
                {"symbol_norm": "BTCUSDT", "side": "short", "target_net_pnl": -0.5},
                {"symbol_norm": "ETHUSDT", "side": "long", "target_net_pnl": 0.75},
                {"symbol_norm": "ETHUSDT", "side": "short", "target_net_pnl": -0.25},
            ],
        },
    )
    write_json(
        reports / "ai_qlib_drift_regime_monitor_v1.json",
        {
            "status": "blocked",
            "reason": "critical_drift_or_missing_required_sources",
            "decision": "MANTER_EM_RESEARCH",
            "lineage_hashes": {"dataset_hash": "dataset-hash"},
        },
    )
    write_json(
        reports / "paper_autotrain_feedback_loop_v1.json",
        {
            "status": "ok",
            "reason": "research_candidate_not_promoted",
            "decision": "MANTER_EM_RESEARCH",
            "warnings": [],
            "lineage_hashes": {"dataset_hash": "dataset-hash"},
        },
    )


def baseline_payload() -> dict[str, object]:
    return {
        "baseline_status": "ok",
        "baseline_row_count": 20,
        "no_trade_expected_value": 0.0,
        "random_deterministic_expected_value": 1.0,
        "always_long_expected_value": -2.0,
        "always_short_expected_value": 0.5,
    }


def test_default_no_write_does_not_create_files(tmp_path: Path) -> None:
    create_required_reports(tmp_path)
    report = build_event_driven_backtest_execution_cost_gate_v1(project_root=tmp_path)
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "event_driven_backtest_execution_cost_gate_v1.json").exists()


def test_write_report_creates_only_json_and_markdown(tmp_path: Path) -> None:
    create_required_reports(tmp_path)
    report = build_event_driven_backtest_execution_cost_gate_v1(project_root=tmp_path, write_report=True)
    assert report["write_performed"] is True
    reports = tmp_path / "data" / "reports"
    assert (reports / "event_driven_backtest_execution_cost_gate_v1.json").exists()
    assert (reports / "event_driven_backtest_execution_cost_gate_v1.md").exists()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "data" / "models").exists()
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))


def test_cli_no_write_precedence_over_write_report(tmp_path: Path) -> None:
    create_required_reports(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_event_driven_backtest_execution_cost_gate_v1.py",
            "--project-root",
            str(tmp_path),
            "--write-report",
            "--no-write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "event_driven_backtest_execution_cost_gate_v1.json").exists()


def test_missing_required_sources_return_blocked(tmp_path: Path) -> None:
    report = build_event_driven_backtest_execution_cost_gate_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert "missing_required_source:data/reports/qlib_institutional_ranking_trainer_v1.json" in report["blockers"]
    assert report["decision"] == "MANTER_EM_RESEARCH"


def test_execution_cost_transforms_gross_ev_to_net_ev(tmp_path: Path) -> None:
    create_required_reports(tmp_path, qlib_ev=10.0, shadow_ev=10.0)
    report = build_event_driven_backtest_execution_cost_gate_v1(
        project_root=tmp_path,
        maker_fee_bps=1,
        taker_fee_bps=2,
        slippage_bps=1,
        spread_bps=1,
    )
    first = report["split_cost_gate_results"][0]
    assert report["execution_cost_model"]["round_trip_cost_bps"] == 6.0
    assert first["estimated_execution_cost"] == 0.006
    assert first["net_expected_value"] == 9.994
    assert first["net_expected_value_delta"] == -0.006


def test_net_ev_negative_generates_blocker(tmp_path: Path) -> None:
    create_required_reports(tmp_path, qlib_ev=-1.0, shadow_ev=-0.5)
    report = build_event_driven_backtest_execution_cost_gate_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert any("net_expected_value_non_positive" in blocker for blocker in report["blockers"])


def test_fee_slippage_and_spread_enter_total_cost(tmp_path: Path) -> None:
    create_required_reports(tmp_path)
    report = build_event_driven_backtest_execution_cost_gate_v1(
        project_root=tmp_path,
        maker_fee_bps=2,
        taker_fee_bps=4,
        slippage_bps=3,
        spread_bps=5,
        funding_bps_per_position=1,
    )
    assert report["execution_cost_model"]["round_trip_cost_bps"] == 18.0
    assert report["execution_cost_model"]["funding_unavailable"] is False


def test_safety_flags_remain_non_operational(tmp_path: Path) -> None:
    create_required_reports(tmp_path)
    report = build_event_driven_backtest_execution_cost_gate_v1(project_root=tmp_path)
    for key, value in report["safety_flags"].items():
        if key in {"paper_only", "shadow_only", "research_only", "read_only"}:
            assert value is True
        else:
            assert value is False
    assert report["release_allowed"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["updates_risk_manager"] is False


def test_json_serializable(tmp_path: Path) -> None:
    create_required_reports(tmp_path)
    report = build_event_driven_backtest_execution_cost_gate_v1(project_root=tmp_path)
    payload = json.loads(json.dumps(report, sort_keys=True, default=str))
    assert payload["schema_version"] == "event_driven_backtest_execution_cost_gate_v1"


def test_group_results_by_symbol_and_side_when_target_records_exist(tmp_path: Path) -> None:
    create_required_reports(tmp_path)
    report = build_event_driven_backtest_execution_cost_gate_v1(project_root=tmp_path)
    assert {row["symbol"] for row in report["symbol_cost_gate_results"]} == {"BTCUSDT", "ETHUSDT"}
    assert {row["side"] for row in report["side_cost_gate_results"]} == {"long", "short"}
