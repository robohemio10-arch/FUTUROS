from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.daily_learning_loop_closeout_handover import (  # noqa: E402
    DECISION,
    SCHEMA_VERSION,
    build_canonical_daily_learning_stages,
    build_daily_learning_loop_closeout_handover,
    is_output_path_forbidden,
    is_source_payload_safe,
    validate_daily_learning_loop_closeout_handover,
)


def test_payload_has_expected_identity_and_closeout_state() -> None:
    payload = build_daily_learning_loop_closeout_handover(project_root=".")

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["project_name"] == "SMART FUTUROS"
    assert payload["status"] == "blocked"
    assert payload["decision"] == DECISION
    assert payload["daily_learning_loop_closed"] is True
    assert payload["daily_learning_loop_closeout_handover_created"] is True
    assert payload["validation_errors"] == []


def test_payload_keeps_research_readonly_paper_shadow_mode() -> None:
    payload = build_daily_learning_loop_closeout_handover(project_root=".")

    assert payload["research_only"] is True
    assert payload["read_only"] is True
    assert payload["paper_only"] is True
    assert payload["shadow_only"] is True
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["write_performed"] is False


def test_payload_denies_all_operational_authority() -> None:
    payload = build_daily_learning_loop_closeout_handover(project_root=".")

    for key in (
        "operational_authority",
        "handover_release_authority",
        "readiness_release_authority",
        "live_release_allowed",
        "canary_release_allowed",
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "changes_model",
    ):
        assert payload[key] is False, key


def test_payload_does_not_execute_or_update_runtime_components() -> None:
    payload = build_daily_learning_loop_closeout_handover(project_root=".")

    for key in (
        "registers_scheduler",
        "executes_scheduler",
        "executes_orchestrator",
        "executes_stage_builders",
        "runs_training",
        "runs_ocr",
        "runs_ai_shadow_incremental",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "updates_ai_shadow_policy",
        "updates_ai_shadow_thresholds",
        "updates_freqtrade",
        "updates_risk_manager",
        "updates_models",
    ):
        assert payload[key] is False, key


def test_payload_does_not_write_runtime_data_or_model_outputs() -> None:
    payload = build_daily_learning_loop_closeout_handover(project_root=".")

    for key in (
        "writes_runtime",
        "writes_data",
        "writes_reports",
        "writes_parquet",
        "writes_sqlite",
        "writes_ai_shadow_sqlite",
    ):
        assert payload[key] is False, key


def test_payload_does_not_apply_or_promote_model_rules_or_feedback() -> None:
    payload = build_daily_learning_loop_closeout_handover(project_root=".")

    for key in (
        "applies_shadow_rules",
        "applies_feedback_to_ai_shadow",
        "promotes_shadow_rules",
        "can_promote_model",
        "can_promote_rules",
        "can_apply_to_freqtrade",
        "can_apply_to_risk_manager",
    ):
        assert payload[key] is False, key


def test_canonical_stage_ledger_has_16_safe_stages() -> None:
    stages = build_canonical_daily_learning_stages()

    assert len(stages) == 16
    assert [stage["sequence"] for stage in stages] == list(range(1, 17))
    assert all(stage["safe_for_closeout"] is True for stage in stages)
    assert all(stage["decision"] == DECISION for stage in stages)


def test_stage_summary_is_fully_blocked_and_non_releasing() -> None:
    payload = build_daily_learning_loop_closeout_handover(project_root=".")
    summary = payload["stage_summary"]

    assert summary["stage_count"] == 16
    assert summary["safe_stage_count"] == 16
    assert summary["unsafe_stage_count"] == 0
    assert summary["decision_counts"] == {DECISION: 16}
    assert summary["status_counts"] == {"blocked": 16}
    assert summary["operational_authority_stage_count"] == 0
    assert summary["release_authority_stage_count"] == 0


def test_gate_matrix_has_no_failed_critical_gates() -> None:
    payload = build_daily_learning_loop_closeout_handover(project_root=".")

    assert payload["gate_summary"]["critical_gate_count"] == 6
    assert payload["gate_summary"]["critical_failed_gate_ids"] == []
    assert payload["gate_summary"]["failed_gate_count"] == 0


def test_readiness_decision_remains_blocked() -> None:
    payload = build_daily_learning_loop_closeout_handover(project_root=".")
    decision = payload["readiness_decision"]

    assert decision["final_decision"] == "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH"
    assert decision["daily_learning_closeout_accepted"] is True
    assert decision["closeout_release_authority"] is False
    assert decision["live_release_allowed"] is False
    assert decision["canary_release_allowed"] is False
    assert decision["model_promotion_allowed"] is False
    assert decision["shadow_rule_promotion_allowed"] is False


def test_readiness_policy_requires_separate_future_branches() -> None:
    payload = build_daily_learning_loop_closeout_handover(project_root=".")
    policy = payload["readiness_policy"]

    assert policy["closeout_handover_is_not_release_evidence"] is True
    assert policy["daily_learning_evidence_is_not_release_evidence"] is True
    assert policy["model_training_requires_separate_branch"] is True
    assert policy["model_promotion_requires_separate_registry_and_oos_review"] is True
    assert policy["real_scheduler_registration_requires_separate_branch"] is True


def test_source_payload_safety_accepts_only_blocked_readonly_no_write_payloads() -> None:
    safe_payload = {
        "schema_version": "example_v1",
        "status": "blocked",
        "decision": DECISION,
        "research_only": True,
        "read_only": True,
        "operational_authority": False,
        "write_performed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
    }
    unsafe_payload = dict(safe_payload, operational_authority=True)

    assert is_source_payload_safe(safe_payload) is True
    assert is_source_payload_safe(unsafe_payload) is False


def test_optional_source_payloads_are_summarized_without_runtime_authority() -> None:
    source = {
        "scheduler": {
            "schema_version": "daily_learning_scheduler_paper_v1",
            "status": "blocked",
            "decision": DECISION,
            "research_only": True,
            "read_only": True,
            "operational_authority": False,
            "readiness_release_authority": False,
            "write_performed": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
        }
    }
    payload = build_daily_learning_loop_closeout_handover(project_root=".", source_payloads=source)

    assert payload["source_summary"]["payload_loaded_count"] == 1
    assert payload["source_summary"]["safe_source_count"] == 1
    assert payload["source_summary"]["unsafe_source_count"] == 0
    assert payload["validation_errors"] == []


def test_forbidden_output_path_blocks_runtime_data_locations(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()

    assert is_output_path_forbidden(project_root, project_root / "data" / "reports" / "x.json") is True
    assert is_output_path_forbidden(project_root, project_root / "logs" / "x.json") is True
    assert is_output_path_forbidden(project_root, project_root / "handover" / "x.json") is False
    assert is_output_path_forbidden(project_root, tmp_path / "outside" / "x.json") is False


def test_cli_no_write_json_returns_blocked_payload() -> None:
    script = PROJECT_ROOT / "scripts" / "build_daily_learning_loop_closeout_handover_v1.py"
    result = subprocess.run(
        [sys.executable, str(script), "--project-root", str(PROJECT_ROOT), "--no-write", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == DECISION
    assert payload["write_performed"] is False
    assert payload["validation_errors"] == []
