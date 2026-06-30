from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.ocr_master_candle_shadow_observation_design.observation_design import (
    OBSERVATION_CONTRACT_VERSION,
    SCHEMA_VERSION,
    build_observation_records,
    build_shadow_observation_design_report,
)


def _survivor(candidate_id: str = "include__symbol_norm_ETHUSDT__side_norm_short") -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "expression": "symbol_norm == 'ETHUSDT' AND side_norm == 'short'",
        "dimensions": ["symbol_norm", "side_norm"],
        "values": ["ETHUSDT", "short"],
        "folds_evaluated": 4,
        "folds_passed": 3,
        "oos_pass_ratio": 0.75,
        "aggregate_oos_metrics": {
            "trade_count": 533,
            "net_pnl": 160.4902946,
            "profit_factor": 1.395779536,
            "mean_pnl": 0.3011074946,
            "win_rate": 0.7110694184,
        },
        "insample_candidate": {
            "baseline_mean_pnl": 0.202677461,
            "baseline_profit_factor": 1.2521660343,
        },
        "fold_results": [
            {"mean_pnl_lift": 0.2300414597},
            {"mean_pnl_lift": 0.0883764137},
            {"mean_pnl_lift": -0.2577372356},
            {"mean_pnl_lift": 0.3375740142},
        ],
        "survives_oos_research_gate": True,
        "ready_for_candidate_registry": False,
        "paper_observation_allowed": False,
        "can_promote_rules": False,
        "operational_authority": False,
    }


def test_no_runtime_read_by_default_blocks_safely(tmp_path: Path) -> None:
    report = build_shadow_observation_design_report(project_root=tmp_path, no_write=True)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["reason"] == "shadow_observation_design_requires_explicit_survivor_source_or_runtime_read"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["survivor_count"] == 0
    assert report["write_performed"] is False
    assert report["research_only"] is True
    assert report["read_only"] is True
    assert report["operational_authority"] is False
    assert report["sends_orders"] is False


def test_observation_contract_contains_required_fields() -> None:
    report = build_shadow_observation_design_report(
        project_root=".",
        survivor_results=[_survivor()],
        no_write=True,
    )

    required = {
        "survivor_rule_id",
        "survivor_expression",
        "would_allow",
        "would_block",
        "opportunity_score",
        "expected_value_delta",
        "shadow_observation_reason",
    }
    assert report["observation_contract_version"] == OBSERVATION_CONTRACT_VERSION
    assert required.issubset(set(report["observation_fields"]))
    assert report["would_allow_semantics"]
    assert report["would_block_semantics"]
    assert report["opportunity_score_contract"]
    assert report["expected_value_delta_contract"]
    assert report["survivor_count"] == 1
    assert required.issubset(set(report["observation_records"][0]))


def test_survivors_are_research_only() -> None:
    report = build_shadow_observation_design_report(
        project_root=".",
        survivor_results=[_survivor()],
        no_write=True,
    )

    record = report["observation_records"][0]
    assert record["would_allow"] is True
    assert record["would_block"] is False
    assert record["research_only"] is True
    assert record["read_only"] is True
    assert record["operational_authority"] is False
    assert record["paper_observation_allowed"] is False
    assert record["ready_for_candidate_registry"] is False
    assert record["can_promote_rules"] is False


def test_no_operational_authority_flags() -> None:
    report = build_shadow_observation_design_report(
        project_root=".",
        survivor_results=[_survivor()],
        no_write=True,
    )

    blocked_flags = [
        "operational_authority",
        "can_apply_to_freqtrade",
        "can_apply_to_risk_manager",
        "can_promote_rules",
        "can_promote_model",
        "sends_orders",
        "changes_risk",
        "changes_model",
        "exchange_private_access",
        "updates_freqtrade",
        "updates_risk_manager",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "registers_shadow_rules",
        "applies_shadow_rules",
    ]
    for flag in blocked_flags:
        assert report[flag] is False
        assert report["safety_flags"][flag] is False


def test_write_requires_explicit_flag_and_only_writes_research_report(tmp_path: Path) -> None:
    no_write_report = build_shadow_observation_design_report(
        project_root=tmp_path,
        survivor_results=[_survivor()],
        no_write=True,
    )
    assert no_write_report["write_performed"] is False
    assert not (tmp_path / "data").exists()

    write_report = build_shadow_observation_design_report(
        project_root=tmp_path,
        survivor_results=[_survivor()],
        write=True,
        no_write=False,
    )

    output = tmp_path / "data" / "reports" / "ocr_master_candle_shadow_observation_design_v1.json"
    assert write_report["write_performed"] is True
    assert write_report["writes_reports"] is True
    assert write_report["writes_runtime"] is False
    assert write_report["writes_sqlite"] is False
    assert write_report["writes_parquet"] is False
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["survivor_count"] == 1


def test_missing_previous_oos_report_returns_structured_blocked(tmp_path: Path) -> None:
    missing = tmp_path / "data" / "reports" / "missing_oos.json"
    report = build_shadow_observation_design_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        oos_report=missing,
        no_write=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "shadow_observation_design_blocked_missing_previous_oos_survivor_source"
    assert report["previous_oos_source"]["source_status"] == "missing"
    assert report["survivor_count"] == 0
    assert report["write_performed"] is False


def test_observation_score_is_deterministic() -> None:
    records_a = build_observation_records([_survivor(), _survivor("include__hour_1")])
    records_b = build_observation_records([_survivor(), _survivor("include__hour_1")])

    assert records_a == records_b
    assert all(0.0 <= record["opportunity_score"] <= 1.0 for record in records_a)
    assert all(record["expected_value_delta"] is not None for record in records_a)


def test_does_not_register_or_apply_shadow_rules() -> None:
    report = build_shadow_observation_design_report(
        project_root=".",
        survivor_results=[_survivor()],
        no_write=True,
    )

    gate_ids = {gate["gate_id"]: gate for gate in report["gate_matrix"]}
    assert gate_ids["no_registry_or_rule_application"]["passed"] is True
    assert report["registers_shadow_rules"] is False
    assert report["applies_shadow_rules"] is False
    assert report["registers_candidate_rules"] is False
    assert report["ready_for_candidate_registry"] is False


def test_cli_no_runtime_json_executes() -> None:
    script = Path("scripts/build_ocr_master_candle_shadow_observation_design_v1.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--no-write", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["write_performed"] is False
