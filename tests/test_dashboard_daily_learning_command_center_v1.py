from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.dashboard.services.daily_learning_command_center import (
    SCHEMA_VERSION,
    build_daily_learning_command_center_view_model,
    build_dashboard_daily_learning_command_center_snapshot,
    validate_dashboard_daily_learning_command_center_snapshot,
)


def test_empty_snapshot_is_blocked_readonly_and_safe() -> None:
    snapshot = build_dashboard_daily_learning_command_center_snapshot(project_root=".")
    assert snapshot["schema_version"] == SCHEMA_VERSION
    assert snapshot["status"] == "blocked"
    assert snapshot["decision"] == "MANTER_EM_RESEARCH"
    assert snapshot["input_mode"] == "no_runtime_rows_loaded"
    assert snapshot["research_only"] is True
    assert snapshot["read_only"] is True
    assert snapshot["paper_only"] is True
    assert snapshot["shadow_only"] is True
    assert snapshot["dashboard_readonly"] is True
    assert snapshot["operational_authority"] is False
    assert snapshot["dashboard_operational_controls"] is False
    assert snapshot["executes_orchestrator"] is False
    assert snapshot["executes_stage_builders"] is False
    assert snapshot["registers_scheduler"] is False
    assert snapshot["runs_training"] is False
    assert snapshot["updates_qlib_runtime"] is False
    assert snapshot["updates_ai_shadow_runtime"] is False
    assert snapshot["updates_freqtrade"] is False
    assert snapshot["updates_risk_manager"] is False
    assert snapshot["sends_orders"] is False
    assert snapshot["write_performed"] is False
    assert validate_dashboard_daily_learning_command_center_snapshot(snapshot) == []


def test_empty_snapshot_renders_six_source_cards_as_not_loaded() -> None:
    snapshot = build_dashboard_daily_learning_command_center_snapshot()
    assert snapshot["source_summary"]["source_count"] == 6
    assert snapshot["source_summary"]["payload_loaded_count"] == 0
    assert {card["status"] for card in snapshot["source_cards"]} == {"not_loaded"}
    assert {card["decision"] for card in snapshot["source_cards"]} == {"MANTER_EM_RESEARCH"}


def test_in_memory_payloads_are_summarized_without_operational_authority() -> None:
    snapshot = build_dashboard_daily_learning_command_center_snapshot(
        scheduler_payload={
            "schema_version": "daily_learning_scheduler_paper_v1",
            "status": "blocked",
            "decision": "MANTER_EM_RESEARCH",
            "input_mode": "no_runtime_rows_loaded",
            "research_only": True,
            "read_only": True,
            "operational_authority": False,
            "write_performed": False,
            "run_plan_summary": {"step_count": 4},
        },
        orchestrator_payload={
            "schema_version": "daily_paper_master_learning_loop_orchestrator_v1",
            "status": "blocked",
            "decision": "MANTER_EM_RESEARCH",
            "input_mode": "no_runtime_rows_loaded",
            "research_only": True,
            "read_only": True,
            "operational_authority": False,
            "write_performed": False,
            "stage_summary": {"total_reported_rows": 11},
        },
    )
    assert snapshot["input_mode"] == "in_memory_payloads_loaded"
    assert snapshot["source_summary"]["payload_loaded_count"] == 2
    assert snapshot["source_summary"]["total_reported_rows"] == 15
    assert snapshot["source_summary"]["unsafe_source_count"] == 0
    assert validate_dashboard_daily_learning_command_center_snapshot(snapshot) == []


def test_unsafe_source_payload_blocks_source_gate() -> None:
    snapshot = build_dashboard_daily_learning_command_center_snapshot(
        scheduler_payload={
            "status": "ok",
            "decision": "APROVAR_OPERACAO",
            "research_only": False,
            "read_only": False,
            "operational_authority": True,
            "write_performed": True,
        }
    )
    assert snapshot["status"] == "blocked"
    assert snapshot["source_summary"]["unsafe_source_count"] == 1
    assert "scheduler" in snapshot["source_summary"]["unsafe_sources"]
    assert "failed_dashboard_gate_source_payload_safety" in snapshot["validation_errors"]


