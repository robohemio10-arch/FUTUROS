from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from smartcrypto.research.daily_learning_scheduler_paper import (
    DAILY_LEARNING_SCHEDULER_PAPER_SCHEMA_VERSION,
    SchedulerContractError,
    SchedulerTime,
    build_daily_learning_scheduler_paper_report,
    build_run_plan,
    build_safe_orchestrator_command,
    build_schedule_contract,
    output_path_is_forbidden,
    summarize_orchestrator_payload,
    validate_daily_learning_scheduler_paper_report,
)


def test_default_report_is_blocked_research_only_contract() -> None:
    payload = build_daily_learning_scheduler_paper_report(project_root=".")

    assert payload["schema_version"] == DAILY_LEARNING_SCHEDULER_PAPER_SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["research_only"] is True
    assert payload["read_only"] is True
    assert payload["paper_only"] is True
    assert payload["shadow_only"] is True
    assert payload["operational_authority"] is False
    assert payload["registers_scheduler"] is False
    assert payload["creates_cron"] is False
    assert payload["creates_systemd_timer"] is False
    assert payload["creates_windows_task"] is False
    assert payload["executes_orchestrator"] is False
    assert payload["executes_stage_builders"] is False
    assert payload["write_performed"] is False
    assert payload["validation_errors"] == []


def test_scheduler_time_validation() -> None:
    assert SchedulerTime(hour_utc=3, minute_utc=15).hhmm == "03:15"
    assert SchedulerTime(hour_utc=23, minute_utc=59).iso_time == "23:59"

    with pytest.raises(SchedulerContractError):
        SchedulerTime(hour_utc=24, minute_utc=0).validate()

    with pytest.raises(SchedulerContractError):
        SchedulerTime(hour_utc=1, minute_utc=60).validate()


def test_schedule_contract_has_no_real_registration_targets() -> None:
    contract = build_schedule_contract(project_root=".", hour_utc=4, minute_utc=5)

    assert contract["scheduler_status"] == "contract_only_not_registered"
    assert contract["schedule_registration_status"] == "not_registered"
    assert contract["cadence"] == "DAILY"
    assert contract["time_utc"] == "04:05"
    assert not any(contract["registration_targets"].values())
    assert not any(contract["execution_targets"].values())


def test_safe_orchestrator_command_is_no_write_and_not_executed() -> None:
    command = build_safe_orchestrator_command(".")

    assert command["executes_now"] is False
    assert command["contains_no_write"] is True
    assert command["contains_json"] is True
    assert "--no-write" in command["command_args"]
    assert "--json" in command["command_args"]
    assert "build_daily_paper_master_learning_loop_orchestrator_v1.py" in command["command_display"]


def test_run_plan_never_executes_or_writes() -> None:
    plan = build_run_plan(project_root=".")

    assert plan["step_count"] == 4
    assert plan["executes_any_step"] is False
    assert plan["writes_any_output"] is False
    assert plan["blocked_step_count"] == 1
    assert all(step["executes_code"] is False for step in plan["steps"])
    assert all(step["writes_output"] is False for step in plan["steps"])


def test_orchestrator_payload_summary_marks_safe_payload() -> None:
    summary = summarize_orchestrator_payload(
        {
            "schema_version": "daily_paper_master_learning_loop_orchestrator_v1",
            "status": "blocked",
            "decision": "MANTER_EM_RESEARCH",
            "operational_authority": False,
            "write_performed": False,
            "stage_summary": {
                "stage_count": 11,
                "not_executed_stage_count": 11,
                "failed_stage_count": 0,
                "unsafe_stage_count": 0,
            },
        }
    )

    assert summary["payload_provided"] is True
    assert summary["stage_count"] == 11
    assert summary["safe_for_scheduler_contract"] is True


