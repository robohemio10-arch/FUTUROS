from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.autolearning_closeout.closeout_report import build_closeout_report
from smartcrypto.learning.autolearning_closeout.evidence_loader import file_sha256

REPO_ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def make_project(root: Path) -> dict[str, Path]:
    reports = root / "data" / "reports"
    fc = "feature-contract-hash"
    ds = "dataset-hash"
    ts = "target-store-hash"
    sp = "split-engine-hash"
    paths = {
        "foundation": reports / "paper_autolearning_foundation_summary.json",
        "master": reports / "paper_feedback_master_consolidation_preview_v1.json",
        "feature": reports / "ai_unified_feature_contract_v1.json",
        "dataset": reports / "ai_unified_dataset_manifest_v1.json",
        "target": reports / "financial_label_target_store_v1.json",
        "target_summary": reports / "financial_label_target_store_summary_v1.json",
        "walkforward": reports / "walkforward_anti_leakage_split_engine_v1.json",
        "baseline": reports / "walkforward_baseline_summary_v1.json",
        "backend": reports / "qlib_research_backend_gate_v1.json",
        "qlib": reports / "qlib_institutional_ranking_trainer_v1.json",
        "shadow": reports / "ai_shadow_quality_veto_trainer_v1.json",
        "shadow_metrics": reports / "ai_shadow_quality_veto_metrics_v1.json",
        "manifest": root / "PROJECT_MANIFEST_CLEAN.json",
    }
    safe = {"paper_only": True, "shadow_only": True, "sends_orders": False, "exchange_private_access": False, "changes_risk": False}
    write_json(paths["foundation"], {"status": "ok", "reason": "paper_autolearning_foundation_loop_closed", "microbatch_rows": 10, **safe})
    write_json(paths["master"], {"status": "ok", "reason": "preview_ready", "accepted_rows": 10, **safe})
    write_json(paths["feature"], {"validation_status": "ok", "contract_hash": fc, "feature_columns": ["feature_a"], **safe})
    write_json(paths["dataset"], {"validation_status": "ok", "feature_contract_hash": fc, "dataset_hash": ds, "row_count": 10, **safe})
    write_json(paths["target"], {"validation_status": "ok", "feature_contract_hash": fc, "dataset_hash": ds, "target_store_hash": ts, "row_count": 10, **safe})
    write_json(paths["target_summary"], {"status": "ok", "reason": "summary_ready", "row_count": 10, **safe})
    write_json(
        paths["walkforward"],
        {
            "validation_status": "ok",
            "feature_contract_hash": fc,
            "dataset_hash": ds,
            "target_store_hash": ts,
            "split_engine_hash": sp,
            "split_count": 3,
            **safe,
        },
    )
    write_json(paths["baseline"], {"status": "ok", "reason": "baseline_ready", "split_count": 3, **safe})
    write_json(paths["backend"], {"status": "ok", "reason": "qlib_backend_unavailable", "qlib_backend_status": "unavailable", **safe})
    write_json(
        paths["qlib"],
        {
            "status": "blocked",
            "reason": "qlib_backend_unavailable",
            "feature_contract_hash": fc,
            "dataset_hash": ds,
            "target_store_hash": ts,
            "split_engine_hash": sp,
            "trainer_status": "blocked",
            "promotion_eligible": False,
            "registry_write_performed": False,
            "model_promotion_performed": False,
            "active_model_changed": False,
            "qlib_runtime_updated": False,
            **safe,
        },
    )
    qlib_hash = file_sha256(paths["qlib"])
    write_json(
        paths["shadow"],
        {
            "status": "ok",
            "reason": "quality_veto_challenger_trained_research_only",
            "trainer_status": "ok",
            "feature_contract_hash": fc,
            "dataset_hash": ds,
            "target_store_hash": ts,
            "split_engine_hash": sp,
            "qlib_trainer_report_hash": qlib_hash,
            "candidate_decision": "MANTER_EM_RESEARCH",
            "aggregate_metrics": {"net_ev_delta_if_applied_research_only_total": 12.5},
            "promotion_eligible": False,
            "registry_write_performed": False,
            "model_promotion_performed": False,
            "active_model_changed": False,
            "ai_shadow_runtime_updated": False,
            "veto_runtime_active": False,
            **safe,
        },
    )
    write_json(paths["shadow_metrics"], {"schema_version": "ai_shadow_quality_veto_metrics_v1", "candidate_decision": "MANTER_EM_RESEARCH"})
    write_json(paths["manifest"], {"status": "ok", "aggregate_sha256": "manifest"})
    return paths


