from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_paper_master_learning_loop_orchestrator import (
    DAILY_PAPER_MASTER_LEARNING_LOOP_ORCHESTRATOR_SCHEMA_VERSION,
    build_daily_learning_loop_orchestration,
    build_daily_learning_stage_result,
    build_daily_paper_master_learning_loop_orchestrator_report,
    get_daily_learning_stage_plan,
    validate_daily_paper_master_learning_loop_orchestrator_report,
)


def _sample_stage_payload(status: str = "blocked", row_count: int = 3) -> dict:
    return {
        "schema_version": "sample_stage_v1",
        "status": status,
        "decision": "MANTER_EM_RESEARCH",
        "reason": "sample_research_payload",
        "input_mode": "in_memory_inputs",
        "row_count": row_count,
        "research_only": True,
        "read_only": True,
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "write_performed": False,
        "runs_training": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "applies_shadow_rules": False,
        "sends_orders": False,
        "writes_data": False,
        "writes_runtime": False,
        "writes_reports": False,
    }


def test_report_without_payloads_is_blocked_and_no_runtime_mode() -> None:
    report = build_daily_paper_master_learning_loop_orchestrator_report(project_root=".")
    assert report["schema_version"] == DAILY_PAPER_MASTER_LEARNING_LOOP_ORCHESTRATOR_SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["operational_authority"] is False
    assert report["write_performed"] is False
    assert report["validation_errors"] == []


def test_stage_plan_has_canonical_order_and_expected_final_stage() -> None:
    plan = get_daily_learning_stage_plan()
    stage_ids = [stage["stage_id"] for stage in plan]
    assert len(stage_ids) == 11
    assert stage_ids[0] == "contracts_source_map"
    assert stage_ids[-1] == "qlib_research_dataset"
    assert stage_ids == [
        "contracts_source_map",
        "readonly_loaders",
        "paper_master_kpi_pack",
        "divergence_alignment",
        "candle_coverage_entry_features",
        "mistake_winner_catalog",
        "pattern_mining_research",
        "candidate_shadow_rule_registry",
        "shadow_rule_oos_validation",
        "ai_shadow_feedback_bridge",
        "qlib_research_dataset",
    ]


def test_orchestration_without_payloads_does_not_execute_builders_by_default() -> None:
    orchestration = build_daily_learning_loop_orchestration(project_root=".")
    summary = orchestration["stage_summary"]
    assert summary["stage_count"] == 11
    assert summary["not_executed_stage_count"] == 11
    assert summary["provided_payload_stage_count"] == 0
    assert orchestration["orchestrator_scope"]["uses_only_in_memory_inputs"] is True
    assert orchestration["orchestrator_scope"]["optional_stage_builder_execution_requested"] is False


def test_in_memory_payloads_are_summarized_without_operational_authority() -> None:
    report = build_daily_paper_master_learning_loop_orchestrator_report(
        project_root=".",
        stage_payloads={
            "paper_master_kpi_pack": _sample_stage_payload(row_count=5),
            "qlib_research_dataset": _sample_stage_payload(row_count=7),
        },
    )
    summary = report["stage_summary"]
    assert report["input_mode"] == "in_memory_stage_payloads"
    assert summary["provided_payload_stage_count"] == 2
    assert summary["row_counts_by_stage"]["paper_master_kpi_pack"] == 5
    assert summary["row_counts_by_stage"]["qlib_research_dataset"] == 7
    assert summary["total_reported_rows"] == 12
    assert report["validation_errors"] == []


def test_stage_payload_with_operational_authority_is_rejected() -> None:
    bad_payload = _sample_stage_payload()
    bad_payload["operational_authority"] = True
    report = build_daily_paper_master_learning_loop_orchestrator_report(
        stage_payloads={"qlib_research_dataset": bad_payload}
    )
    assert "qlib_research_dataset:operational_authority_must_be_false" in report[
        "validation_errors"
    ]
    assert report["stage_summary"]["unsafe_stage_count"] == 1


def test_stage_payload_with_runtime_write_is_rejected() -> None:
    bad_payload = _sample_stage_payload()
    bad_payload["writes_runtime"] = True
    report = build_daily_paper_master_learning_loop_orchestrator_report(
        stage_payloads={"ai_shadow_feedback_bridge": bad_payload}
    )
    assert "ai_shadow_feedback_bridge:writes_runtime_must_be_false" in report[
        "validation_errors"
    ]


