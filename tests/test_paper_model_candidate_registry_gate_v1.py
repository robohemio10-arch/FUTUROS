from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.paper_model_candidate_registry_gate.registry_gate import (
    build_paper_model_candidate_registry_gate_v1,
)


SCRIPT = Path("scripts/build_paper_model_candidate_registry_gate_v1.py")


def valid_payloads() -> dict[str, dict]:
    return {
        "qlib_trainer": {
            "status": "ok",
            "trainer_status": "ok",
            "backend_name": "qlib",
            "feature_column_count": 9,
            "split_count": 3,
            "aggregate_metrics": {"rank_ic_mean": 0.05, "precision_at_k": 0.62},
            "promotion_eligible": False,
        },
        "ai_shadow_quality_veto": {
            "status": "ok",
            "trainer_status": "ok",
            "backend_name": "ai_shadow",
            "feature_column_count": 9,
            "split_count": 3,
            "aggregate_metrics": {"f1": 0.61, "roc_auc": 0.7},
            "promotion_eligible": False,
        },
        "ensemble_threshold_calibration": {
            "status": "ok",
            "decision": "MANTER_EM_RESEARCH",
            "calibration_row_count": 498,
            "recommended_candidate": {
                "threshold": 0.55,
                "selected_count": 3,
                "average_expected_value": 0.3,
                "pnl_selected": 0.9,
            },
            "thresholds_applied": False,
        },
        "feature_source_contract": {
            "status": "ok",
            "can_derive_feature_notional": True,
            "can_derive_feature_quantity": True,
            "forbidden_fields_used": [],
        },
        "target_store": {
            "status": "ok",
            "target_store_hash": "target-hash",
            "target_records": [{"order_id": "1", "target_expected_value_component": 1.0}],
        },
        "drift_monitor": {
            "status": "ok",
            "blockers": [],
            "lineage_hashes": {"feature_contract_hash": "feature-hash"},
        },
        "execution_cost_gate": {
            "status": "ok",
            "blockers": [],
            "research_candidate_cost_gate_passed": True,
        },
        "paper_autotrain_feedback_loop": {
            "status": "ok",
            "decision": "MANTER_EM_RESEARCH",
            "promotion_eligible": False,
            "aggregate_decision": "research_only",
        },
    }


def candidate_by_source(report: dict, source_id: str) -> dict:
    return next(candidate for candidate in report["candidates"] if candidate["source_id"] == source_id)


def test_report_is_json_serializable(tmp_path: Path) -> None:
    report = build_paper_model_candidate_registry_gate_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(),
    )

    encoded = json.dumps(report, sort_keys=True)
    assert "paper_model_candidate_registry_gate_v1" in encoded


def test_safety_flags_disable_runtime_model_registry_and_orders(tmp_path: Path) -> None:
    report = build_paper_model_candidate_registry_gate_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(),
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["release_allowed"] is False
    for key, value in report["safety_flags"].items():
        if key in {"research_only", "paper_only", "shadow_only", "read_only"}:
            assert value is True
        else:
            assert value is False
        assert report[key] == value


def test_domain_no_write_does_not_materialize_files(tmp_path: Path) -> None:
    report = build_paper_model_candidate_registry_gate_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(),
        write=True,
    )

    assert report["write_requested"] is True
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "paper_model_candidate_registry_gate_v1.json").exists()
    assert not (tmp_path / "data" / "reports" / "paper_model_candidate_registry_gate_v1.md").exists()
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))


def test_cli_write_writes_only_json_and_markdown_in_data_reports(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--write", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    json_report = tmp_path / "data" / "reports" / "paper_model_candidate_registry_gate_v1.json"
    markdown_report = tmp_path / "data" / "reports" / "paper_model_candidate_registry_gate_v1.md"

    assert payload["write_performed"] is True
    assert json_report.is_file()
    assert markdown_report.is_file()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "data" / "models").exists()
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))


