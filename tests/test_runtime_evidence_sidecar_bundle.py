from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ops.runtime_evidence_sidecar import build_runtime_evidence_sidecar_bundle


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def seed_core_reports(root: Path) -> None:
    write_json(root / "PROJECT_MANIFEST_CLEAN.json", {"status": "ok", "paper_only": True, "sends_orders": False})
    write_json(
        root / "data/reports/runtime_evidence_pack_v2.json",
        {
            "status": "blocked",
            "reason": "expected_gate_blocked",
            "runtime_observability": {"status": "ok", "reason": "ok"},
            "container_snapshot": {"status": "ok", "reason": "ok"},
            "paper_only": True,
            "shadow_only": True,
            "sends_orders": False,
            "changes_risk": False,
            "exchange_private_access": False,
        },
    )
    write_json(
        root / "data/reports/readiness_snapshot_v2.json",
        {
            "status": "blocked",
            "reason": "soak_days_below_required",
            "paper_only": True,
            "shadow_only": True,
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "sends_orders": False,
        },
    )


def test_sidecar_bundle_writes_manifest_sha256s_and_sources(tmp_path: Path) -> None:
    seed_core_reports(tmp_path)

    result = build_runtime_evidence_sidecar_bundle(
        project_root=tmp_path,
        output_root="data/evidence_packs",
        refresh_runtime_evidence=False,
        now=datetime(2026, 6, 10, 21, 30, tzinfo=timezone.utc),
    )

    assert result.summary["status"] == "ok"
    assert result.write_performed is True
    assert result.manifest_path.exists()
    assert result.sha256s_path.exists()
    assert result.validation_summary_path.exists()
    assert (result.bundle_dir / "sources/data/reports/runtime_evidence_pack_v2.json").exists()
    assert "MANIFEST.json" in result.sha256s_path.read_text(encoding="utf-8")
    assert result.summary["readiness_snapshot_status"] == "blocked"
    assert result.summary["sends_orders"] is False
    assert result.summary["changes_risk"] is False


def test_sidecar_no_write_does_not_create_bundle_dir(tmp_path: Path) -> None:
    seed_core_reports(tmp_path)

    result = build_runtime_evidence_sidecar_bundle(
        project_root=tmp_path,
        no_write=True,
        refresh_runtime_evidence=False,
        now=datetime(2026, 6, 10, 21, 31, tzinfo=timezone.utc),
    )

    assert result.summary["write_performed"] is False
    assert not result.bundle_dir.exists()
    assert result.summary["status"] == "ok"


def test_sidecar_blocks_when_collected_report_has_unsafe_flags(tmp_path: Path) -> None:
    seed_core_reports(tmp_path)
    write_json(
        tmp_path / "data/reports/trade_event_notifications_report.json",
        {
            "status": "ok",
            "paper_only": True,
            "shadow_only": True,
            "sends_orders": True,
        },
    )

    result = build_runtime_evidence_sidecar_bundle(
        project_root=tmp_path,
        refresh_runtime_evidence=False,
        now=datetime(2026, 6, 10, 21, 32, tzinfo=timezone.utc),
    )

    assert result.summary["status"] == "blocked"
    assert result.summary["reason"] == "unsafe_source_flags_detected"
    assert result.manifest["unsafe_sources"]["runtime_trade_event_notifications_report"] == ["sends_orders"]


def test_sidecar_blocks_when_runtime_observability_is_blocked(tmp_path: Path) -> None:
    seed_core_reports(tmp_path)
    write_json(
        tmp_path / "data/reports/runtime_evidence_pack_v2.json",
        {
            "status": "blocked",
            "reason": "runtime_observability_blocked",
            "runtime_observability": {"status": "blocked", "reason": "trade_event_notifications_report"},
            "paper_only": True,
            "shadow_only": True,
            "sends_orders": False,
        },
    )

    result = build_runtime_evidence_sidecar_bundle(
        project_root=tmp_path,
        refresh_runtime_evidence=False,
        now=datetime(2026, 6, 10, 21, 33, tzinfo=timezone.utc),
    )

    assert result.summary["status"] == "blocked"
    assert result.summary["reason"] == "runtime_observability_blocked"


def test_sidecar_blocks_when_core_sources_are_missing(tmp_path: Path) -> None:
    write_json(tmp_path / "PROJECT_MANIFEST_CLEAN.json", {"status": "ok"})

    result = build_runtime_evidence_sidecar_bundle(
        project_root=tmp_path,
        no_write=True,
        refresh_runtime_evidence=False,
        now=datetime(2026, 6, 10, 21, 34, tzinfo=timezone.utc),
    )

    assert result.summary["status"] == "blocked"
    assert result.summary["reason"].startswith("missing_core_sources:")