def test_top_level_runtime_authority_flags_are_hard_false() -> None:
    report = build_daily_paper_master_learning_loop_orchestrator_report(project_root=".")
    false_flags = [
        "runs_training",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "updates_freqtrade",
        "updates_risk_manager",
        "applies_shadow_rules",
        "applies_feedback_to_ai_shadow",
        "can_promote_model",
        "can_promote_rules",
        "sends_orders",
        "live_trading_enabled",
        "canary_release_allowed",
        "writes_data",
        "writes_runtime",
        "writes_reports",
    ]
    for flag in false_flags:
        assert report[flag] is False


def test_build_single_stage_result_preserves_research_boundary() -> None:
    definition = get_daily_learning_stage_plan()[0]
    stage = build_daily_learning_stage_result(definition, payload=_sample_stage_payload())
    assert stage["source"] == "provided_payload"
    assert stage["research_only"] is True
    assert stage["read_only"] is True
    assert stage["operational_authority"] is False
    assert stage["safe_for_research_orchestration"] is True


def test_validate_detects_stage_order_mismatch() -> None:
    report = build_daily_paper_master_learning_loop_orchestrator_report(project_root=".")
    report["daily_learning_orchestrator"]["stage_results"] = list(
        reversed(report["daily_learning_orchestrator"]["stage_results"])
    )
    errors = validate_daily_paper_master_learning_loop_orchestrator_report(report)
    assert "stage_order_mismatch" in errors


def test_validate_detects_mutated_top_level_release_flag() -> None:
    report = build_daily_paper_master_learning_loop_orchestrator_report(project_root=".")
    report["live_trading_enabled"] = True
    errors = validate_daily_paper_master_learning_loop_orchestrator_report(report)
    assert "live_trading_enabled_must_be_false" in errors


def test_cli_no_write_json_output() -> None:
    script = Path("scripts/build_daily_paper_master_learning_loop_orchestrator_v1.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--no-write", "--json"],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == DAILY_PAPER_MASTER_LEARNING_LOOP_ORCHESTRATOR_SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["write_performed"] is False
    assert payload["stage_summary"]["stage_count"] == 11


def test_cli_blocks_output_under_data(tmp_path: Path) -> None:
    script = Path("scripts/build_daily_paper_master_learning_loop_orchestrator_v1.py")
    blocked_output = Path("data") / "daily_learning_orchestrator.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            ".",
            "--output",
            str(blocked_output),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["write_performed"] is False
    assert "output_path_under_blocked_project_area" in payload["validation_errors"]


def test_cli_can_write_only_to_explicit_external_path(tmp_path: Path) -> None:
    script = Path("scripts/build_daily_paper_master_learning_loop_orchestrator_v1.py")
    output = tmp_path / "orchestrator.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            ".",
            "--output",
            str(output),
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(completed.stdout)
    assert output.exists()
    assert payload["write_performed"] is True
    assert payload["writes_data"] is False
    assert payload["writes_runtime"] is False


def test_operator_decision_remains_blocked() -> None:
    report = build_daily_paper_master_learning_loop_orchestrator_report(project_root=".")
    operator = report["operator_decision"]
    assert operator["final_decision"] == "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH"
    assert operator["training_allowed"] is False
    assert operator["model_promotion_allowed"] is False
    assert operator["live_release_allowed"] is False


def test_readiness_policy_does_not_release_live_or_canary() -> None:
    report = build_daily_paper_master_learning_loop_orchestrator_report(project_root=".")
    readiness = report["readiness_policy"]
    assert readiness["daily_learning_orchestrator_is_not_readiness_evidence"] is True
    assert readiness["daily_learning_outputs_do_not_release_live"] is True
    assert readiness["daily_learning_outputs_do_not_release_canary"] is True


def test_static_source_has_no_trading_or_network_imports() -> None:
    module_path = Path("smartcrypto/research/daily_paper_master_learning_loop_orchestrator.py")
    script_path = Path("scripts/build_daily_paper_master_learning_loop_orchestrator_v1.py")
    source = module_path.read_text(encoding="utf-8") + script_path.read_text(encoding="utf-8")
    forbidden_tokens = [
        "import ccxt",
        "from ccxt",
        "import requests",
        "from requests",
        "import httpx",
        "from httpx",
        ".create_order(",
        ".cancel_order(",
        ".fetch_balance(",
        "subprocess.run(",
    ]
    for token in forbidden_tokens:
        assert token not in source