def test_missing_input_source_generates_blocker_without_exception(tmp_path: Path) -> None:
    payloads = valid_payloads()
    payloads.pop("qlib_trainer")

    report = build_paper_model_candidate_registry_gate_v1(project_root=tmp_path, evidence_payloads=payloads)
    qlib_candidate = candidate_by_source(report, "qlib_trainer")

    assert report["status"] in {"blocked", "warning"}
    assert qlib_candidate["gate_status"] == "blocked_missing_evidence"
    assert "blocked_missing_evidence" in qlib_candidate["blocked_reasons"]


def test_candidate_without_evidence_is_blocked_missing_evidence(tmp_path: Path) -> None:
    report = build_paper_model_candidate_registry_gate_v1(project_root=tmp_path, evidence_payloads={})

    assert report["registry_gate_status"] == "blocked_no_eligible_candidates"
    assert report["candidate_count"] == 4
    assert all(candidate["gate_status"] == "blocked_missing_evidence" for candidate in report["candidates"])


def test_candidate_with_drift_blocked_is_blocked_by_drift_gate(tmp_path: Path) -> None:
    payloads = valid_payloads()
    payloads["drift_monitor"] = {"status": "blocked", "reason": "critical_drift"}

    report = build_paper_model_candidate_registry_gate_v1(project_root=tmp_path, evidence_payloads=payloads)
    qlib_candidate = candidate_by_source(report, "qlib_trainer")

    assert qlib_candidate["gate_status"] == "blocked_drift_gate"
    assert "blocked_drift_gate" in qlib_candidate["blocked_reasons"]


def test_candidate_with_execution_cost_blocked_is_blocked_by_cost_gate(tmp_path: Path) -> None:
    payloads = valid_payloads()
    payloads["execution_cost_gate"] = {"status": "blocked", "reason": "execution_cost_gate_blocked"}

    report = build_paper_model_candidate_registry_gate_v1(project_root=tmp_path, evidence_payloads=payloads)
    candidate = candidate_by_source(report, "ai_shadow_quality_veto")

    assert candidate["gate_status"] == "blocked_execution_cost_gate"
    assert "blocked_execution_cost_gate" in candidate["blocked_reasons"]


def test_candidate_with_source_contract_insufficient_is_blocked_by_source_gate(tmp_path: Path) -> None:
    payloads = valid_payloads()
    payloads["feature_source_contract"] = {
        "status": "warning",
        "can_derive_feature_notional": False,
        "can_derive_feature_quantity": True,
        "forbidden_fields_used": [],
    }

    report = build_paper_model_candidate_registry_gate_v1(project_root=tmp_path, evidence_payloads=payloads)
    candidate = candidate_by_source(report, "ensemble_threshold_calibration")

    assert candidate["gate_status"] == "blocked_source_contract_gate"
    assert "blocked_source_contract_gate" in candidate["blocked_reasons"]


def test_candidate_with_sufficient_evidence_is_research_review_only(tmp_path: Path) -> None:
    report = build_paper_model_candidate_registry_gate_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(),
    )

    assert report["status"] == "ok"
    assert report["registry_gate_status"] == "ok_research_review_only"
    assert report["eligible_candidate_count"] == 4
    for candidate in report["candidates"]:
        assert candidate["gate_status"] == "eligible_for_research_review"
        assert candidate["eligible_for_research_review"] is True
        assert candidate["eligible_for_runtime"] is False
        assert candidate["promotes_model"] is False
        assert candidate["applies_thresholds"] is False
        assert candidate["writes_registry"] is False
        assert candidate["updates_runtime"] is False


def test_decision_always_manter_em_research_even_when_eligible(tmp_path: Path) -> None:
    report = build_paper_model_candidate_registry_gate_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(),
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["release_allowed"] is False
    assert all(candidate["eligible_for_runtime"] is False for candidate in report["candidates"])


def test_cli_real_project_no_write_executes_without_runtime_mutation() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", ".", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["write_performed"] is False
    assert payload["registry_write_performed"] is False
    assert payload["candidate_registry_write_performed"] is False
    assert payload["sends_orders"] is False
    assert payload["exchange_private_access"] is False
