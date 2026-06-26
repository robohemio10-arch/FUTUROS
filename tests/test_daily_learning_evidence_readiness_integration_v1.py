from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_learning_evidence_readiness_integration import (
    SCHEMA_VERSION,
    build_daily_learning_evidence_readiness_integration_snapshot,
    build_daily_learning_evidence_readiness_view_model,
    validate_daily_learning_evidence_readiness_integration_snapshot,
)


def test_empty_snapshot_is_blocked_informational_and_safe() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot(project_root=".")
    assert snapshot["schema_version"] == SCHEMA_VERSION
    assert snapshot["status"] == "blocked"
    assert snapshot["decision"] == "MANTER_EM_RESEARCH"
    assert snapshot["readiness_status"] == "blocked"
    assert snapshot["input_mode"] == "no_runtime_rows_loaded"
    assert snapshot["research_only"] is True
    assert snapshot["read_only"] is True
    assert snapshot["paper_only"] is True
    assert snapshot["shadow_only"] is True
    assert snapshot["daily_learning_evidence_is_informational"] is True
    assert snapshot["readiness_snapshot_blocked"] is True
    assert snapshot["readiness_release_authority"] is False
    assert snapshot["operational_authority"] is False
    assert snapshot["live_release_allowed"] is False
    assert snapshot["canary_release_allowed"] is False
    assert snapshot["write_performed"] is False
    assert validate_daily_learning_evidence_readiness_integration_snapshot(snapshot) == []


def test_empty_snapshot_contains_seven_sources_as_not_loaded() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot()
    assert snapshot["source_summary"]["source_count"] == 7
    assert snapshot["source_summary"]["payload_loaded_count"] == 0
    assert snapshot["source_summary"]["unsafe_source_count"] == 0
    assert {card["status"] for card in snapshot["source_cards"]} == {"not_loaded"}
    assert {card["decision"] for card in snapshot["source_cards"]} == {"MANTER_EM_RESEARCH"}


def test_in_memory_safe_payloads_are_informational_not_releasing() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot(
        scheduler_payload={
            "schema_version": "daily_learning_scheduler_paper_v1",
            "status": "blocked",
            "decision": "MANTER_EM_RESEARCH",
            "input_mode": "no_runtime_rows_loaded",
            "research_only": True,
            "read_only": True,
            "paper_only": True,
            "shadow_only": True,
            "operational_authority": False,
            "write_performed": False,
            "run_plan_summary": {"step_count": 4},
        },
        dashboard_payload={
            "schema_version": "dashboard_daily_learning_command_center_v1",
            "status": "blocked",
            "decision": "MANTER_EM_RESEARCH",
            "research_only": True,
            "read_only": True,
            "operational_authority": False,
            "write_performed": False,
            "source_summary": {"source_count": 6},
        },
        orchestrator_payload={
            "schema_version": "daily_paper_master_learning_loop_orchestrator_v1",
            "status": "blocked",
            "decision": "MANTER_EM_RESEARCH",
            "research_only": True,
            "read_only": True,
            "operational_authority": False,
            "write_performed": False,
            "stage_summary": {"total_reported_rows": 11},
        },
    )
    assert snapshot["input_mode"] == "in_memory_payloads_loaded"
    assert snapshot["source_summary"]["payload_loaded_count"] == 3
    assert snapshot["source_summary"]["total_reported_rows"] == 21
    assert snapshot["readiness_release_authority"] is False
    assert snapshot["readiness_summary"]["release_allowed"] is False
    assert validate_daily_learning_evidence_readiness_integration_snapshot(snapshot) == []


def test_unsafe_payload_blocks_high_readiness_gate() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot(
        scheduler_payload={
            "status": "ok",
            "decision": "APROVAR_OPERACAO",
            "research_only": False,
            "read_only": False,
            "operational_authority": True,
            "write_performed": True,
            "live_release_allowed": True,
        }
    )
    assert snapshot["status"] == "blocked"
    assert snapshot["source_summary"]["unsafe_source_count"] == 1
    assert "scheduler" in snapshot["source_summary"]["unsafe_sources"]
    errors = validate_daily_learning_evidence_readiness_integration_snapshot(snapshot)
    assert "failed_readiness_gate_source_payload_safety" in errors
    assert "unsafe_daily_learning_sources_present" in errors


def test_release_authority_in_nested_operator_decision_is_unsafe() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot(
        dashboard_payload={
            "status": "blocked",
            "decision": "MANTER_EM_RESEARCH",
            "research_only": True,
            "read_only": True,
            "operational_authority": False,
            "write_performed": False,
            "operator_decision": {"canary_release_allowed": True},
        }
    )
    digest = next(item for item in snapshot["source_digests"] if item["source_id"] == "dashboard_command_center")
    assert digest["release_authority"] is True
    assert digest["safe_for_readiness"] is False
    assert "dashboard_command_center" in snapshot["source_summary"]["unsafe_sources"]


