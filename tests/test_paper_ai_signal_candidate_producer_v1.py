from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.paper_ai_signal_candidate_producer.signal_candidate_producer import (
    build_paper_ai_signal_candidate_producer_v1,
)


SCRIPT = Path("scripts/build_paper_ai_signal_candidate_producer_v1.py")


def registry_payload(*, status: str = "ok_research_review_only", eligible_count: int = 1) -> dict:
    gate_status = status
    candidate_gate_status = "eligible_for_research_review" if eligible_count else "blocked_missing_evidence"
    return {
        "status": "ok" if gate_status == "ok_research_review_only" else "blocked",
        "registry_gate_status": gate_status,
        "candidate_count": 1,
        "eligible_candidate_count": eligible_count,
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "candidate_type": "ensemble_threshold_candidate",
                "source_id": "ensemble_threshold_calibration",
                "symbol_scope": ["BTCUSDT"],
                "side_scope": ["long"],
                "regime_scope": ["normal"],
                "threshold": 0.55,
                "score_metric_summary": {
                    "recommended_candidate": {
                        "threshold": 0.55,
                        "selected_count": 3,
                        "average_expected_value": 0.3,
                        "pnl_selected": 0.9,
                        "target_label": 1,
                    }
                },
                "evidence_status": "available",
                "gate_status": candidate_gate_status,
                "blocked_reasons": [] if eligible_count else ["blocked_missing_evidence"],
            }
        ],
    }


def valid_payloads(*, registry_status: str = "ok_research_review_only", eligible_count: int = 1) -> dict[str, dict]:
    return {
        "registry_gate": registry_payload(status=registry_status, eligible_count=eligible_count),
        "ensemble_threshold_calibration": {"status": "ok", "decision": "MANTER_EM_RESEARCH"},
        "qlib_trainer": {"status": "ok", "trainer_status": "ok"},
        "ai_shadow_quality_veto": {"status": "ok", "trainer_status": "ok"},
        "paper_autotrain_feedback_loop": {"status": "ok", "decision": "MANTER_EM_RESEARCH"},
        "target_store": {"status": "ok", "target_store_hash": "target-hash"},
        "drift_monitor": {"status": "ok", "blockers": []},
        "execution_cost_gate": {"status": "ok", "blockers": []},
    }


def test_report_is_json_serializable(tmp_path: Path) -> None:
    report = build_paper_ai_signal_candidate_producer_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(),
    )

    encoded = json.dumps(report, sort_keys=True)
    assert "paper_ai_signal_candidate_producer_v1" in encoded


def test_safety_flags_disable_runtime_model_registry_orders_and_signal_file(tmp_path: Path) -> None:
    report = build_paper_ai_signal_candidate_producer_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(),
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    for key, value in report["safety_flags"].items():
        if key in {"research_only", "paper_only", "shadow_only", "read_only"}:
            assert value is True
        else:
            assert value is False
        assert report[key] == value


def test_domain_no_write_does_not_materialize_files(tmp_path: Path) -> None:
    report = build_paper_ai_signal_candidate_producer_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(),
        write=True,
    )

    assert report["write_requested"] is True
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "paper_ai_signal_candidate_producer_v1.json").exists()
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))


def test_cli_write_writes_only_json_and_markdown_under_data_reports(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--write", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    json_report = tmp_path / "data" / "reports" / "paper_ai_signal_candidate_producer_v1.json"
    markdown_report = tmp_path / "data" / "reports" / "paper_ai_signal_candidate_producer_v1.md"

    assert payload["write_performed"] is True
    assert json_report.is_file()
    assert markdown_report.is_file()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "data" / "models").exists()
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))


def test_missing_registry_gate_blocks_without_exception(tmp_path: Path) -> None:
    payloads = valid_payloads()
    payloads.pop("registry_gate")

    report = build_paper_ai_signal_candidate_producer_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert report["status"] == "blocked"
    assert "missing_required_source:data/reports/paper_model_candidate_registry_gate_v1.json" in report["blockers"]
    assert report["actionable_signal_candidate_count"] == 0


