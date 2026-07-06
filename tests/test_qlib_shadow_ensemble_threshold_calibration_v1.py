from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.qlib_shadow_ensemble_threshold_calibration.calibrator import (
    build_qlib_shadow_ensemble_threshold_calibration_v1,
    render_markdown,
)


SCRIPT = Path("scripts/build_qlib_shadow_ensemble_threshold_calibration_v1.py")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def create_required_reports(project_root: Path, *, include_optional: bool = True) -> None:
    reports = project_root / "data" / "reports"
    write_json(
        reports / "financial_label_target_store_v1.json",
        {
            "status": "ok",
            "target_records": [
                {
                    "order_id": "o-1",
                    "symbol_norm": "BTCUSDT",
                    "side": "long",
                    "target_expected_value_component": 2.0,
                    "qlib_score": 0.9,
                },
                {
                    "order_id": "o-2",
                    "symbol_norm": "ETHUSDT",
                    "side": "short",
                    "target_expected_value_component": -1.0,
                    "qlib_score": 0.2,
                },
                {
                    "order_id": "o-3",
                    "symbol_norm": "BTCUSDT",
                    "side": "short",
                    "target_expected_value_component": 1.5,
                    "qlib_score": 0.7,
                },
                {
                    "order_id": "o-4",
                    "symbol_norm": "ETHUSDT",
                    "side": "long",
                    "target_expected_value_component": -0.5,
                    "qlib_score": 0.4,
                },
            ],
            "lineage_hashes": {"target_store_hash": "target-hash"},
        },
    )
    write_json(
        reports / "paper_autotrain_feedback_loop_v1.json",
        {"status": "ok", "decision": "MANTER_EM_RESEARCH", "dataset_hash": "dataset-hash"},
    )
    write_json(
        reports / "ai_qlib_drift_regime_monitor_v1.json",
        {"status": "blocked", "reason": "research_only_drift_monitor", "feature_contract_hash": "feature-hash"},
    )
    write_json(
        reports / "event_driven_backtest_execution_cost_gate_v1.json",
        {"status": "blocked", "reason": "execution_cost_gate_blocked"},
    )
    if include_optional:
        write_json(
            reports / "ai_shadow_quality_veto_trainer_v1.json",
            {
                "status": "ok",
                "decision_sample": [
                    {"order_id": "o-1", "probability_quality": 0.8},
                    {"order_id": "o-2", "probability_quality": 0.1},
                    {"order_id": "o-3", "probability_quality": 0.6},
                    {"order_id": "o-4", "probability_quality": 0.3},
                ],
            },
        )
        write_json(
            reports / "qlib_institutional_ranking_trainer_v1.json",
            {"status": "ok", "split_count": 3},
        )


