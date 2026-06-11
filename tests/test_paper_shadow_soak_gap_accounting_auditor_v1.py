from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.paper_shadow_soak_gap_accounting import audit_paper_shadow_soak_continuity_and_gap_accounting


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


def add_semantic_doc(root: Path) -> None:
    write_text(root, "docs/SMART_FUTUROS_DASHBOARD_SEMANTIC_COVERAGE_AUDIT_V2.md", "# ok\n")


def test_without_anchor_family_evidence_is_evidence_missing(tmp_path: Path) -> None:
    add_semantic_doc(tmp_path)

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path)

    assert result.report["status"] == "evidence_missing"
    assert result.report["continuity_accounting_established"] is False
    assert result.report["live_release_allowed"] is False
    assert result.report["canary_release_allowed"] is False
    assert result.write_performed is False


def test_seven_day_diagnostic_does_not_satisfy_thirty_day_readiness(tmp_path: Path) -> None:
    add_semantic_doc(tmp_path)
    write_json(tmp_path, "data/reports/paper_shadow_soak_report.json", {"status": "ok", "observed_soak_days": 7})

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path)

    assert result.report["diagnostic_soak_reached"] is True
    assert result.report["readiness_soak_reached"] is False
    assert result.report["seven_day_diagnostic_status"] == "not_reached" or result.report["seven_day_diagnostic_status"] == "reached"
    assert result.report["thirty_day_readiness_status"] == "blocked"
    assert result.report["status"] == "blocked"


def test_thirty_one_day_gap_free_interval_is_ok_or_degraded_but_never_releases(tmp_path: Path) -> None:
    add_semantic_doc(tmp_path)
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_report.json",
        {
            "status": "ok",
            "covered_intervals": [{"start": "2026-01-01T00:00:00Z", "end": "2026-02-01T00:00:00Z"}],
            "paper_only": True,
            "shadow_only": True,
            "sends_orders": False,
            "changes_risk": False,
        },
    )
    write_json(tmp_path, "data/reports/paper_shadow_soak_continuity_audit.json", {"status": "ok", "observed_calendar_days": 31})
    write_json(tmp_path, "data/reports/paper_shadow_soak_anchor_continuity_pack.json", {"status": "ok", "observed_soak_days": 31})
    write_json(tmp_path, "data/reports/runtime_evidence_pack_v2.json", {"status": "ok", "observed_soak_days": 31})
    write_json(tmp_path, "data/reports/readiness_snapshot_v2.json", {"status": "ok", "observed_soak_days": 31})
    write_json(tmp_path, "data/reports/freqtrade_paper_db_authority_report.json", {"status": "ok"})
    write_json(tmp_path, "data/reports/monte_carlo_risk_simulation_report.json", {"status": "ok"})

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path)

    assert result.report["readiness_soak_reached"] is True
    assert result.report["critical_gap_count"] == 0
    assert result.report["status"] in {"ok", "degraded"}
    assert result.report["live_release_allowed"] is False
    assert result.report["canary_release_allowed"] is False
    assert result.report["manual_go_no_go_required"] is True


def test_timestamp_gap_generates_critical_gap_window(tmp_path: Path) -> None:
    add_semantic_doc(tmp_path)
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_report.json",
        {
            "status": "ok",
            "observed_soak_days": 31,
            "events": [
                {"generated_at": "2026-01-01T00:00:00Z"},
                {"generated_at": "2026-01-01T07:01:00Z"},
            ],
        },
    )

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path)

    assert result.report["status"] == "blocked"
    assert result.report["critical_gap_count"] == 1
    assert result.report["gap_windows"][0]["severity"] == "critical"
    assert result.report["live_release_allowed"] is False


def test_timestamp_gap_generates_warning_gap_window(tmp_path: Path) -> None:
    add_semantic_doc(tmp_path)
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
        },
    )

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path)

    assert result.report["warning_gap_count"] == 1
    assert result.report["critical_gap_count"] == 0
    assert result.report["status"] in {"blocked", "degraded"}


def test_explicit_missing_intervals_are_normalized(tmp_path: Path) -> None:
    add_semantic_doc(tmp_path)
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_continuity_audit.json",
        {
            "status": "blocked",
            "observed_calendar_days": 31,
            "missing_intervals": [
                {
                    "start": "2026-01-02T00:00:00Z",
                    "end": "2026-01-02T08:00:00Z",
                    "severity": "critical",
                }
            ],
        },
    )

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path)

    assert result.report["critical_gap_count"] >= 1
    assert any(window["source"] == "paper_shadow_soak_continuity_audit" for window in result.report["gap_windows"])


def test_daily_and_hourly_coverage_are_materialized(tmp_path: Path) -> None:
    add_semantic_doc(tmp_path)
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_report.json",
        {
            "status": "ok",
            "covered_intervals": [{"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T03:00:00Z"}],
            "events": [{"generated_at": "2026-01-01T01:30:00Z"}],
        },
    )

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path)

    assert result.report["daily_coverage"]
    assert result.report["hourly_coverage"]
    assert result.report["effective_soak_start_utc"] == "2026-01-01T00:00:00Z"


def test_unsafe_flags_block_readiness(tmp_path: Path) -> None:
    add_semantic_doc(tmp_path)
    write_json(
        tmp_path,
        "data/reports/runtime_evidence_pack_v2.json",
        {"status": "ok", "observed_soak_days": 31, "safety": {"sends_orders": True, "changes_risk": True}},
    )

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path)

    assert result.report["status"] == "blocked"
    assert any("sends_orders=true" in reason for reason in result.report["blocking_reasons"])
    assert any("changes_risk=true" in reason for reason in result.report["blocking_reasons"])


def test_invalid_json_is_reported(tmp_path: Path) -> None:
    add_semantic_doc(tmp_path)
    path = tmp_path / "data/reports/paper_shadow_soak_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{invalid", encoding="utf-8")

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path)

    assert result.report["status"] == "evidence_missing"
    assert result.report["invalid_evidence"]


def test_write_flag_materializes_report(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/paper_shadow_soak_gap_accounting_report.json"

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path, output=output, write=True)

    assert result.write_performed is True
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "paper_shadow_soak_continuity_gap_accounting_v1"
    assert payload["write_performed"] is True
    assert payload["live_release_allowed"] is False


def test_default_does_not_write_output(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/paper_shadow_soak_gap_accounting_report.json"

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path, output=output)

    assert result.write_performed is False
    assert not output.exists()


def test_output_must_remain_under_project_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.json"

    try:
        audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path, output=outside)
    except ValueError as exc:
        assert "under project root" in str(exc)
    else:
        raise AssertionError("Expected ValueError for output outside project root")


def test_now_argument_is_stable(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    result = audit_paper_shadow_soak_continuity_and_gap_accounting(project_root=tmp_path, now=now)

    assert result.report["generated_at_utc"] == "2026-01-01T00:00:00Z"
