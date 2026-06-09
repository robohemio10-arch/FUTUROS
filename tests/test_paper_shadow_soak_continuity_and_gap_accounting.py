from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ops.paper_shadow_soak_continuity import audit_paper_shadow_soak_continuity


def write_json(root: Path, relative: str, payload: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_without_evidence_returns_evidence_missing(tmp_path: Path) -> None:
    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "evidence_missing"
    assert result.report["live_release_allowed"] is False
    assert result.write_performed is False
    assert result.report["missing_evidence"]


def test_seven_days_without_required_soak_blocks_readiness(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_report.json",
        {
            "status": "ok",
            "observed_soak_days": 7,
            "safety_flags": {"paper_only": True, "shadow_only": True, "sends_orders": False, "changes_risk": False},
        },
    )

    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, no_write=True)

    assert result.report["diagnostic_soak_reached"] is True
    assert result.report["readiness_soak_reached"] is False
    assert result.report["status"] == "blocked"
    assert result.report["live_release_allowed"] is False


def test_thirty_days_with_critical_gap_is_blocked(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_report.json",
        {
            "status": "ok",
            "covered_intervals": [
                {"start": "2026-01-01T00:00:00Z", "end": "2026-01-31T00:00:00Z"}
            ],
            "events": [
                {"generated_at": "2026-01-01T00:00:00Z"},
                {"generated_at": "2026-01-01T07:01:00Z"},
            ],
            "safety_flags": {"sends_orders": False, "changes_risk": False},
        },
    )

    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, no_write=True)

    assert result.report["readiness_soak_reached"] is True
    assert result.report["critical_gap_count"] == 1
    assert result.report["status"] == "blocked"
    assert result.report["live_release_allowed"] is False


def test_thirty_days_without_critical_gap_can_be_ok_but_never_releases_live(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_report.json",
        {
            "status": "ok",
            "covered_intervals": [
                {"start": "2026-01-01T00:00:00Z", "end": "2026-01-31T00:00:00Z"}
            ],
            "safety_flags": {
                "paper_only": True,
                "shadow_only": True,
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
                "sends_orders": False,
                "changes_risk": False,
                "changes_training_dataset": False,
                "writes_trades_master": False,
                "live_release_allowed": False,
            },
        },
    )

    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, no_write=True)

    assert result.report["readiness_soak_reached"] is True
    assert result.report["critical_gap_count"] == 0
    assert result.report["status"] in {"ok", "degraded"}
    assert result.report["continuity_approved"] is (result.report["status"] == "ok")
    assert result.report["live_release_allowed"] is False


def test_sends_orders_true_blocks(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/runtime_evidence_pack_v2.json",
        {
            "status": "ok",
            "observed_soak_days": 31,
            "safety_flags": {"sends_orders": True},
        },
    )

    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert any("sends_orders=true" in reason for reason in result.report["blocking_reasons"])


def test_changes_risk_true_blocks(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/readiness_snapshot_v2.json",
        {
            "status": "ok",
            "observed_soak_days": 31,
            "safety_flags": {"changes_risk": True},
        },
    )

    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert any("changes_risk=true" in reason for reason in result.report["blocking_reasons"])


def test_invalid_json_is_reported_without_crashing(tmp_path: Path) -> None:
    path = tmp_path / "data/reports/paper_shadow_soak_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{invalid-json", encoding="utf-8")

    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "evidence_missing"
    assert result.report["invalid_evidence"]


def test_no_write_does_not_create_output(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/paper_shadow_soak_continuity_audit.json"
    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, output=output, no_write=True)

    assert result.write_performed is False
    assert not output.exists()


def test_writes_json_when_enabled(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/paper_shadow_soak_continuity_audit.json"
    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, output=output, no_write=False)

    assert result.write_performed is True
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "paper_shadow_soak_continuity_v1"
    assert payload["live_release_allowed"] is False


def test_runtime_evidence_and_readiness_snapshot_are_consumed(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/runtime_evidence_pack_v2.json",
        {
            "status": "ok",
            "readiness_snapshot": {"status": "ok", "observed_soak_days": 31, "live_release_allowed": False},
            "safety_flags": {"sends_orders": False, "changes_risk": False},
        },
    )
    write_json(
        tmp_path,
        "data/reports/readiness_snapshot_v2.json",
        {"status": "ok", "observed_soak_days": 31, "live_release_allowed": False},
    )

    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, no_write=True)

    assert result.report["readiness_soak_reached"] is True
    assert result.report["status"] in {"ok", "degraded"}
    assert any(source["name"] == "runtime_evidence_pack_v2" for source in result.report["evidence_sources"])
    assert any(source["name"] == "readiness_snapshot_v2" for source in result.report["evidence_sources"])


def test_dashboard_can_read_report_without_side_effect(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/paper_shadow_soak_continuity_audit.json"
    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, output=output, no_write=False)

    before = output.stat().st_mtime_ns
    payload = json.loads(output.read_text(encoding="utf-8"))
    after = output.stat().st_mtime_ns

    assert before == after
    assert payload["status"] == result.report["status"]
    assert payload["safety_flags"]["sends_orders"] is False
    assert payload["safety_flags"]["changes_risk"] is False
    assert payload["safety_flags"]["exchange_private_access"] is False


def test_timestamp_gap_warning_not_critical(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_report.json",
        {
            "status": "ok",
            "observed_soak_days": 31,
            "events": [
                {"generated_at": "2026-01-01T00:00:00Z"},
                {"generated_at": "2026-01-01T02:00:00Z"},
            ],
            "safety_flags": {"sends_orders": False, "changes_risk": False},
        },
    )

    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, no_write=True)

    assert result.report["critical_gap_count"] == 0
    assert result.report["warning_gap_count"] == 1
    assert result.report["status"] == "degraded"
    assert result.report["live_release_allowed"] is False


def test_now_argument_is_stable(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = audit_paper_shadow_soak_continuity(project_root=tmp_path, no_write=True, now=now)

    assert result.report["generated_at"] == "2026-01-01T00:00:00Z"
