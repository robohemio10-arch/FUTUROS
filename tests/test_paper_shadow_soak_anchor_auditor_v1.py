from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from smartcrypto.ops.paper_shadow_soak_anchor import audit_paper_shadow_soak_anchor_continuity_pack


def write_json(root: Path, relative: str, payload: dict[str, Any]) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def write_text(root: Path, relative: str, text: str = "ok") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def add_semantic_closeout_files(root: Path) -> None:
    write_text(root, "scripts/audit_dashboard_semantic_coverage_v2.py", "# semantic audit\n")
    write_text(root, "docs/SMART_FUTUROS_DASHBOARD_SEMANTIC_COVERAGE_AUDIT_V2.md", "# semantic audit\n")


def test_without_soak_family_evidence_is_evidence_missing(tmp_path: Path) -> None:
    add_semantic_closeout_files(tmp_path)

    result = audit_paper_shadow_soak_anchor_continuity_pack(project_root=tmp_path)

    assert result.report["status"] == "evidence_missing"
    assert result.report["continuity_anchor_established"] is False
    assert result.report["live_release_allowed"] is False
    assert result.report["canary_release_allowed"] is False
    assert result.write_performed is False


def test_seven_day_diagnostic_is_not_thirty_day_readiness(tmp_path: Path) -> None:
    add_semantic_closeout_files(tmp_path)
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_report.json",
        {
            "status": "ok",
            "observed_soak_days": 7,
            "paper_only": True,
            "shadow_only": True,
            "sends_orders": False,
            "changes_risk": False,
        },
    )

    result = audit_paper_shadow_soak_anchor_continuity_pack(project_root=tmp_path)

    assert result.report["diagnostic_soak_reached"] is True
    assert result.report["readiness_soak_reached"] is False
    assert result.report["seven_day_diagnostic_status"] == "reached"
    assert result.report["thirty_day_readiness_status"] == "blocked"
    assert result.report["status"] == "blocked"
    assert result.report["live_release_allowed"] is False


def test_thirty_day_continuity_without_critical_gap_is_ok_or_degraded(tmp_path: Path) -> None:
    add_semantic_closeout_files(tmp_path)
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_report.json",
        {
            "status": "ok",
            "observed_soak_days": 31,
            "critical_gap_count": 0,
            "warning_gap_count": 0,
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "sends_orders": False,
            "changes_risk": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
        },
    )
    write_json(tmp_path, "data/reports/paper_shadow_soak_continuity_audit.json", {"status": "ok", "observed_calendar_days": 31})
    write_json(tmp_path, "data/reports/runtime_evidence_pack_v2.json", {"status": "ok", "observed_soak_days": 31})
    write_json(tmp_path, "data/reports/readiness_snapshot_v2.json", {"status": "blocked", "observed_soak_days": 31, "live_release_allowed": False})
    write_json(tmp_path, "data/reports/freqtrade_paper_db_authority_report.json", {"status": "ok"})
    write_json(tmp_path, "data/reports/monte_carlo_risk_simulation_report.json", {"status": "ok"})

    result = audit_paper_shadow_soak_anchor_continuity_pack(project_root=tmp_path)

    assert result.report["readiness_soak_reached"] is True
    assert result.report["critical_gap_count"] == 0
    assert result.report["status"] in {"ok", "degraded"}
    assert result.report["live_release_allowed"] is False
    assert result.report["canary_release_allowed"] is False
    assert result.report["manual_go_no_go_required"] is True


def test_critical_gap_blocks_readiness(tmp_path: Path) -> None:
    add_semantic_closeout_files(tmp_path)
    write_json(tmp_path, "data/reports/paper_shadow_soak_report.json", {"status": "ok", "observed_soak_days": 31, "critical_gap_count": 1})

    result = audit_paper_shadow_soak_anchor_continuity_pack(project_root=tmp_path)

    assert result.report["status"] == "blocked"
    assert "critical_gap_count_gt_zero: 1" in result.report["blocking_reasons"]
    assert result.report["thirty_day_readiness_status"] == "blocked"


def test_unsafe_safety_flags_block(tmp_path: Path) -> None:
    add_semantic_closeout_files(tmp_path)
    write_json(
        tmp_path,
        "data/reports/runtime_evidence_pack_v2.json",
        {"status": "ok", "observed_soak_days": 31, "safety": {"sends_orders": True, "changes_risk": True}},
    )

    result = audit_paper_shadow_soak_anchor_continuity_pack(project_root=tmp_path)

    assert result.report["status"] == "blocked"
    assert any("sends_orders=true" in reason for reason in result.report["blocking_reasons"])
    assert any("changes_risk=true" in reason for reason in result.report["blocking_reasons"])


def test_invalid_json_is_reported(tmp_path: Path) -> None:
    add_semantic_closeout_files(tmp_path)
    path = tmp_path / "data/reports/paper_shadow_soak_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{invalid", encoding="utf-8")

    result = audit_paper_shadow_soak_anchor_continuity_pack(project_root=tmp_path)

    assert result.report["status"] == "evidence_missing"
    assert result.report["invalid_evidence"]


def test_default_does_not_write_output(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/paper_shadow_soak_anchor_continuity_pack.json"

    result = audit_paper_shadow_soak_anchor_continuity_pack(project_root=tmp_path, output=output)

    assert result.write_performed is False
    assert not output.exists()
    assert result.report["write_performed"] is False


def test_write_flag_materializes_report(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/paper_shadow_soak_anchor_continuity_pack.json"

    result = audit_paper_shadow_soak_anchor_continuity_pack(project_root=tmp_path, output=output, write=True)

    assert result.write_performed is True
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "paper_shadow_soak_anchor_continuity_pack_v1"
    assert payload["live_release_allowed"] is False
    assert payload["write_performed"] is True


def test_output_must_remain_under_project_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.json"

    try:
        audit_paper_shadow_soak_anchor_continuity_pack(project_root=tmp_path, output=outside)
    except ValueError as exc:
        assert "under project root" in str(exc)
    else:
        raise AssertionError("Expected ValueError for output outside project root")
