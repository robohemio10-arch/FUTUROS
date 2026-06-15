from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.dashboard.components.runtime_evidence_panel import (
    SUMMARY_COLUMNS,
    runtime_evidence_source_rows,
    runtime_evidence_summary_row,
    runtime_evidence_view,
)
from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.builder_registry import build_all_dashboard_snapshots
from smartcrypto.ops.dashboard_snapshots.runtime_evidence_integration import (
    build_runtime_evidence_view,
)


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def write_json(root: Path, relative: str, payload: object) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def build_empty_source_closeout(status: str = "OK") -> dict[str, object]:
    return {
        "dashboard_status": status,
        "global_source_health_status": status,
        "source_health_matrix": [],
        "source_matrix": [],
        "page_source_matrix": [],
        "global_blocking_reasons": [],
    }


def test_runtime_evidence_pack_missing_never_returns_ok(tmp_path: Path) -> None:
    view = build_runtime_evidence_view(
        project_root=tmp_path,
        now_utc=NOW,
        source_closeout=build_empty_source_closeout(),
    )

    assert view["runtime_evidence_status"] == "BLOCKED"
    assert "runtime_evidence_pack_v2:MISSING_REQUIRED" in view["blocking_evidence_sources"]
    assert view["canary_release_allowed"] is False
    assert view["live_release_allowed"] is False


def test_readiness_snapshot_missing_never_returns_ok(tmp_path: Path) -> None:
    write_json(tmp_path, "data/reports/runtime_evidence_pack_v2.json", {"status": "ok"})
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_gap_accounting_report.json",
        {"status": "ok", "critical_gap_count": 0, "thirty_day_readiness_status": "ok"},
    )

    view = build_runtime_evidence_view(
        project_root=tmp_path,
        now_utc=NOW,
        source_closeout=build_empty_source_closeout(),
    )

    assert view["readiness_status"] == "MISSING"
    assert view["runtime_evidence_status"] == "BLOCKED"
    assert "readiness_snapshot_v2:MISSING_REQUIRED" in view["blocking_evidence_sources"]


def test_soak_gap_insufficient_blocks_readiness(tmp_path: Path) -> None:
    write_json(tmp_path, "data/reports/runtime_evidence_pack_v2.json", {"status": "ok"})
    write_json(tmp_path, "data/reports/readiness_snapshot_v2.json", {"status": "ok"})
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_gap_accounting_report.json",
        {
            "status": "blocked",
            "critical_gap_count": 2,
            "warning_gap_count": 1,
            "thirty_day_readiness_status": "blocked",
        },
    )

    view = build_runtime_evidence_view(
        project_root=tmp_path,
        now_utc=NOW,
        source_closeout=build_empty_source_closeout(),
    )

    assert view["gap_accounting_status"] == "BLOCKED"
    assert view["critical_gap_count"] == 2
    assert "paper_shadow_soak_gap_accounting:BLOCKED" in view["blocking_evidence_sources"]
    assert view["runtime_evidence_status"] == "BLOCKED"


def test_source_health_global_block_prevents_ok_even_if_evidence_payload_allows_release(
    tmp_path: Path,
) -> None:
    write_json(
        tmp_path,
        "data/reports/runtime_evidence_pack_v2.json",
        {"status": "ok", "canary_release_allowed": True, "live_release_allowed": True},
    )
    write_json(
        tmp_path,
        "data/reports/readiness_snapshot_v2.json",
        {"status": "ok", "canary_release_allowed": True, "live_release_allowed": True},
    )
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_gap_accounting_report.json",
        {"status": "ok", "critical_gap_count": 0, "thirty_day_readiness_status": "ok"},
    )

    view = build_runtime_evidence_view(
        project_root=tmp_path,
        now_utc=NOW,
        source_closeout=build_empty_source_closeout("BLOCKED"),
    )

    assert view["runtime_evidence_status"] == "BLOCKED"
    assert view["canary_release_allowed_raw"] is True
    assert view["live_release_allowed_raw"] is True
    assert view["canary_release_allowed"] is False
    assert view["live_release_allowed"] is False
    assert "canary_release_allowed_conflicts_with_blocked_source_health" in view["blocking_evidence_sources"]


