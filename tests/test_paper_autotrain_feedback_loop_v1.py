from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from smartcrypto.learning.paper_autotrain_feedback_loop import build_paper_autotrain_feedback_loop_v1

REPO_ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def make_project(root: Path, *, qlib_backend_status: str = "available") -> None:
    reports = root / "data" / "reports"
    safe = {
        "paper_only": True,
        "shadow_only": True,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "registry_write_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
    }
    write_json(reports / "paper_feedback_master_consolidation_preview_v1.json", {"status": "ok", "reason": "preview_ready", **safe})
    write_json(reports / "paper_autolearning_foundation_summary.json", {"status": "ok", "reason": "foundation_ready", **safe})
    write_json(reports / "ai_unified_feature_contract_v1.json", {"validation_status": "ok", "contract_hash": "fc", "feature_columns": ["feature_a"], **safe})
    write_json(reports / "ai_unified_dataset_manifest_v1.json", {"validation_status": "ok", "dataset_hash": "ds", "row_count": 42, **safe})
    write_json(reports / "financial_label_target_store_v1.json", {"validation_status": "ok", "target_store_hash": "ts", "row_count": 42, "target_columns": ["target_expected_value_component"], **safe})
    write_json(reports / "walkforward_anti_leakage_split_engine_v1.json", {"validation_status": "ok", "split_engine_hash": "wf", "split_count": 3, **safe})
    write_json(
        reports / "qlib_research_backend_gate_v1.json",
        {
            "status": "ok",
            "reason": f"qlib_backend_{qlib_backend_status}",
            "qlib_backend_status": qlib_backend_status,
            "qlib_importable": qlib_backend_status == "available",
            "qlib_version": "0.9.7" if qlib_backend_status == "available" else None,
            **safe,
        },
    )
    write_json(
        reports / "qlib_institutional_ranking_trainer_v1.json",
        {
            "status": "ok",
            "reason": "dry_run_validated",
            "qlib_backend_status": qlib_backend_status,
            "qlib_importable": qlib_backend_status == "available",
            "qlib_version": "0.9.7" if qlib_backend_status == "available" else None,
            "trained_split_count": 0,
            "candidate_decision": "NOT_TRAINED_DRY_RUN",
            "aggregate_metrics": {},
            "metrics_by_split": [],
            "baseline_comparison": {},
            "qlib_training_performed": False,
            "qlib_challenger_training_performed": False,
            "qlib_runtime_updated": False,
            "promotion_eligible": False,
            **safe,
        },
    )
    write_json(
        reports / "ai_shadow_quality_veto_trainer_v1.json",
        {
            "status": "ok",
            "reason": "dry_run_validated",
            "candidate_decision": "NOT_TRAINED_DRY_RUN",
            "probability_output": "probability_quality",
            "aggregate_metrics": {},
            "metrics_by_split": [],
            "ai_shadow_challenger_training_performed": False,
            "ai_shadow_runtime_updated": False,
            "veto_registry_write_performed": False,
            "promotion_eligible": False,
            **safe,
        },
    )


def qlib_trainer_ok(**_: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "reason": "research_challenger_trained",
        "qlib_backend_status": "available",
        "qlib_importable": True,
        "qlib_version": "0.9.7",
        "trained_split_count": 3,
        "candidate_decision": "MANTER_EM_RESEARCH",
        "aggregate_metrics": {"selected_top_k_expected_value_total": -1.0},
        "metrics_by_split": [{}, {}, {}],
        "baseline_comparison": {"beats_no_trade_split_count": 0},
        "qlib_training_performed": True,
        "qlib_challenger_training_performed": True,
        "model_promotion_performed": False,
        "registry_write_performed": False,
        "qlib_runtime_updated": False,
        "sends_orders": False,
        "promotion_eligible": False,
    }


def ai_shadow_trainer_ok(**_: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "reason": "quality_veto_challenger_trained_research_only",
        "candidate_decision": "MANTER_EM_RESEARCH",
        "probability_output": "probability_quality",
        "aggregate_metrics": {"net_ev_delta_if_applied_research_only_total": 2.5},
        "metrics_by_split": [{}, {}, {}],
        "ai_shadow_challenger_training_performed": True,
        "registry_write_performed": False,
        "veto_registry_write_performed": False,
        "ai_shadow_runtime_updated": False,
        "active_model_changed": False,
        "sends_orders": False,
        "promotion_eligible": False,
    }


def raising_trainer(**_: Any) -> dict[str, Any]:
    raise AssertionError("trainer should not be called")


