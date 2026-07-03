from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.ai_qlib_drift_regime_monitor import build_ai_qlib_drift_regime_monitor_v1


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def safe_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "operational_authority": False,
        "readiness_release_authority": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "changes_model": False,
        "model_promotion_performed": False,
        "registry_write_performed": False,
        "active_model_changed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }


def create_stable_reports(root: Path) -> None:
    reports = root / "data" / "reports"
    features = ["feature_a", "feature_b"]
    write_json(
        reports / "ai_unified_feature_contract_v1.json",
        {
            "schema_version": "ai_unified_feature_contract_v1",
            "validation_status": "ok",
            "contract_hash": "feature-contract-hash",
            "feature_columns": features,
            "safety_flags": safe_flags(),
        },
    )
    write_json(
        reports / "ai_unified_dataset_manifest_v1.json",
        {
            "schema_version": "ai_unified_dataset_manifest_v1",
            "validation_status": "ok",
            "dataset_hash": "dataset-hash",
            "feature_contract_hash": "feature-contract-hash",
            "row_count": 100,
            "selected_training_dataset_rows": 100,
            "selected_training_dataset_columns": [*features, "target_win_loss"],
            "null_counts": {"feature_a": 0, "feature_b": 2},
            "label_distribution": {"0": 40, "1": 60},
            "feature_statistics": {
                "feature_a": {"reference_mean": 10.0, "current_mean": 10.1, "reference_std": 2.0},
                "feature_b": {"reference_mean": 3.0, "current_mean": 3.1, "reference_std": 1.0},
            },
            "safety_flags": safe_flags(),
        },
    )
    write_json(
        reports / "financial_label_target_store_v1.json",
        {
            "schema_version": "financial_label_target_store_v1",
            "validation_status": "ok",
            "dataset_hash": "dataset-hash",
            "feature_contract_hash": "feature-contract-hash",
            "target_store_hash": "target-store-hash",
            "row_count": 100,
            "target_columns": ["target_win_loss", "target_net_pnl"],
            "label_distribution": {"0": 40, "1": 60},
            "safety_flags": safe_flags(),
        },
    )
    write_json(
        reports / "walkforward_anti_leakage_split_engine_v1.json",
        {
            "schema_version": "walkforward_anti_leakage_split_engine_v1",
            "validation_status": "ok",
            "dataset_hash": "dataset-hash",
            "feature_contract_hash": "feature-contract-hash",
            "target_store_hash": "target-store-hash",
            "split_engine_hash": "split-engine-hash",
            "split_count": 3,
            "splits": [
                {"split_id": "wf_split_001", "test_row_count": 30},
                {"split_id": "wf_split_002", "test_row_count": 30},
                {"split_id": "wf_split_003", "test_row_count": 30},
            ],
            "leakage_audit": {"leakage_status": "ok"},
            "safety_flags": safe_flags(),
        },
    )
    write_json(
        reports / "walkforward_baseline_summary_v1.json",
        {"baseline_status": "ok", "baseline_row_count": 100},
    )
    write_json(
        reports / "qlib_institutional_ranking_trainer_v1.json",
        {
            "schema_version": "qlib_institutional_ranking_trainer_v1",
            "status": "ok",
            "reason": "research_challenger_trained",
            "dataset_hash": "dataset-hash",
            "feature_contract_hash": "feature-contract-hash",
            "target_store_hash": "target-store-hash",
            "split_engine_hash": "split-engine-hash",
            "split_count": 3,
            "metrics_by_split": [
                {"split_id": "wf_split_001", "rank_ic": 0.21, "precision_at_10": 0.5, "selected_top_k_expected_value": 1.0},
                {"split_id": "wf_split_002", "rank_ic": 0.22, "precision_at_10": 0.51, "selected_top_k_expected_value": 1.1},
                {"split_id": "wf_split_003", "rank_ic": 0.2, "precision_at_10": 0.49, "selected_top_k_expected_value": 1.05},
            ],
            "aggregate_metrics": {"mean_rank_ic": 0.21, "mean_precision_at_10": 0.5},
            "qlib_training_performed": True,
            **safe_flags(),
            "safety_flags": safe_flags(),
        },
    )
    write_json(
        reports / "ai_shadow_quality_veto_trainer_v1.json",
        {
            "schema_version": "ai_shadow_quality_veto_trainer_v1",
            "status": "ok",
            "reason": "quality_veto_challenger_trained_research_only",
            "dataset_hash": "dataset-hash",
            "feature_contract_hash": "feature-contract-hash",
            "target_store_hash": "target-store-hash",
            "split_engine_hash": "split-engine-hash",
            "split_count": 3,
            "metrics_by_split": [
                {
                    "split_id": "wf_split_001",
                    "net_ev_delta_if_applied_research_only": 10.0,
                    "precision_reject": 0.6,
                    "recall_reject": 0.7,
                },
                {
                    "split_id": "wf_split_002",
                    "net_ev_delta_if_applied_research_only": 10.2,
                    "precision_reject": 0.61,
                    "recall_reject": 0.69,
                },
                {
                    "split_id": "wf_split_003",
                    "net_ev_delta_if_applied_research_only": 9.9,
                    "precision_reject": 0.59,
                    "recall_reject": 0.7,
                },
            ],
            "aggregate_metrics": {"net_ev_delta_if_applied_research_only_total": 30.1},
            "ai_shadow_challenger_training_performed": True,
            **safe_flags(),
            "safety_flags": safe_flags(),
        },
    )
    write_json(
        reports / "paper_autotrain_feedback_loop_v1.json",
        {
            "schema_version": "paper_autotrain_feedback_loop_v1",
            "status": "ok",
            "decision": "MANTER_EM_RESEARCH",
            "lineage_hashes": {"dataset_hash": "dataset-hash"},
            "safety_flags": safe_flags(),
        },
    )


