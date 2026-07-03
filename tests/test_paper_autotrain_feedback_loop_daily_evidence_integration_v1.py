from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from scripts.build_daily_learning_evidence_readiness_integration_v1 import build_report_from_args
from smartcrypto.research.daily_learning_evidence_readiness_integration import (
    build_daily_learning_evidence_readiness_integration_snapshot,
    validate_daily_learning_evidence_readiness_integration_snapshot,
)


def _safe_paper_autotrain_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "paper_autotrain_feedback_loop_v1",
        "status": "ok",
        "reason": "research_candidate_not_promoted",
        "decision": "MANTER_EM_RESEARCH",
        "blockers": [],
        "warnings": ["qlib_backend_unavailable"],
        "lineage_hashes": {
            "feature_contract_hash": "feature-hash",
            "dataset_hash": "dataset-hash",
            "target_store_hash": "target-hash",
        },
        "input_sources": [{"source_id": "feature_contract"}],
        "write_performed": False,
        "run_qlib_train_requested": False,
        "run_ai_shadow_train_requested": False,
        "research_only": True,
        "read_only": True,
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "changes_model": False,
        "promotion_eligible": False,
        "registry_write_performed": False,
        "model_promotion_performed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }
    payload.update(overrides)
    return payload


def test_paper_autotrain_feedback_loop_is_daily_evidence_section() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot(
        paper_autotrain_feedback_loop_payload=_safe_paper_autotrain_payload()
    )

    section = snapshot["paper_autotrain_feedback_loop_v1"]
    assert section["status"] == "ok"
    assert section["decision"] == "MANTER_EM_RESEARCH"
    assert section["warnings"] == ["qlib_backend_unavailable"]
    assert section["hashes"]["feature_contract_hash"] == "feature-hash"
    assert section["hashes"]["dataset_hash"] == "dataset-hash"
    assert section["safety_flags"]["paper_only"] is True
    assert section["safety_flags"]["shadow_only"] is True
    assert section["safety_flags"]["sends_orders"] is False
    assert section["safety_flags"]["exchange_private_access"] is False
    assert section["safe_for_readiness"] is True
    assert snapshot["status"] == "blocked"
    assert snapshot["readiness_release_authority"] is False
    assert snapshot["operational_authority"] is False
    assert validate_daily_learning_evidence_readiness_integration_snapshot(snapshot) == []


def test_blocked_paper_autotrain_payload_stays_safe_and_research_only() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot(
        paper_autotrain_feedback_loop_payload=_safe_paper_autotrain_payload(
            status="blocked",
            decision="BLOCKED",
            reason="missing_required_source:data/reports/example.json",
            blockers=["missing_required_source:data/reports/example.json"],
        )
    )

    section = snapshot["paper_autotrain_feedback_loop_v1"]
    assert section["status"] == "blocked"
    assert section["decision"] == "BLOCKED"
    assert section["blockers"] == ["missing_required_source:data/reports/example.json"]
    assert section["safe_for_readiness"] is True
    assert snapshot["source_summary"]["unsafe_source_count"] == 0
    assert snapshot["readiness_decision"]["final_decision"] == "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH"
    assert validate_daily_learning_evidence_readiness_integration_snapshot(snapshot) == []


def test_unsafe_paper_autotrain_payload_blocks_source_safety_gate() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot(
        paper_autotrain_feedback_loop_payload=_safe_paper_autotrain_payload(
            sends_orders=True,
            safety_flags={"sends_orders": True},
        )
    )

    section = snapshot["paper_autotrain_feedback_loop_v1"]
    assert section["safe_for_readiness"] is False
    assert "paper_autotrain_feedback_loop_v1" in snapshot["source_summary"]["unsafe_sources"]
    errors = validate_daily_learning_evidence_readiness_integration_snapshot(snapshot)
    assert "failed_readiness_gate_source_payload_safety" in errors
    assert "unsafe_daily_learning_sources_present" in errors


def test_cli_probe_adds_paper_autotrain_without_runtime_writes(tmp_path: Path) -> None:
    args = argparse.Namespace(
        project_root=str(tmp_path),
        json=True,
        no_write=True,
        output=None,
        paper_autotrain_report=None,
        skip_paper_autotrain_probe=False,
    )

    snapshot = build_report_from_args(args)

    section = snapshot["paper_autotrain_feedback_loop_v1"]
    assert section["payload_loaded"] is True
    assert section["status"] == "blocked"
    assert section["decision"] == "BLOCKED"
    assert section["write_performed"] is False
    assert section["run_qlib_train_requested"] is False
    assert section["run_ai_shadow_train_requested"] is False
    assert section["safety_flags"]["sends_orders"] is False
    assert snapshot["write_performed"] is False
    assert not (tmp_path / "data/reports/paper_autotrain_feedback_loop_v1.json").exists()


def test_cli_json_executes_with_paper_autotrain_section(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_daily_learning_evidence_readiness_integration_v1.py",
            "--project-root",
            str(tmp_path),
            "--no-write",
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    section = payload["paper_autotrain_feedback_loop_v1"]
    assert payload["status"] == "blocked"
    assert payload["write_performed"] is False
    assert section["payload_loaded"] is True
    assert section["write_performed"] is False
    assert section["safety_flags"]["sends_orders"] is False
    assert section["safety_flags"]["exchange_private_access"] is False
