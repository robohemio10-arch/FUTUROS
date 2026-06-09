from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ops.manual_go_no_go_governance import build_manual_go_no_go_live_canary_governance


def write_json(root: Path, relative: str, payload: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def seed_ready_evidence(root: Path) -> None:
    for relative in (
        "data/reports/runtime_evidence_pack_v2.json",
        "data/reports/readiness_snapshot_v2.json",
        "data/reports/paper_shadow_soak_continuity_audit.json",
        "data/reports/monte_carlo_no_trade_recovery_diagnostics.json",
        "data/reports/ai_shadow_threshold_readiness_evidence.json",
    ):
        write_json(root, relative, {"status": "ok", "release_allowed": False})


def seed_go_decision(root: Path, *, decided_at: str = "2026-01-01T00:00:00Z") -> None:
    write_json(root, "data/governance/manual_go_no_go_live_canary_decision.json", {
        "decision": "GO",
        "decided_at": decided_at,
        "decider": "operator",
        "evidence_pack_id": "pack-001",
        "rationale": "Evidence reviewed.",
        "restrictions": [],
        "acknowledges_risk": True,
        "acknowledges_no_automatic_release": True,
    })


def test_missing_decision_blocks(tmp_path: Path) -> None:
    seed_ready_evidence(tmp_path)
    result = build_manual_go_no_go_live_canary_governance(project_root=tmp_path, no_write=True)
    assert result.report["status"] == "blocked"
    assert result.report["manual_go_no_go_required"] is True
    assert result.report["release_allowed"] is False
    assert "manual_decision_missing" in result.report["blocking_reasons"]


def test_valid_go_records_but_never_releases(tmp_path: Path) -> None:
    seed_ready_evidence(tmp_path)
    seed_go_decision(tmp_path)
    result = build_manual_go_no_go_live_canary_governance(project_root=tmp_path, no_write=True, now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
    assert result.report["status"] == "manual_go_recorded"
    assert result.report["manual_decision"] == "GO"
    assert result.report["release_allowed"] is False
    assert result.report["auto_promotion_allowed"] is False
    assert result.report["live_release_allowed"] is False
    assert result.report["canary_release_allowed"] is False


def test_no_go_blocks(tmp_path: Path) -> None:
    seed_ready_evidence(tmp_path)
    seed_go_decision(tmp_path)
    path = tmp_path / "data/governance/manual_go_no_go_live_canary_decision.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["decision"] = "NO_GO"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = build_manual_go_no_go_live_canary_governance(project_root=tmp_path, no_write=True, now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
    assert result.report["status"] == "blocked"
    assert "manual_decision_no_go" in result.report["blocking_reasons"]


def test_go_with_restrictions_requires_contract(tmp_path: Path) -> None:
    seed_ready_evidence(tmp_path)
    seed_go_decision(tmp_path)
    path = tmp_path / "data/governance/manual_go_no_go_live_canary_decision.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["decision"] = "GO_WITH_RESTRICTIONS"
    payload["restrictions"] = ["only BTC"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = build_manual_go_no_go_live_canary_governance(project_root=tmp_path, no_write=True, now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
    assert result.report["status"] == "blocked"
    assert "manual_decision_requires_restriction_contract" in result.report["blocking_reasons"]


def test_expired_decision_blocks(tmp_path: Path) -> None:
    seed_ready_evidence(tmp_path)
    seed_go_decision(tmp_path)
    result = build_manual_go_no_go_live_canary_governance(project_root=tmp_path, no_write=True, now=datetime(2026, 1, 5, tzinfo=timezone.utc), max_decision_age_hours=72)
    assert result.report["status"] == "blocked"
    assert "manual_decision_expired" in result.report["blocking_reasons"]


def test_missing_acknowledgements_block(tmp_path: Path) -> None:
    seed_ready_evidence(tmp_path)
    seed_go_decision(tmp_path)
    path = tmp_path / "data/governance/manual_go_no_go_live_canary_decision.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["acknowledges_risk"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = build_manual_go_no_go_live_canary_governance(project_root=tmp_path, no_write=True, now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
    assert result.report["status"] == "blocked"
    assert "manual_decision_must_acknowledge_risk" in result.report["blocking_reasons"]


def test_upstream_block_blocks_governance(tmp_path: Path) -> None:
    seed_ready_evidence(tmp_path)
    write_json(tmp_path, "data/reports/readiness_snapshot_v2.json", {"status": "blocked"})
    seed_go_decision(tmp_path)
    result = build_manual_go_no_go_live_canary_governance(project_root=tmp_path, no_write=True, now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
    assert result.report["status"] == "blocked"
    assert "upstream_evidence_not_ready" in result.report["blocking_reasons"]


def test_policy_violation_blocks(tmp_path: Path) -> None:
    seed_ready_evidence(tmp_path)
    write_json(tmp_path, "data/reports/runtime_evidence_pack_v2.json", {"status": "ok", "auto_promotion_allowed": True})
    seed_go_decision(tmp_path)
    result = build_manual_go_no_go_live_canary_governance(project_root=tmp_path, no_write=True, now=datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
    assert result.report["status"] == "blocked"
    assert any("auto_promotion_allowed=true" in reason for reason in result.report["blocking_reasons"])


def test_write_enabled_creates_report(tmp_path: Path) -> None:
    result = build_manual_go_no_go_live_canary_governance(project_root=tmp_path, no_write=False)
    assert result.write_performed is True
    assert result.output_path.exists()
    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "manual_go_no_go_live_canary_governance_v1"
    assert payload["release_allowed"] is False


def test_invalid_decision_json_is_reported(tmp_path: Path) -> None:
    seed_ready_evidence(tmp_path)
    path = tmp_path / "data/governance/manual_go_no_go_live_canary_decision.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{invalid-json", encoding="utf-8")
    result = build_manual_go_no_go_live_canary_governance(project_root=tmp_path, no_write=True)
    assert result.report["status"] == "blocked"
    assert any("invalid_decision_file" in reason for reason in result.report["blocking_reasons"])


def test_now_argument_is_stable(tmp_path: Path) -> None:
    result = build_manual_go_no_go_live_canary_governance(project_root=tmp_path, no_write=True, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert result.report["generated_at"] == "2026-01-01T00:00:00Z"