def test_default_no_write_does_not_train_qlib_or_ai_shadow(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_paper_autotrain_feedback_loop_v1(
        project_root=tmp_path,
        qlib_trainer=raising_trainer,
        ai_shadow_trainer=raising_trainer,
        generated_at_utc="2026-07-03T00:00:00+00:00",
    )
    assert report["write_performed"] is False
    assert report["qlib_section"]["qlib_training_performed"] is False
    assert report["ai_shadow_section"]["ai_shadow_training_performed"] is False


def test_default_no_write_does_not_write_report(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_paper_autotrain_feedback_loop_v1(project_root=tmp_path, generated_at_utc="2026-07-03T00:00:00+00:00")
    assert report["write_performed"] is False
    assert not (tmp_path / "data/reports/paper_autotrain_feedback_loop_v1.json").exists()


def test_write_report_writes_only_allowed_paths(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_paper_autotrain_feedback_loop_v1(project_root=tmp_path, write_report=True, generated_at_utc="2026-07-03T00:00:00+00:00")
    assert report["write_performed"] is True
    assert (tmp_path / "data/reports/paper_autotrain_feedback_loop_v1.json").exists()
    assert (tmp_path / "data/reports/paper_autotrain_feedback_loop_v1.md").exists()
    assert not (tmp_path / "data/models").exists()


def test_qlib_unavailable_generates_safe_warning(tmp_path: Path) -> None:
    make_project(tmp_path, qlib_backend_status="unavailable")
    report = build_paper_autotrain_feedback_loop_v1(project_root=tmp_path, generated_at_utc="2026-07-03T00:00:00+00:00")
    assert report["status"] == "warning"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert "qlib_backend_unavailable" in report["warnings"]


def test_qlib_available_train_research_only_preserves_safety(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_paper_autotrain_feedback_loop_v1(
        project_root=tmp_path,
        run_qlib_train=True,
        qlib_trainer=qlib_trainer_ok,
        generated_at_utc="2026-07-03T00:00:00+00:00",
    )
    assert report["qlib_section"]["qlib_training_performed"] is True
    assert report["qlib_section"]["registry_write_performed"] is False
    assert report["qlib_section"]["model_promotion_performed"] is False
    assert report["qlib_section"]["qlib_runtime_updated"] is False
    assert report["sends_orders"] is False


def test_candidate_that_does_not_beat_no_trade_stays_research(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_paper_autotrain_feedback_loop_v1(
        project_root=tmp_path,
        run_qlib_train=True,
        qlib_trainer=qlib_trainer_ok,
        generated_at_utc="2026-07-03T00:00:00+00:00",
    )
    assert report["qlib_section"]["beats_no_trade_split_count"] == 0
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["promotion_eligible"] is False


def test_ai_shadow_when_requested_does_not_update_runtime_or_registry(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_paper_autotrain_feedback_loop_v1(
        project_root=tmp_path,
        run_ai_shadow_train=True,
        ai_shadow_trainer=ai_shadow_trainer_ok,
        generated_at_utc="2026-07-03T00:00:00+00:00",
    )
    assert report["ai_shadow_section"]["ai_shadow_training_performed"] is True
    assert report["ai_shadow_section"]["registry_write_performed"] is False
    assert report["ai_shadow_section"]["ai_shadow_runtime_updated"] is False
    assert report["ai_shadow_section"]["active_model_changed"] is False


def test_output_is_json_serializable_and_deterministic_for_fixtures(tmp_path: Path) -> None:
    make_project(tmp_path)
    first = build_paper_autotrain_feedback_loop_v1(project_root=tmp_path, generated_at_utc="2026-07-03T00:00:00+00:00")
    second = build_paper_autotrain_feedback_loop_v1(project_root=tmp_path, generated_at_utc="2026-07-03T00:00:00+00:00")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_missing_minimum_input_generates_explicit_blocker(tmp_path: Path) -> None:
    report = build_paper_autotrain_feedback_loop_v1(project_root=tmp_path, generated_at_utc="2026-07-03T00:00:00+00:00")
    assert report["status"] == "blocked"
    assert report["blockers"]
    assert any(blocker.startswith("missing_required_source:") for blocker in report["blockers"])


def test_no_sends_orders_exchange_private_or_risk_change(tmp_path: Path) -> None:
    make_project(tmp_path)
    report = build_paper_autotrain_feedback_loop_v1(project_root=tmp_path, generated_at_utc="2026-07-03T00:00:00+00:00")
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["changes_risk"] is False


def test_cli_json_executes() -> None:
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/build_paper_autotrain_feedback_loop_v1.py"), "--project-root", str(REPO_ROOT), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == "paper_autotrain_feedback_loop_v1"
    assert payload["write_performed"] is False
    assert payload["sends_orders"] is False