def test_blocked_registry_gate_blocks_actionable_production(tmp_path: Path) -> None:
    report = build_paper_ai_signal_candidate_producer_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(registry_status="blocked_no_eligible_candidates", eligible_count=0),
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "no_registry_eligible_candidates"
    assert report["registry_gate_status"] == "blocked_no_eligible_candidates"
    assert report["actionable_signal_candidate_count"] == 0
    assert all(candidate["signal_actionability"] == "blocked" for candidate in report["signal_candidates"])


def test_zero_registry_eligible_candidates_returns_blocked(tmp_path: Path) -> None:
    report = build_paper_ai_signal_candidate_producer_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(eligible_count=0),
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "no_registry_eligible_candidates"
    assert report["registry_eligible_candidate_count"] == 0


def test_observational_candidate_never_eligible_for_freqtrade(tmp_path: Path) -> None:
    report = build_paper_ai_signal_candidate_producer_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(),
    )
    candidate = report["signal_candidates"][0]

    assert candidate["signal_actionability"] == "research_observation_only"
    assert candidate["eligible_for_research_observation"] is True
    assert candidate["eligible_for_paper_selector"] is False
    assert candidate["eligible_for_freqtrade"] is False
    assert candidate["updates_freqtrade"] is False
    assert candidate["sends_orders"] is False


def test_drift_blocked_blocks_signal_candidate(tmp_path: Path) -> None:
    payloads = valid_payloads()
    payloads["drift_monitor"] = {"status": "blocked", "blockers": ["critical_drift"]}

    report = build_paper_ai_signal_candidate_producer_v1(project_root=tmp_path, evidence_payloads=payloads)
    candidate = report["signal_candidates"][0]

    assert report["status"] == "blocked"
    assert "drift_gate_blocked" in candidate["blocked_reasons"]
    assert candidate["signal_actionability"] == "blocked"


def test_execution_cost_blocked_blocks_signal_candidate(tmp_path: Path) -> None:
    payloads = valid_payloads()
    payloads["execution_cost_gate"] = {"status": "blocked", "blockers": ["execution_cost_gate_blocked"]}

    report = build_paper_ai_signal_candidate_producer_v1(project_root=tmp_path, evidence_payloads=payloads)
    candidate = report["signal_candidates"][0]

    assert report["status"] == "blocked"
    assert "execution_cost_gate_blocked" in candidate["blocked_reasons"]
    assert candidate["signal_actionability"] == "blocked"


def test_forbidden_outcome_fields_are_not_used_in_signal_summary(tmp_path: Path) -> None:
    report = build_paper_ai_signal_candidate_producer_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(),
    )
    summary = report["signal_candidates"][0]["ensemble_score_summary"]
    encoded = json.dumps(summary, sort_keys=True)

    for forbidden in ("label", "target", "outcome", "pnl", "profit", "win_loss", "future_return", "expected_value"):
        assert forbidden not in encoded.lower()


def test_decision_always_manter_em_research(tmp_path: Path) -> None:
    report = build_paper_ai_signal_candidate_producer_v1(
        project_root=tmp_path,
        evidence_payloads=valid_payloads(),
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["release_allowed"] is False
    assert all(candidate["eligible_for_freqtrade"] is False for candidate in report["signal_candidates"])


def test_cli_real_project_no_write_executes_without_runtime_mutation() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", ".", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["write_performed"] is False
    assert payload["writes_signal_file"] is False
    assert payload["sends_orders"] is False
    assert payload["exchange_private_access"] is False


def test_boundary_auditor_has_no_critical_or_high_findings() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/audit_state_execution_ledger_boundary.py", "--project-root", ".", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    findings = payload.get("boundary_findings", [])
    critical_or_high = [
        item
        for item in findings
        if item.get("severity") in {"critical", "high"}
    ]
    assert critical_or_high == []