def test_orchestrator_payload_summary_rejects_unsafe_payload() -> None:
    summary = summarize_orchestrator_payload(
        {
            "decision": "PROMOTE",
            "operational_authority": True,
            "write_performed": True,
            "stage_summary": {"failed_stage_count": 1, "unsafe_stage_count": 1},
        }
    )

    assert summary["payload_provided"] is True
    assert summary["safe_for_scheduler_contract"] is False


def test_output_path_forbidden_roots() -> None:
    root = Path("/tmp/project")
    assert output_path_is_forbidden(root, root / "data" / "x.json") is True
    assert output_path_is_forbidden(root, root / "runtime" / "x.json") is True
    assert output_path_is_forbidden(root, root / "reports" / "x.json") is True
    assert output_path_is_forbidden(root, root / "logs" / "x.json") is True
    assert output_path_is_forbidden(root, root / "freqtrade" / "x.json") is True
    assert output_path_is_forbidden(root, root / "tmp_research" / "x.json") is False
    assert output_path_is_forbidden(root, Path("/outside/x.json")) is False


def test_validation_catches_forbidden_flag_flip() -> None:
    payload = build_daily_learning_scheduler_paper_report(project_root=".")
    payload["creates_cron"] = True
    payload["scheduler_scope"]["creates_cron"] = True

    errors = validate_daily_learning_scheduler_paper_report(payload)

    assert "creates_cron_must_be_false" in errors
    assert "scheduler_scope_creates_cron_must_be_false" in errors


def test_validation_requires_orchestrator_no_write_command() -> None:
    payload = build_daily_learning_scheduler_paper_report(project_root=".")
    command_args = payload["scheduler_contract"]["command_contract"]["command_args"]
    command_args.remove("--no-write")

    errors = validate_daily_learning_scheduler_paper_report(payload)

    assert "orchestrator_command_must_include_no_write" in errors


def test_report_contains_operator_blocks_and_readiness_policy() -> None:
    payload = build_daily_learning_scheduler_paper_report(project_root=".")

    assert payload["operator_decision"]["scheduler_registration_allowed"] is False
    assert payload["operator_decision"]["orchestrator_execution_allowed"] is False
    assert payload["operator_decision"]["training_allowed"] is False
    assert payload["readiness_policy"]["scheduler_contract_is_not_readiness_evidence"] is True
    assert payload["readiness_policy"]["real_scheduler_registration_requires_separate_branch"] is True


def test_cli_no_write_json() -> None:
    script = Path("scripts/build_daily_learning_scheduler_paper_v1.py")
    result = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--no-write", "--json"],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert payload["cli_reason"] == "no_write_requested"
    assert payload["write_performed"] is False
    assert payload["registers_scheduler"] is False


def test_cli_blocks_forbidden_output_path(tmp_path: Path) -> None:
    script = Path("scripts/build_daily_learning_scheduler_paper_v1.py")
    forbidden = tmp_path / "data" / "scheduler.json"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(tmp_path),
            "--output",
            str(forbidden),
            "--json",
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["reason"] == "output_path_under_forbidden_runtime_root"
    assert payload["write_performed"] is False
    assert not forbidden.exists()


def test_cli_allows_explicit_non_runtime_output(tmp_path: Path) -> None:
    script = Path("scripts/build_daily_learning_scheduler_paper_v1.py")
    output = tmp_path / "tmp_research" / "scheduler.json"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(tmp_path),
            "--output",
            str(output),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["write_requested"] is True
    assert payload["write_performed"] is True
    assert output.exists()


def test_static_policy_terms_are_not_operationalized() -> None:
    payload = build_daily_learning_scheduler_paper_report(project_root=".")
    serialized = json.dumps(payload, sort_keys=True)

    assert "contract_only_not_registered" in serialized
    assert payload["creates_service"] is False
    assert payload["modifies_project_scheduler"] is False
    assert payload["modifies_operational_runtime"] is False
    assert payload["updates_freqtrade"] is False
    assert payload["updates_risk_manager"] is False