def test_default_no_write(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["status"] == "ok"
    assert report["canonical_loop_decision"] == "CANONICAL_RESEARCH_LOOP_CLOSED"
    assert report["write_performed"] is False
    assert not (tmp_path / "data/reports/autolearning_canonical_loop_closeout_evidence_v1.json").exists()


def test_write_outputs_only_reports(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path, write=True)
    assert report["write_performed"] is True
    for path in report["output_paths"].values():
        output = Path(path)
        assert output.exists()
        assert output.suffix in {".json", ".md"}
        assert "data\\reports" in str(output) or "data/reports" in str(output)


def test_blocks_when_required_evidence_missing(tmp_path: Path) -> None:
    paths = make_project(tmp_path)
    paths["target"].unlink()
    report = build_closeout_report(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["canonical_loop_decision"] == "BLOCKED_MISSING_EVIDENCE"
    assert "data/reports/financial_label_target_store_v1.json" in report["missing_evidence_sources"]


def test_detects_lineage_drift(tmp_path: Path) -> None:
    paths = make_project(tmp_path)
    payload = json.loads(paths["target"].read_text(encoding="utf-8"))
    payload["dataset_hash"] = "drifted"
    write_json(paths["target"], payload)
    report = build_closeout_report(project_root=tmp_path)
    assert report["canonical_loop_decision"] == "BLOCKED_LINEAGE_DRIFT"
    assert report["lineage_drift_detected"] is True


def test_detects_safety_violation(tmp_path: Path) -> None:
    paths = make_project(tmp_path)
    payload = json.loads(paths["shadow"].read_text(encoding="utf-8"))
    payload["sends_orders"] = True
    write_json(paths["shadow"], payload)
    report = build_closeout_report(project_root=tmp_path)
    assert report["canonical_loop_decision"] == "BLOCKED_SAFETY_VIOLATION"
    assert report["safety_status"] == "blocked"


def test_stage_matrix_contains_all_canonical_stages(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    stage_ids = {row["stage_id"] for row in report["stage_matrix"]}
    assert {
        "foundation_loop",
        "master_consolidation",
        "scheduler",
        "feature_contract",
        "dataset_manifest",
        "target_store",
        "walkforward_split",
        "qlib_trainer",
        "ai_shadow_trainer",
    }.issubset(stage_ids)


def test_lineage_matrix_contains_required_hashes(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["feature_contract_hash"] == "feature-contract-hash"
    assert report["dataset_hash"] == "dataset-hash"
    assert report["target_store_hash"] == "target-store-hash"
    assert report["split_engine_hash"] == "split-engine-hash"
    assert report["lineage_matrix"]


def test_safety_matrix_blocks_operational_authority(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["operational_authority"] is False
    assert all(row["actual"] is False for row in report["safety_matrix"] if row["flag"] == "operational_authority")


def test_ai_shadow_metrics_are_summarized_research_only(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["ai_shadow_candidate_decision"] == "MANTER_EM_RESEARCH"
    assert report["ai_shadow_net_ev_delta_if_applied_research_only_total"] == 12.5
    assert report["ai_shadow_veto_runtime_active"] is False


def test_qlib_backend_unavailable_is_warning_not_failure(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["canonical_loop_status"] == "ok"
    assert report["warning_stage_count"] >= 1
    assert report["qlib_backend_status"] == "unavailable"


def test_no_training_performed(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["training_performed"] is False
    assert report["qlib_training_performed"] is False
    assert report["ai_shadow_training_performed"] is False


def test_no_registry_write(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["registry_write_performed"] is False
    assert report["veto_registry_write_performed"] is False


def test_no_model_promotion(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["model_promotion_performed"] is False
    assert report["promotion_eligible"] is False


def test_no_active_model_change(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["active_model_changed"] is False


def test_no_runtime_update(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["writes_runtime"] is False
    assert report["qlib_runtime_updated"] is False
    assert report["ai_shadow_runtime_updated"] is False


def test_no_veto_runtime_active(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["veto_runtime_active"] is False
    assert report["ai_shadow_veto_runtime_active"] is False


def test_no_exchange_private_access(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["exchange_private_access"] is False


def test_no_orders_sent(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["sends_orders"] is False


def test_no_sqlite_parquet_writes(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False


def test_cli_no_write_json_executes(tmp_path: Path) -> None:
    make_project(tmp_path)
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/build_autolearning_canonical_loop_closeout_evidence_v1.py"), "--project-root", str(tmp_path), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["write_performed"] is False


def test_cli_write_json_executes(tmp_path: Path) -> None:
    make_project(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/build_autolearning_canonical_loop_closeout_evidence_v1.py"),
            "--project-root",
            str(tmp_path),
            "--write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["write_performed"] is True
    assert (tmp_path / "data/reports/autolearning_canonical_loop_closeout_evidence_v1.json").exists()


def test_safety_flags_preserved(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_closeout_report(project_root=tmp_path)
    expected_false = [
        "operational_authority",
        "paper_observation_allowed",
        "ready_for_shadow_observation",
        "promotion_eligible",
        "registry_write_performed",
        "model_promotion_performed",
        "active_model_changed",
        "qlib_runtime_updated",
        "ai_shadow_runtime_updated",
        "sends_orders",
        "exchange_private_access",
        "writes_runtime",
        "writes_sqlite",
        "writes_parquet",
    ]
    for key in expected_false:
        assert report[key] is False
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
