from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ops.monte_carlo_no_trade_recovery import build_monte_carlo_no_trade_recovery_diagnostics


def write_json(root: Path, relative: str, payload: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_without_core_evidence_returns_evidence_missing(tmp_path: Path) -> None:
    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "evidence_missing"
    assert result.report["live_release_allowed"] is False
    assert result.report["canary_release_allowed"] is False
    assert result.write_performed is False


def test_no_trade_status_blocks_with_marker(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/monte_carlo_risk_simulation_report.json",
        {"status": "no_trade", "reason": "risk_budget_block", "live_release_allowed": False},
    )

    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert result.report["no_trade_detected"] is True
    assert result.report["no_trade_markers"]
    assert "monte_carlo_or_readiness_no_trade_detected" in result.report["blocking_reasons"]
    assert result.report["live_release_allowed"] is False


def test_risk_budget_drawdown_block_is_classified(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/monte_carlo_risk_budget_policy_report.json",
        {"status": "blocked", "blocking_reasons": ["max_drawdown_exceeded", "risk_of_ruin_high"]},
    )

    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert "risk_budget_or_drawdown_block" in result.report["root_cause_categories"]
    assert any("drawdown" in action.lower() for action in result.report["recovery_actions"])


def test_market_data_stale_is_classified(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/runtime_evidence_pack_v2.json",
        {"status": "blocked", "blocking_reasons": ["input_data_stale", "market_data_health_report_stale"]},
    )

    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True)

    assert "market_data_stale_or_missing" in result.report["root_cause_categories"]
    assert result.report["live_release_allowed"] is False


def test_soak_continuity_block_is_classified(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_continuity_audit.json",
        {"status": "blocked", "blocking_reasons": ["required_soak_days_not_reached", "critical_gap_count_gt_zero"]},
    )
    write_json(tmp_path, "data/reports/readiness_snapshot_v2.json", {"status": "blocked"})

    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True)

    assert "soak_or_continuity_block" in result.report["root_cause_categories"]
    assert result.report["status"] == "blocked"


def test_ai_shadow_threshold_block_is_classified(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/ai_shadow_filter_decision_db_audit_summary.json",
        {"status": "blocked", "reason": "AI_SHADOW threshold quality reject distribution invalid"},
    )
    write_json(tmp_path, "data/reports/monte_carlo_risk_simulation_report.json", {"status": "ok"})

    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True)

    assert "ai_shadow_quality_gate_block" in result.report["root_cause_categories"]


def test_prediction_absence_is_classified(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/phase13_active_signals_summary.json",
        {"status": "blocked", "reason": "no_signal from qlib prediction pipeline"},
    )
    write_json(tmp_path, "data/reports/monte_carlo_risk_simulation_report.json", {"status": "ok"})

    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True)

    assert "prediction_or_signal_absence" in result.report["root_cause_categories"]


def test_safety_violation_blocks(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/runtime_evidence_pack_v2.json",
        {"status": "ok", "safety_flags": {"sends_orders": True, "exchange_private_access": True}},
    )

    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert any("sends_orders=true" in reason for reason in result.report["blocking_reasons"])
    assert any("exchange_private_access=true" in reason for reason in result.report["blocking_reasons"])


def test_live_release_allowed_true_blocks(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/readiness_snapshot_v2.json",
        {"status": "ok", "live_release_allowed": True},
    )

    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert "readiness_snapshot_live_release_allowed_true" in result.report["blocking_reasons"]
    assert result.report["live_release_allowed"] is False


def test_invalid_json_is_reported_without_crashing(tmp_path: Path) -> None:
    path = tmp_path / "data/reports/monte_carlo_risk_simulation_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{invalid-json", encoding="utf-8")

    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "evidence_missing"
    assert result.report["invalid_evidence"]
    assert "invalid_evidence" in result.report["root_cause_categories"]


def test_no_write_does_not_create_output(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/monte_carlo_no_trade_recovery_diagnostics.json"
    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, output=output, no_write=True)

    assert result.write_performed is False
    assert not output.exists()


def test_write_enabled_creates_json(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/monte_carlo_no_trade_recovery_diagnostics.json"
    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, output=output, no_write=False)

    assert result.write_performed is True
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "monte_carlo_no_trade_recovery_diagnostics_v1"
    assert payload["live_release_allowed"] is False


def test_ok_evidence_still_keeps_live_false(tmp_path: Path) -> None:
    write_json(tmp_path, "data/reports/monte_carlo_risk_simulation_report.json", {"status": "ok", "live_release_allowed": False})
    write_json(tmp_path, "data/reports/monte_carlo_risk_budget_policy_report.json", {"status": "ok", "live_release_allowed": False})
    write_json(tmp_path, "data/reports/readiness_snapshot_v2.json", {"status": "ok", "live_release_allowed": False})

    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True)

    assert result.report["status"] in {"ok", "degraded"}
    assert result.report["no_trade_detected"] is False
    assert result.report["live_release_allowed"] is False
    assert result.report["canary_release_allowed"] is False


def test_now_argument_is_stable(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = build_monte_carlo_no_trade_recovery_diagnostics(project_root=tmp_path, no_write=True, now=now)

    assert result.report["generated_at"] == "2026-01-01T00:00:00Z"
