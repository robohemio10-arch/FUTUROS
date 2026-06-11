from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.ops.dashboard_snapshots.active_controls_snapshot_builder import build_active_controls_snapshot
from smartcrypto.ops.dashboard_snapshots.quantitative_reports_snapshot_builder import build_quantitative_reports_snapshot
from smartcrypto.ops.runtime_evidence_pack import build_runtime_evidence_pack_and_readiness_snapshot_v2
from tests.dashboard_builder_test_support import context
from tests.test_runtime_evidence_gap_accounting_integration_v1 import complete_gap_project
from tests.test_runtime_evidence_pack_and_readiness_snapshot_v2 import ROOT


GAP_REPORT = Path("data/reports/paper_shadow_soak_gap_accounting_report.json")
RUNTIME_CLI = ROOT / "scripts" / "build_runtime_evidence_pack_and_readiness_snapshot_v2.py"
GAP_CLI = ROOT / "scripts" / "audit_paper_shadow_soak_continuity_and_gap_accounting.py"


def test_runtime_evidence_write_materializes_gap_accounting_report(tmp_path: Path) -> None:
    root = complete_gap_project(tmp_path, critical_gap_count=2)
    report_path = root / GAP_REPORT

    result = build_runtime_evidence_pack_and_readiness_snapshot_v2(
        project_root=root,
        output_dir=root / "data" / "reports",
        no_write=False,
    )

    assert result.write_performed is True
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "paper_shadow_soak_continuity_gap_accounting_v1"
    assert payload["write_performed"] is True
    assert payload["critical_gap_count"] == 2
    assert payload["live_release_allowed"] is False
    assert payload["canary_release_allowed"] is False
    assert result.evidence_pack["output_paths"]["paper_shadow_soak_gap_accounting_report"] == str(report_path)
    assert result.evidence_pack["evidence_sources"]["paper_shadow_soak_gap_accounting"]["materialized"] is True
    assert result.readiness_snapshot["paper_shadow_soak_gap_accounting"]["write_performed"] is True
    assert result.readiness_snapshot["paper_shadow_soak_gap_accounting"]["report_materialized"] is True


def test_runtime_evidence_no_write_keeps_gap_accounting_report_unmaterialized(tmp_path: Path) -> None:
    root = complete_gap_project(tmp_path)
    report_path = root / GAP_REPORT

    result = build_runtime_evidence_pack_and_readiness_snapshot_v2(
        project_root=root,
        output_dir=root / "data" / "reports",
        no_write=True,
    )

    assert result.write_performed is False
    assert not report_path.exists()
    assert result.evidence_pack["evidence_sources"]["paper_shadow_soak_gap_accounting"]["materialized"] is False
    assert result.readiness_snapshot["paper_shadow_soak_gap_accounting"]["write_performed"] is False
    assert result.readiness_snapshot["paper_shadow_soak_gap_accounting"]["report_materialized"] is False


def test_runtime_cli_write_materializes_gap_accounting_report_and_summary(tmp_path: Path) -> None:
    root = complete_gap_project(tmp_path, critical_gap_count=1)
    report_path = root / GAP_REPORT

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNTIME_CLI),
            "--project-root",
            str(root),
            "--output-dir",
            str(root / "data" / "reports"),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    summary = json.loads(completed.stdout)

    assert summary["write_performed"] is True
    assert summary["paper_shadow_soak_gap_accounting_report_path"] == str(report_path)
    assert summary["paper_shadow_soak_gap_accounting_report_write_performed"] is True
    assert summary["paper_shadow_soak_gap_accounting_report_materialized"] is True
    assert summary["sends_orders"] is False
    assert summary["live_release_allowed"] is False
    assert report_path.exists()


def test_dashboard_snapshots_consume_materialized_gap_accounting_without_missing_optional(tmp_path: Path) -> None:
    root = complete_gap_project(tmp_path, critical_gap_count=3)
    build_runtime_evidence_pack_and_readiness_snapshot_v2(
        project_root=root,
        output_dir=root / "data" / "reports",
        no_write=False,
    )

    active = build_active_controls_snapshot(context(root))
    quantitative = build_quantitative_reports_snapshot(context(root))

    assert str(GAP_REPORT) not in active["missing_optional_sources"]
    assert str(GAP_REPORT) not in quantitative["missing_optional_sources"]
    assert active["sections"]["readiness_gap_accounting"]["status"] == "BLOCKED"
    assert quantitative["sections"]["soak_gap_accounting"]["status"] == "BLOCKED"
    assert active["sections"]["readiness_gap_accounting"]["live_release_allowed"] is False
    assert quantitative["sections"]["soak_gap_accounting"]["live_release_allowed"] is False


def test_gap_accounting_cli_default_remains_read_only(tmp_path: Path) -> None:
    root = complete_gap_project(tmp_path)
    report_path = root / GAP_REPORT

    completed = subprocess.run(
        [sys.executable, str(GAP_CLI), "--project-root", str(root), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["write_performed"] is False
    assert payload["sends_orders"] is False
    assert payload["live_release_allowed"] is False
    assert not report_path.exists()