def test_missing_essential_reports_block_safely(tmp_path: Path) -> None:
    report = build_qlib_shadow_ensemble_threshold_calibration_v1(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "ensemble_threshold_calibration_blocked"
    assert any(str(blocker).startswith("missing_required_source:") for blocker in report["blockers"])
    assert report["write_performed"] is False


def test_calibration_builds_deterministic_threshold_grid(tmp_path: Path) -> None:
    create_required_reports(tmp_path)

    first = build_qlib_shadow_ensemble_threshold_calibration_v1(project_root=tmp_path)
    second = build_qlib_shadow_ensemble_threshold_calibration_v1(project_root=tmp_path)

    assert first["status"] == "ok"
    assert first["threshold_grid"] == second["threshold_grid"]
    assert first["recommended_candidate"] == second["recommended_candidate"]
    assert first["calibration_row_count"] == 4


def test_threshold_metrics_are_calculated(tmp_path: Path) -> None:
    create_required_reports(tmp_path)

    report = build_qlib_shadow_ensemble_threshold_calibration_v1(project_root=tmp_path)
    row = next(item for item in report["threshold_grid"] if item["threshold"] == 0.5)

    assert row["selected_count"] == 2
    assert row["accepted_count"] == 2
    assert row["rejected_count"] == 2
    assert row["pnl_selected"] == 3.5
    assert row["pnl_rejected"] == -1.5
    assert row["precision_proxy"] == 1.0
    assert row["recall_proxy"] == 1.0
    assert row["average_expected_value"] == 1.75


def test_recommended_candidate_is_research_only_and_not_applied(tmp_path: Path) -> None:
    create_required_reports(tmp_path)

    report = build_qlib_shadow_ensemble_threshold_calibration_v1(project_root=tmp_path)
    candidate = report["recommended_candidate"]

    assert candidate["candidate_decision"] == "MANTER_EM_RESEARCH"
    assert candidate["recommended_for_runtime"] is False
    assert candidate["thresholds_applied"] is False
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["thresholds_applied"] is False
    assert report["release_allowed"] is False


def test_domain_builder_never_writes_runtime_files(tmp_path: Path) -> None:
    create_required_reports(tmp_path)

    report = build_qlib_shadow_ensemble_threshold_calibration_v1(project_root=tmp_path, write=True)

    assert report["write_requested"] is True
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "qlib_shadow_ensemble_threshold_calibration_v1.json").exists()
    assert not (tmp_path / "data" / "models").exists()
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.sqlite"))


def test_optional_sources_missing_are_reported_without_false_runtime_update(tmp_path: Path) -> None:
    create_required_reports(tmp_path, include_optional=False)

    report = build_qlib_shadow_ensemble_threshold_calibration_v1(project_root=tmp_path)

    assert report["status"] == "warning"
    assert any("missing_optional_source:" in warning for warning in report["warnings"])
    assert report["updates_qlib_runtime"] is False
    assert report["updates_ai_shadow_thresholds"] is False


def test_safety_flags_preserve_shadow_research_boundaries(tmp_path: Path) -> None:
    create_required_reports(tmp_path)

    report = build_qlib_shadow_ensemble_threshold_calibration_v1(project_root=tmp_path)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["operational_authority"] is False
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["writes_registry"] is False
    assert report["registry_write_performed"] is False
    assert report["runs_training"] is False
    assert report["promotes_model"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False


def test_markdown_report_contains_main_sections(tmp_path: Path) -> None:
    create_required_reports(tmp_path)
    report = build_qlib_shadow_ensemble_threshold_calibration_v1(project_root=tmp_path)

    markdown = render_markdown(report)

    assert "# Qlib Shadow Ensemble Threshold Calibration V1" in markdown
    assert "## Executive Summary" in markdown
    assert "## Threshold Grid" in markdown
    assert "## Safety Invariants" in markdown


def test_cli_json_executes_no_write(tmp_path: Path) -> None:
    create_required_reports(tmp_path)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["status"] == "ok"
    assert payload["write_performed"] is False
    assert payload["thresholds_applied"] is False
    assert not (tmp_path / "data" / "reports" / "qlib_shadow_ensemble_threshold_calibration_v1.json").exists()


def test_cli_write_only_materializes_research_reports(tmp_path: Path) -> None:
    create_required_reports(tmp_path)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--write", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    json_report = tmp_path / "data" / "reports" / "qlib_shadow_ensemble_threshold_calibration_v1.json"
    markdown_report = tmp_path / "data" / "reports" / "qlib_shadow_ensemble_threshold_calibration_v1.md"

    assert payload["write_performed"] is True
    assert json_report.is_file()
    assert markdown_report.is_file()
    assert not (tmp_path / "data" / "models").exists()
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.sqlite"))


def test_report_is_json_serializable(tmp_path: Path) -> None:
    create_required_reports(tmp_path)

    report = build_qlib_shadow_ensemble_threshold_calibration_v1(project_root=tmp_path)

    encoded = json.dumps(report, sort_keys=True)
    assert "qlib_shadow_ensemble_threshold_calibration_v1" in encoded
