from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.ocr_shadow_paper_replay_attribution_source_diagnostics import (
    SCHEMA_VERSION,
    build_ocr_shadow_paper_replay_attribution_source_diagnostics_report,
    compute_source_diagnostics,
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


def _blocked_fixture_reports() -> dict[str, dict[str, object]]:
    return {
        "oos_validation": {
            "schema_version": "ocr_master_candle_positive_rule_oos_validation_v1",
            "status": "blocked",
            "reason": "positive_rule_oos_survivors_found_research_only",
            "oos_surviving_candidate_count": 2,
            "oos_shortlist": [{"candidate_id": "rule_1"}, {"candidate_id": "rule_2"}],
            **_base_safety(),
        },
        "observation_design": {
            "schema_version": "ocr_master_candle_shadow_observation_design_v1",
            "status": "blocked",
            "reason": "shadow_observation_design_completed_research_only_no_operational_authority",
            "observation_contract_version": "shadow_observation_contract_v1",
            "observation_record_count": 2,
            "observation_records": [{"survivor_rule_id": "rule_1"}, {"survivor_rule_id": "rule_2"}],
            **_base_safety(),
        },
        "observation_replay": {
            "schema_version": "ocr_master_candle_shadow_observation_replay_v1",
            "status": "blocked",
            "reason": "shadow_observation_replay_blocked_no_closed_trades",
            "survivor_count": 2,
            "closed_trade_count": 0,
            "survivor_source_path": "data/reports/ocr_master_candle_shadow_observation_design_v1.json",
            "trades_source_path": None,
            "replay_metrics": {
                "replay_trade_count": 0,
                "would_allow_count": 0,
                "would_block_count": 0,
                "replay_rows_sample": [],
            },
            **_base_safety(),
        },
        "paper_attribution": {
            "schema_version": "paper_closed_trades_shadow_rule_attribution_v1",
            "status": "blocked",
            "reason": "paper_shadow_attribution_blocked_no_closed_trades",
            "closed_trade_count": 0,
            "attributed_trade_count": 0,
            "unattributed_trade_count": 0,
            "replay_row_count": 0,
            "survivor_record_count": 0,
            "shadow_replay_source_path": None,
            "closed_trades_source_path": None,
            "attribution_table_sample": [],
            **_base_safety(),
        },
        "readiness_gate": {
            "schema_version": "paper_shadow_observation_readiness_gate_v1",
            "status": "blocked",
            "reason": "paper_shadow_readiness_gate_blocked_by_research_evidence_contract",
            "readiness_score": 75.0,
            "readiness_level": "BLOCKED",
            "readiness_blockers": ["replay_report_without_trades", "attribution_report_without_trades"],
            "paper_observation_allowed": False,
            "ready_for_shadow_observation": False,
            **_base_safety(),
        },
        "closeout": {
            "schema_version": "ocr_shadow_research_evidence_closeout_v1",
            "status": "blocked",
            "reason": "ocr_shadow_research_closeout_has_blockers",
            "blocker_summary": {
                "blockers": [
                    "readiness_gate:replay_report_without_trades",
                    "readiness_gate:attribution_report_without_trades",
                ]
            },
            **_base_safety(),
        },
        "evidence_pack": {
            "schema_version": "ocr_shadow_research_explicit_evidence_pack_v1",
            "status": "blocked",
            "reason": "explicit_evidence_pack_contains_blocked_research_stages",
            "stage_results": [
                {"stage_id": "observation_replay", "status": "blocked"},
                {"stage_id": "paper_attribution", "status": "blocked"},
            ],
            **_base_safety(),
        },
    }


def _complete_fixture_reports() -> dict[str, dict[str, object]]:
    reports = _blocked_fixture_reports()
    reports["observation_replay"] = {
        "schema_version": "ocr_master_candle_shadow_observation_replay_v1",
        "status": "blocked",
        "reason": "shadow_observation_replay_completed_research_only_no_operational_authority",
        "survivor_count": 2,
        "closed_trade_count": 2,
        "survivor_source_path": "data/reports/ocr_master_candle_shadow_observation_design_v1.json",
        "trades_source_path": "data/reports/paper_closed_trades.json",
        "replay_metrics": {
            "replay_trade_count": 2,
            "would_allow_count": 1,
            "would_block_count": 1,
            "replay_rows_sample": [
                {
                    "trade_id": "t1",
                    "order_id": "o1",
                    "fingerprint_operacional": "fp1",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "open_time": "2026-01-01T00:00:00Z",
                    "close_time": "2026-01-01T00:10:00Z",
                    "net_pnl": 10.0,
                    "matched_survivor_rule_id": "rule_1",
                }
            ],
        },
        **_base_safety(),
    }
    reports["paper_attribution"] = {
        "schema_version": "paper_closed_trades_shadow_rule_attribution_v1",
        "status": "blocked",
        "reason": "paper_shadow_attribution_completed_research_only_no_operational_authority",
        "closed_trade_count": 2,
        "attributed_trade_count": 2,
        "unattributed_trade_count": 0,
        "replay_row_count": 2,
        "survivor_record_count": 0,
        "shadow_replay_source_path": "data/reports/ocr_master_candle_shadow_observation_replay_v1.json",
        "closed_trades_source_path": "data/reports/paper_closed_trades.json",
        "attribution_table_sample": [
            {
                "trade_id": "t1",
                "order_id": "o1",
                "fingerprint_operacional": "fp1",
                "symbol": "BTCUSDT",
                "side": "long",
                "open_time": "2026-01-01T00:00:00Z",
                "close_time": "2026-01-01T00:10:00Z",
                "net_pnl": 10.0,
                "matched_survivor_rule_id": "rule_1",
            }
        ],
        **_base_safety(),
    }
    reports["readiness_gate"] = {
        "schema_version": "paper_shadow_observation_readiness_gate_v1",
        "status": "blocked",
        "reason": "paper_shadow_readiness_gate_research_ready_but_operationally_blocked",
        "readiness_score": 100.0,
        "readiness_level": "RESEARCH_READY_BLOCKED",
        "readiness_blockers": [],
        "paper_observation_allowed": False,
        "ready_for_shadow_observation": False,
        **_base_safety(),
    }
    reports["closeout"] = {
        "schema_version": "ocr_shadow_research_evidence_closeout_v1",
        "status": "blocked",
        "reason": "ocr_shadow_research_cycle_closed_research_only_operationally_blocked",
        "blocker_summary": {"blockers": []},
        **_base_safety(),
    }
    return reports


def test_no_runtime_read_by_default_blocks_safely(tmp_path: Path) -> None:
    report = build_ocr_shadow_paper_replay_attribution_source_diagnostics_report(project_root=tmp_path)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["reason"] == "source_diagnostics_requires_explicit_runtime_read_or_in_memory_inputs"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["write_performed"] is False
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False


def test_missing_reports_are_reported_structurally(tmp_path: Path) -> None:
    report = build_ocr_shadow_paper_replay_attribution_source_diagnostics_report(
        project_root=tmp_path,
        allow_runtime_read=True,
    )

    assert report["status"] == "blocked"
    assert report["source_status"] == "blocked"
    assert report["reason"] == "missing_or_invalid_sources"
    assert "observation_replay" in report["missing_sources"]
    assert "paper_attribution" in report["missing_sources"]
    assert report["report_sha256"]["observation_replay"] is None


def test_extracts_replay_blockers_from_fixture() -> None:
    diagnostics = compute_source_diagnostics(_blocked_fixture_reports(), [])

    replay = diagnostics["replay_diagnostics"]
    assert replay["closed_trade_count"] == 0
    assert replay["replay_trade_count"] == 0
    assert "replay_report_without_trades" in replay["blockers"]
    assert "replay_missing_closed_trades_source" in replay["blockers"]
    assert "observation_replay_had_zero_closed_trades_to_replay" in diagnostics["root_cause_candidates"]


def test_extracts_attribution_blockers_from_fixture() -> None:
    diagnostics = compute_source_diagnostics(_blocked_fixture_reports(), [])

    attribution = diagnostics["attribution_diagnostics"]
    assert attribution["closed_trade_count"] == 0
    assert attribution["attributed_trade_count"] == 0
    assert "paper_attribution_without_attributed_trades" in attribution["blockers"]
    assert "attribution_missing_shadow_replay_source" in attribution["blockers"]
    assert "paper_attribution_had_zero_attributed_trades" in diagnostics["root_cause_candidates"]


def test_detects_missing_closed_trades_source() -> None:
    diagnostics = compute_source_diagnostics(_blocked_fixture_reports(), [])

    assert "observation_replay_was_not_given_closed_trades_source" in diagnostics["root_cause_candidates"]
    assert "paper_attribution_was_not_given_closed_trades_source" in diagnostics["root_cause_candidates"]
    assert diagnostics["replay_diagnostics"]["received_closed_trades_source"] is False
    assert diagnostics["attribution_diagnostics"]["received_closed_trades_source"] is False


def test_detects_missing_join_fields() -> None:
    reports = _complete_fixture_reports()
    reports["paper_attribution"]["attribution_table_sample"] = [{"symbol": "BTCUSDT", "side": "long", "net_pnl": 1.0}]
    diagnostics = compute_source_diagnostics(reports, [])

    missing = diagnostics["missing_fields"]["attribution_rows"]
    assert "trade_id" in missing
    assert "order_id" in missing
    assert "fingerprint_operacional" in missing
    assert "attribution_rows_missing_stable_join_identifiers" in diagnostics["root_cause_candidates"]


def test_detects_contract_mismatch_between_replay_and_attribution() -> None:
    reports = _complete_fixture_reports()
    reports["paper_attribution"]["attributed_trade_count"] = 0
    diagnostics = compute_source_diagnostics(reports, [])

    assert "replay_trades_not_attributed_to_paper_closed_trades" in diagnostics["contract_mismatches"]
    assert "contract_mismatch:replay_trades_not_attributed_to_paper_closed_trades" in diagnostics["root_cause_candidates"]


def test_complete_fixture_still_keeps_research_only_blocked(tmp_path: Path) -> None:
    report = build_ocr_shadow_paper_replay_attribution_source_diagnostics_report(
        project_root=tmp_path,
        report_payloads=_complete_fixture_reports(),
    )

    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False
    assert report["operational_authority"] is False
    assert report["root_cause_candidates"] == []


def test_write_requires_explicit_flag_and_only_writes_reports(tmp_path: Path) -> None:
    no_write = build_ocr_shadow_paper_replay_attribution_source_diagnostics_report(
        project_root=tmp_path,
        report_payloads=_blocked_fixture_reports(),
    )
    assert no_write["write_performed"] is False
    assert not (tmp_path / "data").exists()

    written = build_ocr_shadow_paper_replay_attribution_source_diagnostics_report(
        project_root=tmp_path,
        report_payloads=_blocked_fixture_reports(),
        write=True,
        no_write=False,
    )

    json_output = tmp_path / "data" / "reports" / "ocr_shadow_paper_replay_attribution_source_diagnostics_v1.json"
    markdown_output = tmp_path / "data" / "reports" / "ocr_shadow_paper_replay_attribution_source_diagnostics_v1.md"
    assert written["write_performed"] is True
    assert written["output_path"] == "data/reports/ocr_shadow_paper_replay_attribution_source_diagnostics_v1.json"
    assert written["markdown_output_path"] == "data/reports/ocr_shadow_paper_replay_attribution_source_diagnostics_v1.md"
    assert json_output.exists()
    assert markdown_output.exists()
    assert not (tmp_path / "data" / "runtime").exists()
    assert json.loads(json_output.read_text(encoding="utf-8"))["decision"] == "MANTER_EM_RESEARCH"
    assert "research-only" in markdown_output.read_text(encoding="utf-8")


def test_no_operational_authority_flags(tmp_path: Path) -> None:
    report = build_ocr_shadow_paper_replay_attribution_source_diagnostics_report(
        project_root=tmp_path,
        report_payloads=_blocked_fixture_reports(),
    )

    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["operational_authority"] is False
    assert report["can_promote_rules"] is False
    assert report["can_apply_to_freqtrade"] is False
    assert report["can_apply_to_risk_manager"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False


def test_does_not_register_or_apply_shadow_rules(tmp_path: Path) -> None:
    report = build_ocr_shadow_paper_replay_attribution_source_diagnostics_report(
        project_root=tmp_path,
        report_payloads=_blocked_fixture_reports(),
    )

    assert report["registers_shadow_rules"] is False
    assert report["applies_shadow_rules"] is False
    assert report["updates_ai_shadow_runtime"] is False
    assert report["updates_freqtrade"] is False
    assert report["updates_risk_manager"] is False
    assert report["gate_summary"]["result_can_be_used_for_operations"] is False


def test_cli_no_runtime_json_executes() -> None:
    script = Path("scripts/build_ocr_shadow_paper_replay_attribution_source_diagnostics_v1.py")
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