def test_json_invalid_required_evidence_blocks(tmp_path: Path) -> None:
    target = tmp_path / "data/reports/runtime_evidence_pack_v2.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{invalid", encoding="utf-8")
    write_json(tmp_path, "data/reports/readiness_snapshot_v2.json", {"status": "ok"})
    write_json(
        tmp_path,
        "data/reports/paper_shadow_soak_gap_accounting_report.json",
        {"status": "ok", "critical_gap_count": 0, "thirty_day_readiness_status": "ok"},
    )

    view = build_runtime_evidence_view(
        project_root=tmp_path,
        now_utc=NOW,
        source_closeout=build_empty_source_closeout(),
    )

    assert view["runtime_evidence_pack_status"] == "BLOCKED"
    assert view["runtime_evidence_status"] == "BLOCKED"
    assert any("runtime_evidence_pack_v2" in item for item in view["blocking_evidence_sources"])


def test_build_summary_global_and_page_snapshots_include_runtime_evidence_view(
    tmp_path: Path,
) -> None:
    context = create_dashboard_build_context(
        tmp_path,
        output_dir=tmp_path / "output",
        now_utc=NOW,
        runtime_mode="paper",
        strict=False,
        allow_writes_to_output_dir=False,
    )
    result = build_all_dashboard_snapshots(context)
    summary = result["summary"]
    global_snapshot = result["snapshots"]["dashboard_global_status_snapshot.json"]
    infrastructure = result["snapshots"]["dashboard_infrastructure_snapshot.json"]
    active_controls = result["snapshots"]["dashboard_active_controls_snapshot.json"]

    assert summary["runtime_evidence_integration_status"] == "BLOCKED"
    assert "runtime_evidence_view" in summary
    assert global_snapshot["runtime_evidence_view"] == summary["runtime_evidence_view"]
    assert "runtime_evidence_integration" in infrastructure["sections"]
    assert "runtime_evidence_integration" in active_controls["sections"]


def test_runtime_evidence_component_extracts_summary_and_sources() -> None:
    snapshot = {
        "runtime_evidence_view": {
            "runtime_evidence_status": "BLOCKED",
            "runtime_evidence_pack_status": "MISSING",
            "readiness_status": "BLOCKED",
            "paper_runtime_health_status": "DEGRADED",
            "container_snapshot_status": "DISABLED",
            "gap_accounting_status": "BLOCKED",
            "continuous_valid_soak_days": 0.25,
            "critical_gap_count": 3,
            "canary_release_allowed": False,
            "live_release_allowed": False,
            "evidence_sources": [
                {
                    "source_id": "runtime_evidence_pack_v2",
                    "status": "MISSING_REQUIRED",
                    "health_status": "BLOCKED",
                    "freshness_status": "UNKNOWN",
                    "required": True,
                    "missing": True,
                    "stale": False,
                    "blocking": True,
                    "degraded": False,
                    "path": "data/reports/runtime_evidence_pack_v2.json",
                    "remediation_action": "Refresh evidence.",
                }
            ],
        }
    }

    view = runtime_evidence_view(snapshot)
    summary = runtime_evidence_summary_row(view)
    sources = runtime_evidence_source_rows(view)

    assert summary["runtime_evidence_status"] == "BLOCKED"
    assert sources[0]["blocking"] is True
    assert "runtime_evidence_status" in SUMMARY_COLUMNS


def test_static_safety_no_runtime_evidence_forbidden_calls() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "smartcrypto/ops/dashboard_snapshots/runtime_evidence_integration.py",
        root / "smartcrypto/dashboard/components/runtime_evidence_panel.py",
        root / "smartcrypto/ops/dashboard_snapshots/builder_registry.py",
    )
    source = "\n".join(path.read_text(encoding="utf-8").lower() for path in paths)

    for prohibited in (
        "import ccxt",
        "create_order(",
        "cancel_order(",
        "fetch_balance(",
        "fetch_open_orders(",
        "ordermanager(",
        "exchangegateway(",
        "notificationdispatcher(",
        "requests.post(",
        "shell=true",
    ):
        assert prohibited not in source