def test_default_no_write(tmp_path: Path) -> None:
    create_stable_reports(tmp_path)
    report = build_ai_qlib_drift_regime_monitor_v1(project_root=tmp_path)
    assert report["status"] == "ok"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "ai_qlib_drift_regime_monitor_v1.json").exists()


def test_write_report_writes_only_allowed_report_paths(tmp_path: Path) -> None:
    create_stable_reports(tmp_path)
    report = build_ai_qlib_drift_regime_monitor_v1(project_root=tmp_path, write_report=True)
    assert report["write_requested"] is True
    assert report["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "ai_qlib_drift_regime_monitor_v1.json").exists()
    assert (tmp_path / "data" / "reports" / "ai_qlib_drift_regime_monitor_v1.md").exists()
    assert not (tmp_path / "data" / "runtime").exists()


def test_missing_sources_generate_explicit_blockers(tmp_path: Path) -> None:
    report = build_ai_qlib_drift_regime_monitor_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert "missing_required_source:data/reports/ai_unified_feature_contract_v1.json" in report["blockers"]


def test_insufficient_data_generates_safe_warning(tmp_path: Path) -> None:
    create_stable_reports(tmp_path)
    (tmp_path / "data" / "reports" / "qlib_institutional_ranking_trainer_v1.json").unlink()
    (tmp_path / "data" / "reports" / "ai_shadow_quality_veto_trainer_v1.json").unlink()
    report = build_ai_qlib_drift_regime_monitor_v1(project_root=tmp_path)
    assert report["status"] == "warning"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert "qlib_metrics_drift_insufficient_data" in report["warnings"]
    assert "ai_shadow_quality_drift_insufficient_data" in report["warnings"]


def test_critical_drift_blocks_without_operational_authority(tmp_path: Path) -> None:
    create_stable_reports(tmp_path)
    qlib_path = tmp_path / "data" / "reports" / "qlib_institutional_ranking_trainer_v1.json"
    qlib = json.loads(qlib_path.read_text(encoding="utf-8"))
    qlib["metrics_by_split"] = [
        {"split_id": "wf_split_001", "rank_ic": 0.8, "precision_at_10": 0.8, "selected_top_k_expected_value": 10.0},
        {"split_id": "wf_split_002", "rank_ic": 0.5, "precision_at_10": 0.6, "selected_top_k_expected_value": 5.0},
        {"split_id": "wf_split_003", "rank_ic": 0.1, "precision_at_10": 0.2, "selected_top_k_expected_value": -2.0},
    ]
    write_json(qlib_path, qlib)
    report = build_ai_qlib_drift_regime_monitor_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert "qlib_rank_ic_drift_critical" in report["blockers"]
    assert report["operational_authority"] is False


def test_stable_scenario_returns_ok_and_research_decision(tmp_path: Path) -> None:
    create_stable_reports(tmp_path)
    report = build_ai_qlib_drift_regime_monitor_v1(project_root=tmp_path)
    assert report["status"] == "ok"
    assert report["reason"] == "drift_regime_stable_research_only"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["regime_summary"]["overall_regime"] == "stable"
    assert report["drift_summary"]["promotion_eligible"] is False


def test_qlib_metrics_drift_is_calculated_without_training(tmp_path: Path) -> None:
    create_stable_reports(tmp_path)
    report = build_ai_qlib_drift_regime_monitor_v1(project_root=tmp_path)
    qlib = report["qlib_performance_drift_section"]
    assert qlib["training_performed_by_monitor"] is False
    assert qlib["rank_ic_drift"]["split_count"] == 3
    assert qlib["precision_at_10_drift"]["metric"] == "precision_at_10"
    assert qlib["selected_top_k_expected_value_drift"]["latest"] == 1.05


def test_ai_shadow_drift_is_calculated_when_report_exists(tmp_path: Path) -> None:
    create_stable_reports(tmp_path)
    report = build_ai_qlib_drift_regime_monitor_v1(project_root=tmp_path)
    shadow = report["ai_shadow_quality_drift_section"]
    assert shadow["training_performed_by_monitor"] is False
    assert shadow["net_ev_delta_drift"]["split_count"] == 3
    assert shadow["precision_reject_drift"]["metric"] == "precision_reject"


def test_safety_flags_never_enable_operational_behavior(tmp_path: Path) -> None:
    create_stable_reports(tmp_path)
    report = build_ai_qlib_drift_regime_monitor_v1(project_root=tmp_path)
    for key, value in report["safety_flags"].items():
        if key in {"paper_only", "shadow_only", "research_only", "read_only"}:
            assert value is True
        else:
            assert value is False
    assert report["readiness_release_authority"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False


def test_output_is_json_serializable(tmp_path: Path) -> None:
    create_stable_reports(tmp_path)
    report = build_ai_qlib_drift_regime_monitor_v1(project_root=tmp_path)
    assert json.loads(json.dumps(report, sort_keys=True, default=str))["schema_version"] == "ai_qlib_drift_regime_monitor_v1"


def test_cli_json_executes(tmp_path: Path) -> None:
    create_stable_reports(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_ai_qlib_drift_regime_monitor_v1.py",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "ai_qlib_drift_regime_monitor_v1"
    assert payload["write_performed"] is False
    assert payload["decision"] == "MANTER_EM_RESEARCH"