def test_gate_matrix_keeps_critical_gates_passing_in_empty_mode() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot()
    critical_gates = [gate for gate in snapshot["gate_matrix"] if gate["severity"] == "critical"]
    assert critical_gates
    assert all(gate["passed"] is True for gate in critical_gates)
    info_gate = next(gate for gate in snapshot["gate_matrix"] if gate["gate_id"] == "source_payload_presence")
    assert info_gate["passed"] is False
    assert info_gate["severity"] == "info"
    assert validate_daily_learning_evidence_readiness_integration_snapshot(snapshot) == []


def test_view_model_is_compact_and_non_releasing() -> None:
    view_model = build_daily_learning_evidence_readiness_view_model()
    assert view_model["title"].startswith("SMART FUTUROS")
    assert view_model["status"] == "blocked"
    assert view_model["decision"] == "MANTER_EM_RESEARCH"
    assert view_model["readiness_status"] == "blocked"
    assert len(view_model["cards"]) == 7
    assert view_model["safety_footer"]["daily_learning_evidence_is_informational"] is True
    assert view_model["safety_footer"]["readiness_release_authority"] is False
    assert view_model["safety_footer"]["order_submission_enabled"] is False


def test_validation_detects_mutated_release_flags() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot()
    snapshot["readiness_release_authority"] = True
    snapshot["daily_learning_evidence_is_informational"] = False
    snapshot["canary_release_allowed"] = True
    errors = validate_daily_learning_evidence_readiness_integration_snapshot(snapshot)
    assert "readiness_release_authority_must_be_false" in errors
    assert "daily_learning_evidence_is_informational_must_be_true" in errors
    assert "canary_release_allowed_must_be_false" in errors


def test_cli_no_write_json_returns_blocked_snapshot() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_daily_learning_evidence_readiness_integration_v1.py",
            "--project-root",
            ".",
            "--no-write",
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert payload["daily_learning_evidence_is_informational"] is True
    assert payload["readiness_release_authority"] is False


def test_cli_blocks_forbidden_output_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "project" / "data"
    data_dir.mkdir(parents=True)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_daily_learning_evidence_readiness_integration_v1.py",
            "--project-root",
            str(tmp_path / "project"),
            "--output",
            str(data_dir / "snapshot.json"),
            "--json",
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert payload["write_performed"] is False
    assert any(error.startswith("output_path_under_forbidden_runtime_tree") for error in payload["validation_errors"])
    assert not (data_dir / "snapshot.json").exists()


def test_cli_allows_explicit_safe_json_output(tmp_path: Path) -> None:
    output_path = tmp_path / "daily_learning_evidence_readiness_snapshot.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_daily_learning_evidence_readiness_integration_v1.py",
            "--project-root",
            ".",
            "--output",
            str(output_path),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    assert payload["write_requested"] is True
    assert payload["write_performed"] is True
    assert payload["output_path"] == str(output_path)
    assert output_path.exists()


def test_snapshot_has_expected_evidence_sources() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot()
    source_ids = {card["card_id"] for card in snapshot["source_cards"]}
    assert source_ids == {
        "scheduler",
        "dashboard_command_center",
        "orchestrator",
        "qlib_research_dataset",
        "ai_shadow_feedback_bridge",
        "candidate_shadow_rule_registry",
        "shadow_rule_oos_validation",
    }


def test_readiness_decision_blocks_all_authorities() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot()
    decision = snapshot["readiness_decision"]
    assert decision["final_decision"] == "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH"
    assert decision["daily_learning_release_authority"] is False
    assert decision["canary_release_allowed"] is False
    assert decision["live_release_allowed"] is False
    assert decision["model_promotion_allowed"] is False
    assert decision["shadow_rule_promotion_allowed"] is False
    assert decision["training_allowed"] is False


def test_static_source_has_no_runtime_registration_or_execution_terms() -> None:
    module_source = Path("smartcrypto/research/daily_learning_evidence_readiness_integration.py").read_text(
        encoding="utf-8"
    )
    script_source = Path("scripts/build_daily_learning_evidence_readiness_integration_v1.py").read_text(
        encoding="utf-8"
    )
    forbidden_literals = [
        "subprocess.run(",
        "os.system(",
        "create_order",
        "send_order",
        "register_cron",
        "systemctl",
        "schtasks",
    ]
    for literal in forbidden_literals:
        assert literal not in module_source
    assert "subprocess.run(" not in script_source


def test_validation_errors_are_stable_and_unique() -> None:
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot()
    snapshot["validation_errors"] = ["x", "x", "y"]
    errors = validate_daily_learning_evidence_readiness_integration_snapshot(snapshot)
    assert errors == ["x", "y"]
