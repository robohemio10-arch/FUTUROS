from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.ocr_shadow_research_evidence_closeout import (
    SCHEMA_VERSION,
    build_ocr_shadow_research_evidence_closeout_report,
    compute_closeout,
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
        "readiness_gate": {
            "schema_version": "paper_shadow_observation_readiness_gate_v1",
            "status": "blocked",
            "reason": "paper_shadow_readiness_gate_research_ready_but_operationally_blocked",
            "readiness_score": 100.0,
            "readiness_level": "RESEARCH_READY_BLOCKED",
            "readiness_blockers": [],
            "paper_observation_allowed": False,
            "ready_for_shadow_observation": False,
            **_base_safety(),
        },
    }


def test_no_runtime_read_by_default_returns_structured_closeout(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_evidence_closeout_report(project_root=tmp_path)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["closeout_decision"] == "MANTER_EM_RESEARCH"
    assert report["reason"] == "ocr_shadow_research_closeout_requires_explicit_runtime_read_or_in_memory_inputs"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["write_performed"] is False
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False


def test_missing_sources_are_reported_as_blockers(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    report = build_ocr_shadow_research_evidence_closeout_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        oos_validation_report=missing,
        shadow_observation_design_report=missing,
        shadow_observation_replay_report=missing,
        paper_closed_trades_attribution_report=missing,
        readiness_gate_report=missing,
    )

    assert report["status"] == "blocked"
    assert report["input_mode"] == "runtime_read_requested"
    assert report["source_status"] == "blocked"
    assert report["reason"] == "source_path_missing"
    assert report["evidence_sources_present"] == 0
    assert report["blocker_summary"]["blocker_count"] > 0


def test_complete_evidence_still_keeps_paper_observation_blocked(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_evidence_closeout_report(
        project_root=tmp_path,
        report_payloads=_complete_reports(),
    )

    assert report["status"] == "blocked"
    assert report["closeout_status"] == "research_closed_blocked"
    assert report["closeout_decision"] == "MANTER_EM_RESEARCH"
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False
    assert report["gate_summary"]["paper_observation_allowed"] is False


def test_closeout_rejects_any_operational_authority(tmp_path: Path) -> None:
    reports = _complete_reports()
    reports["observation_replay"]["operational_authority"] = True
    report = build_ocr_shadow_research_evidence_closeout_report(
        project_root=tmp_path,
        report_payloads=reports,
    )

    assert report["closeout_status"] == "blocked"
    assert "observation_replay_operational_authority_violates_closeout_contract" in report["blocker_summary"]["safety_blockers"]
    assert report["operational_authority"] is False


def test_closeout_rejects_rule_promotion(tmp_path: Path) -> None:
    reports = _complete_reports()
    reports["observation_design"]["can_promote_rules"] = True
    report = build_ocr_shadow_research_evidence_closeout_report(
        project_root=tmp_path,
        report_payloads=reports,
    )

    assert report["closeout_status"] == "blocked"
    assert "observation_design_can_promote_rules_violates_closeout_contract" in report["blocker_summary"]["safety_blockers"]
    assert report["can_promote_rules"] is False


def test_closeout_rejects_runtime_write(tmp_path: Path) -> None:
    reports = _complete_reports()
    reports["readiness_gate"]["writes_runtime"] = True
    report = build_ocr_shadow_research_evidence_closeout_report(
        project_root=tmp_path,
        report_payloads=reports,
    )

    assert report["closeout_status"] == "blocked"
    assert "readiness_gate_writes_runtime_violates_closeout_contract" in report["blocker_summary"]["safety_blockers"]
    assert report["writes_runtime"] is False


def test_recommended_next_action_is_safe() -> None:
    closeout = compute_closeout(_complete_reports())

    assert closeout["recommended_next_action"] == "manter_ciclo_encerrado_bloqueado_e_preparar_handover_para_revisao_humana"
    assert "promover" not in closeout["recommended_next_action"]
    assert "liberar" not in closeout["recommended_next_action"]


def test_forbidden_next_actions_include_runtime_and_orders(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_evidence_closeout_report(
        project_root=tmp_path,
        report_payloads=_complete_reports(),
    )

    forbidden = set(report["forbidden_next_actions"])
    assert "ativar paper observer" in forbidden
    assert "promover regra" in forbidden
    assert "alterar runtime" in forbidden
    assert "alterar RiskManager" in forbidden
    assert "alterar Freqtrade" in forbidden
    assert "alterar Qlib runtime" in forbidden
    assert "alterar IA Shadow runtime" in forbidden
    assert "enviar ordens" in forbidden


def test_write_requires_explicit_flag_and_only_writes_research_outputs(tmp_path: Path) -> None:
    no_write = build_ocr_shadow_research_evidence_closeout_report(
        project_root=tmp_path,
        report_payloads=_complete_reports(),
    )
    assert no_write["write_performed"] is False
    assert not (tmp_path / "data").exists()

    written = build_ocr_shadow_research_evidence_closeout_report(
        project_root=tmp_path,
        report_payloads=_complete_reports(),
        write=True,
        no_write=False,
    )

    json_output = tmp_path / "data" / "reports" / "ocr_shadow_research_evidence_closeout_v1.json"
    markdown_output = tmp_path / "data" / "reports" / "ocr_shadow_research_evidence_closeout_v1.md"
    assert written["write_requested"] is True
    assert written["write_performed"] is True
    assert written["output_path"] == "data/reports/ocr_shadow_research_evidence_closeout_v1.json"
    assert written["markdown_output_path"] == "data/reports/ocr_shadow_research_evidence_closeout_v1.md"
    assert written["writes_runtime"] is False
    assert written["writes_sqlite"] is False
    assert written["writes_parquet"] is False
    assert json_output.exists()
    assert markdown_output.exists()
    assert not (tmp_path / "data" / "runtime").exists()
    assert json.loads(json_output.read_text(encoding="utf-8"))["closeout_decision"] == "MANTER_EM_RESEARCH"
    assert "research-only" in markdown_output.read_text(encoding="utf-8")


def test_does_not_register_or_apply_shadow_rules(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_evidence_closeout_report(
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
    script = Path("scripts/build_ocr_shadow_research_evidence_closeout_v1.py")
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
    assert payload["closeout_decision"] == "MANTER_EM_RESEARCH"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["paper_observation_allowed"] is False
    assert payload["ready_for_shadow_observation"] is False
    assert payload["write_performed"] is False
