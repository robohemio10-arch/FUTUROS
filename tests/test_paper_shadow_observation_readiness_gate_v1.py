from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.paper_shadow_observation_readiness_gate import (
    SCHEMA_VERSION,
    build_paper_shadow_observation_readiness_gate_report,
    compute_readiness_gate,
)


def _base_safety() -> dict[str, object]:
    return {
        "decision": "MANTER_EM_RESEARCH",
        "operational_authority": False,
        "sends_orders": False,
        "changes_risk": False,
        "can_promote_rules": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "writes_runtime": False,
    }


def _complete_reports() -> dict[str, dict[str, object]]:
    return {
        "oos_validation": {
            "schema_version": "ocr_master_candle_positive_rule_oos_validation_v1",
            "status": "blocked",
            "reason": "positive_rule_oos_survivors_found_research_only",
            "oos_surviving_candidate_count": 3,
            "oos_shortlist": [{"candidate_id": "rule_1"}, {"candidate_id": "rule_2"}, {"candidate_id": "rule_3"}],
            **_base_safety(),
        },
        "observation_design": {
            "schema_version": "ocr_master_candle_shadow_observation_design_v1",
            "status": "blocked",
            "reason": "shadow_observation_design_completed_research_only_no_operational_authority",
            "observation_contract_version": "shadow_observation_contract_v1",
            "observation_record_count": 3,
            **_base_safety(),
        },
        "observation_replay": {
            "schema_version": "ocr_master_candle_shadow_observation_replay_v1",
            "status": "blocked",
            "reason": "shadow_observation_replay_completed_research_only_no_operational_authority",
            "replay_metrics": {
                "replay_trade_count": 4,
                "would_allow_count": 2,
                "would_block_count": 2,
            },
            **_base_safety(),
        },
        "paper_attribution": {
            "schema_version": "paper_closed_trades_shadow_rule_attribution_v1",
            "status": "blocked",
            "reason": "paper_shadow_attribution_completed_research_only_no_operational_authority",
            "closed_trade_count": 4,
            "attributed_trade_count": 4,
            "would_allow_count": 2,
            "would_block_count": 2,
            **_base_safety(),
        },
    }


def test_no_runtime_read_by_default_blocks_safely(tmp_path: Path) -> None:
    report = build_paper_shadow_observation_readiness_gate_report(project_root=tmp_path)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["reason"] == "paper_shadow_readiness_gate_requires_explicit_runtime_read_or_in_memory_inputs"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["source_status"] == "blocked"
    assert report["write_performed"] is False
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False


def test_missing_sources_return_structured_blocked(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    report = build_paper_shadow_observation_readiness_gate_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        oos_validation_report=missing,
        shadow_observation_design_report=missing,
        shadow_observation_replay_report=missing,
        paper_closed_trades_attribution_report=missing,
    )

    assert report["status"] == "blocked"
    assert report["input_mode"] == "runtime_read_requested"
    assert report["source_status"] == "blocked"
    assert report["reason"] == "source_path_missing"
    assert report["write_performed"] is False


def test_readiness_gate_requires_all_evidence_sources(tmp_path: Path) -> None:
    partial = {"oos_validation": _complete_reports()["oos_validation"]}
    report = build_paper_shadow_observation_readiness_gate_report(
        project_root=tmp_path,
        report_payloads=partial,
    )

    assert report["status"] == "blocked"
    assert report["source_status"] == "blocked"
    assert report["readiness_level"] == "INCOMPLETE"
    assert "observation_design_missing" in report["readiness_blockers"]
    assert "observation_replay_missing" in report["readiness_blockers"]
    assert "paper_attribution_missing" in report["readiness_blockers"]


def test_readiness_gate_rejects_any_operational_authority(tmp_path: Path) -> None:
    reports = _complete_reports()
    reports["observation_replay"]["operational_authority"] = True
    report = build_paper_shadow_observation_readiness_gate_report(
        project_root=tmp_path,
        report_payloads=reports,
    )

    assert report["status"] == "blocked"
    assert report["readiness_level"] == "BLOCKED"
    assert "observation_replay_operational_authority_violates_readiness_contract" in report["readiness_blockers"]
    assert report["operational_authority"] is False
    assert report["gate_summary"]["operational_authority"] is False


