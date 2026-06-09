from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ops.ai_shadow_threshold_readiness import build_ai_shadow_threshold_readiness_evidence


def write_json(root: Path, relative: str, payload: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_missing_core_evidence_is_evidence_missing(tmp_path: Path) -> None:
    result = build_ai_shadow_threshold_readiness_evidence(project_root=tmp_path, no_write=True)
    assert result.report["status"] == "evidence_missing"
    assert result.report["threshold_readiness_evidence_approved"] is False
    assert result.report["live_release_allowed"] is False
    assert result.write_performed is False


def test_valid_decision_audit_can_approve_threshold_evidence(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/ai_shadow_filter_decision_db_audit_summary.json",
        {"status": "ok", "AI_ACCEPT": 600, "AI_REJECT": 400, "rows": 1000, "profit_factor": 1.25},
    )
    result = build_ai_shadow_threshold_readiness_evidence(project_root=tmp_path, no_write=True)
    assert result.report["status"] == "ok"
    assert result.report["threshold_readiness_evidence_approved"] is True
    assert result.report["live_release_allowed"] is False
    assert result.report["metrics"]["acceptance_rate"] == 0.6


def test_min_decisions_blocks(tmp_path: Path) -> None:
    write_json(tmp_path, "data/reports/ai_shadow_filter_decision_db_audit_summary.json", {"status": "ok", "AI_ACCEPT": 20, "AI_REJECT": 30, "rows": 50})
    result = build_ai_shadow_threshold_readiness_evidence(project_root=tmp_path, no_write=True, min_decisions=100)
    assert result.report["status"] == "blocked"
    assert any("min_decisions_not_reached" in reason for reason in result.report["blocking_reasons"])


def test_missing_accept_reject_counts_blocks(tmp_path: Path) -> None:
    write_json(tmp_path, "data/reports/ai_shadow_filter_decision_db_audit_summary.json", {"status": "ok", "rows": 1000})
    result = build_ai_shadow_threshold_readiness_evidence(project_root=tmp_path, no_write=True)
    assert result.report["status"] == "blocked"
    assert "accept_reject_counts_missing" in result.report["blocking_reasons"]


def test_low_profit_factor_blocks(tmp_path: Path) -> None:
    write_json(tmp_path, "data/reports/ai_shadow_financial_threshold_evaluation_report.json", {"status": "ok", "AI_ACCEPT": 550, "AI_REJECT": 450, "rows": 1000, "profit_factor": 0.9})
    result = build_ai_shadow_threshold_readiness_evidence(project_root=tmp_path, no_write=True, min_profit_factor=1.0)
    assert result.report["status"] == "blocked"
    assert any("profit_factor_below_minimum" in reason for reason in result.report["blocking_reasons"])


def test_acceptance_rate_bounds_block(tmp_path: Path) -> None:
    write_json(tmp_path, "data/reports/ai_shadow_filter_decision_db_audit_summary.json", {"status": "ok", "AI_ACCEPT": 995, "AI_REJECT": 5, "rows": 1000})
    result = build_ai_shadow_threshold_readiness_evidence(project_root=tmp_path, no_write=True, max_acceptance_rate=0.95)
    assert result.report["status"] == "blocked"
    assert any("acceptance_rate_above_minimum" not in reason for reason in result.report["blocking_reasons"])
    assert any("acceptance_rate_above_maximum" in reason for reason in result.report["blocking_reasons"])


def test_schema_block_is_detected(tmp_path: Path) -> None:
    write_json(tmp_path, "data/reports/ai_shadow_drift_monitor_report.json", {"status": "blocked", "validation_errors": ["schema drift detected"]})
    write_json(tmp_path, "data/reports/ai_shadow_filter_decision_db_audit_summary.json", {"status": "ok", "AI_ACCEPT": 600, "AI_REJECT": 400, "rows": 1000})
    result = build_ai_shadow_threshold_readiness_evidence(project_root=tmp_path, no_write=True)
    assert result.report["status"] == "blocked"
    assert "drift_or_schema_block" in result.report["root_cause_categories"]


def test_invalid_json_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "data/reports/ai_shadow_filter_decision_db_audit_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{invalid-json", encoding="utf-8")
    result = build_ai_shadow_threshold_readiness_evidence(project_root=tmp_path, no_write=True)
    assert result.report["status"] == "evidence_missing"
    assert result.report["invalid_evidence"]


def test_no_write_does_not_create_output(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/ai_shadow_threshold_readiness_evidence.json"
    result = build_ai_shadow_threshold_readiness_evidence(project_root=tmp_path, output=output, no_write=True)
    assert result.write_performed is False
    assert not output.exists()


def test_write_enabled_creates_output(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/ai_shadow_threshold_readiness_evidence.json"
    result = build_ai_shadow_threshold_readiness_evidence(project_root=tmp_path, output=output, no_write=False)
    assert result.write_performed is True
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "ai_shadow_threshold_readiness_evidence_v1"
    assert payload["live_release_allowed"] is False


def test_now_argument_is_stable(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = build_ai_shadow_threshold_readiness_evidence(project_root=tmp_path, no_write=True, now=now)
    assert result.report["generated_at"] == "2026-01-01T00:00:00Z"