def test_view_model_is_compact_and_readonly() -> None:
    view_model = build_daily_learning_command_center_view_model()
    assert view_model["title"].startswith("SMART FUTUROS")
    assert view_model["status"] == "blocked"
    assert view_model["decision"] == "MANTER_EM_RESEARCH"
    assert len(view_model["cards"]) == 6
    assert view_model["safety_footer"]["dashboard_readonly"] is True
    assert view_model["safety_footer"]["operational_authority"] is False
    assert view_model["safety_footer"]["order_submission_enabled"] is False


def test_validation_detects_mutated_operational_flags() -> None:
    snapshot = build_dashboard_daily_learning_command_center_snapshot()
    snapshot["operational_authority"] = True
    snapshot["dashboard_readonly"] = False
    errors = validate_dashboard_daily_learning_command_center_snapshot(snapshot)
    assert "operational_authority_must_be_false" in errors
    assert "dashboard_readonly_must_be_true" in errors


def test_cli_no_write_json_returns_blocked_snapshot() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_dashboard_daily_learning_command_center_v1.py",
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
    assert payload["dashboard_readonly"] is True
    assert payload["registers_scheduler"] is False


def test_cli_blocks_forbidden_output_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "project" / "data"
    data_dir.mkdir(parents=True)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_dashboard_daily_learning_command_center_v1.py",
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
    output_path = tmp_path / "dashboard_daily_learning_snapshot.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_dashboard_daily_learning_command_center_v1.py",
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


def test_page_module_imports_without_streamlit_dependency() -> None:
    import smartcrypto.dashboard.pages.daily_learning_command_center as page

    assert callable(page.render_daily_learning_command_center_page)


def test_static_source_has_no_operational_button_terms() -> None:
    page_source = Path("smartcrypto/dashboard/pages/daily_learning_command_center.py").read_text(encoding="utf-8")
    service_source = Path("smartcrypto/dashboard/services/daily_learning_command_center.py").read_text(encoding="utf-8")
    assert ".button(" not in page_source
    assert ".form(" not in page_source
    assert "send_order" not in service_source
    assert "create_order" not in service_source


def test_snapshot_contains_expected_command_center_sections() -> None:
    snapshot = build_dashboard_daily_learning_command_center_snapshot()
    section_ids = {section["section_id"] for section in snapshot["command_center_sections"]}
    assert section_ids == {
        "daily_learning_overview",
        "scheduler_contract",
        "research_pipeline",
        "safety_gates",
    }
    assert all(section["read_only"] is True for section in snapshot["command_center_sections"])
    assert all(section["contains_operational_controls"] is False for section in snapshot["command_center_sections"])


def test_gate_matrix_keeps_critical_gates_passing_on_empty_snapshot() -> None:
    snapshot = build_dashboard_daily_learning_command_center_snapshot()
    critical_gates = [gate for gate in snapshot["gate_matrix"] if gate["severity"] == "critical"]
    assert critical_gates
    assert all(gate["passed"] is True for gate in critical_gates)


def test_dashboard_scope_contains_all_hard_blocks() -> None:
    snapshot = build_dashboard_daily_learning_command_center_snapshot()
    scope = snapshot["dashboard_scope"]
    assert scope["creates_operational_buttons"] is False
    assert scope["creates_go_live_button"] is False
    assert scope["creates_promote_button"] is False
    assert scope["executes_any_code_from_dashboard"] is False
    assert scope["builds_dashboard_snapshot"] is True
    assert scope["shows_research_gates"] is True


def test_operator_decision_blocks_all_runtime_actions() -> None:
    snapshot = build_dashboard_daily_learning_command_center_snapshot()
    operator_decision = snapshot["operator_decision"]
    assert operator_decision["final_decision"] == "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH"
    blocked_keys = [key for key in operator_decision if key.endswith("_allowed")]
    assert blocked_keys
    assert all(operator_decision[key] is False for key in blocked_keys)