def test_readiness_gate_rejects_rule_promotion(tmp_path: Path) -> None:
    reports = _complete_reports()
    reports["observation_design"]["can_promote_rules"] = True
    report = build_paper_shadow_observation_readiness_gate_report(
        project_root=tmp_path,
        report_payloads=reports,
    )

    assert report["status"] == "blocked"
    assert "observation_design_can_promote_rules_violates_readiness_contract" in report["readiness_blockers"]
    assert report["can_promote_rules"] is False
    assert report["gate_summary"]["can_promote_rules"] is False


def test_readiness_score_is_deterministic() -> None:
    reports = _complete_reports()
    gate_a = compute_readiness_gate(reports)
    gate_b = compute_readiness_gate(reports)

    assert gate_a == gate_b
    assert gate_a["readiness_score"] == 100.0
    assert gate_a["readiness_level"] == "RESEARCH_READY_BLOCKED"
    assert gate_a["oos_survivor_count"] == 3
    assert gate_a["replay_trade_count"] == 4
    assert gate_a["attribution_trade_count"] == 4


def test_ready_for_shadow_observation_remains_false_even_when_evidence_is_complete(tmp_path: Path) -> None:
    report = build_paper_shadow_observation_readiness_gate_report(
        project_root=tmp_path,
        report_payloads=_complete_reports(),
    )

    assert report["status"] == "blocked"
    assert report["readiness_level"] == "RESEARCH_READY_BLOCKED"
    assert report["ready_for_shadow_observation"] is False
    assert report["gate_summary"]["ready_for_shadow_observation"] is False
    assert report["reason"] == "paper_shadow_readiness_gate_research_ready_but_operationally_blocked"


def test_paper_observation_allowed_remains_false(tmp_path: Path) -> None:
    report = build_paper_shadow_observation_readiness_gate_report(
        project_root=tmp_path,
        report_payloads=_complete_reports(),
    )

    assert report["paper_observation_allowed"] is False
    assert report["safety_flags"]["paper_observation_allowed"] is False
    assert report["gate_summary"]["paper_observation_allowed"] is False


def test_write_requires_explicit_flag_and_only_writes_research_json(tmp_path: Path) -> None:
    no_write = build_paper_shadow_observation_readiness_gate_report(
        project_root=tmp_path,
        report_payloads=_complete_reports(),
    )
    assert no_write["write_performed"] is False
    assert not (tmp_path / "data").exists()

    written = build_paper_shadow_observation_readiness_gate_report(
        project_root=tmp_path,
        report_payloads=_complete_reports(),
        write=True,
        no_write=False,
    )

    output = tmp_path / "data" / "reports" / "paper_shadow_observation_readiness_gate_v1.json"
    assert written["write_requested"] is True
    assert written["write_performed"] is True
    assert written["output_path"] == "data/reports/paper_shadow_observation_readiness_gate_v1.json"
    assert written["writes_runtime"] is False
    assert written["writes_sqlite"] is False
    assert written["writes_parquet"] is False
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert not (tmp_path / "data" / "runtime").exists()


def test_does_not_register_or_apply_shadow_rules(tmp_path: Path) -> None:
    report = build_paper_shadow_observation_readiness_gate_report(
        project_root=tmp_path,
        report_payloads=_complete_reports(),
    )

    assert report["registers_shadow_rules"] is False
    assert report["applies_shadow_rules"] is False
    assert report["updates_ai_shadow_runtime"] is False
    assert report["updates_freqtrade"] is False
    assert report["updates_risk_manager"] is False
    assert report["gate_summary"]["result_can_be_used_for_operations"] is False


def test_cli_no_runtime_json_executes() -> None:
    script = Path("scripts/build_paper_shadow_observation_readiness_gate_v1.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--no-write", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["paper_observation_allowed"] is False
    assert payload["ready_for_shadow_observation"] is False
    assert payload["write_performed"] is False
