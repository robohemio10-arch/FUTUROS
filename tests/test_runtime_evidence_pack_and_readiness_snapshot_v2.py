from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.runtime_evidence_pack import (
    build_runtime_evidence_pack_and_readiness_snapshot_v2,
)


ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def generate_manifest(project_root: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_project_manifest.py"),
            "--project-root",
            str(project_root),
            "--output",
            "PROJECT_MANIFEST_CLEAN.json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def base_report(status: str = "ok", **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
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
    }
    payload.update(extra)
    return payload


def complete_project(tmp_path: Path, *, observed_soak_days: float = 31.0) -> Path:
    root = tmp_path / "project"
    reports = root / "data" / "reports"
    write_json(
        reports / "paper_soak_report.json",
        base_report(observed_soak_days=observed_soak_days, required_soak_days=30),
    )
    write_json(
        reports / "freqtrade_paper_db_authority_report.json",
        base_report(selected_db="data/snapshots/freqtrade_paper.sqlite", reason="authorized_snapshot"),
    )
    write_json(reports / "readiness_gate_report.json", base_report(readiness_approved=True))
    write_json(reports / "monte_carlo_risk_simulation_report.json", base_report(policy_action="allow_paper"))
    generate_manifest(root)
    return root


def build(root: Path) -> dict[str, Any]:
    return build_runtime_evidence_pack_and_readiness_snapshot_v2(
        project_root=root,
        output_dir=root / "out",
        no_write=True,
        now=FIXED_NOW,
    ).readiness_snapshot


def test_snapshot_blocks_when_paper_soak_report_missing(tmp_path: Path) -> None:
    root = complete_project(tmp_path)
    (root / "data" / "reports" / "paper_soak_report.json").unlink()

    snapshot = build(root)

    assert snapshot["status"] == "blocked"
    assert "paper_soak_report" in snapshot["missing_evidence"]
    assert "missing_required_evidence:paper_soak_report" in snapshot["blocking_reasons"]


def test_snapshot_blocks_when_freqtrade_paper_db_authority_missing(tmp_path: Path) -> None:
    root = complete_project(tmp_path)
    (root / "data" / "reports" / "freqtrade_paper_db_authority_report.json").unlink()

    snapshot = build(root)

    assert snapshot["status"] == "blocked"
    assert "freqtrade_paper_db_authority_report" in snapshot["missing_evidence"]
    assert "missing_required_evidence:freqtrade_paper_db_authority_report" in snapshot["blocking_reasons"]


def test_snapshot_blocks_when_observed_soak_days_below_30(tmp_path: Path) -> None:
    root = complete_project(tmp_path, observed_soak_days=7.5)

    snapshot = build(root)

    assert snapshot["status"] == "blocked"
    assert snapshot["diagnostic_soak_reached"] is True
    assert snapshot["readiness_soak_reached"] is False
    assert "soak_days_below_required" in snapshot["blocking_reasons"]


def test_snapshot_blocks_when_monte_carlo_missing(tmp_path: Path) -> None:
    root = complete_project(tmp_path)
    (root / "data" / "reports" / "monte_carlo_risk_simulation_report.json").unlink()

    snapshot = build(root)

    assert snapshot["status"] == "blocked"
    assert "monte_carlo_report" in snapshot["missing_evidence"]
    assert "missing_required_evidence:monte_carlo_report" in snapshot["blocking_reasons"]


def test_snapshot_blocks_when_manifest_check_fails(tmp_path: Path) -> None:
    root = complete_project(tmp_path)
    (root / "PROJECT_MANIFEST_CLEAN.json").write_text("{}", encoding="utf-8")

    snapshot = build(root)

    assert snapshot["status"] == "blocked"
    assert "manifest_check_manifest_outdated" in snapshot["blocking_reasons"]


def test_snapshot_blocks_when_secret_scan_fails(tmp_path: Path) -> None:
    root = complete_project(tmp_path)
    write_json(
        root / "PROJECT_MANIFEST_CLEAN.json",
        {"files": [{"path": "scripts/leaky.py"}], "runtime_exclusions": {"excluded_tracked_paths": []}},
    )
    leaky = root / "scripts" / "leaky.py"
    leaky.parent.mkdir(parents=True, exist_ok=True)
    fake_secret = "a" * 36
    leaky.write_text(f"api_key = '{fake_secret}'\n", encoding="utf-8")

    snapshot = build(root)

    assert snapshot["status"] == "blocked"
    assert "secret_scan_secret_findings_detected" in snapshot["blocking_reasons"]


def test_missing_evidence_and_blocking_reasons_are_deterministic(tmp_path: Path) -> None:
    root = complete_project(tmp_path)
    (root / "data" / "reports" / "paper_soak_report.json").unlink()
    (root / "data" / "reports" / "monte_carlo_risk_simulation_report.json").unlink()

    snapshot = build(root)

    assert snapshot["missing_evidence"] == sorted(snapshot["missing_evidence"])
    assert snapshot["blocking_reasons"] == sorted(snapshot["blocking_reasons"])


def test_safety_flags_remain_false(tmp_path: Path) -> None:
    root = complete_project(tmp_path)

    snapshot = build(root)
    flags = snapshot["safety_flags"]

    assert snapshot["paper_only"] is True
    assert snapshot["shadow_only"] is True
    for flag in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "changes_training_dataset",
        "writes_trades_master",
        "live_release_allowed",
    ):
        assert snapshot[flag] is False
        assert flags[flag] is False


def test_cli_no_write_does_not_create_runtime_artifacts(tmp_path: Path) -> None:
    root = complete_project(tmp_path)
    output_dir = root / "data" / "reports"
    evidence = output_dir / "runtime_evidence_pack_v2.json"
    snapshot = output_dir / "readiness_snapshot_v2.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_runtime_evidence_pack_and_readiness_snapshot_v2.py"),
            "--project-root",
            str(root),
            "--output-dir",
            str(output_dir),
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["write_performed"] is False
    assert payload["exchange_private_access"] is False
    assert payload["sends_orders"] is False
    assert not evidence.exists()
    assert not snapshot.exists()


def test_json_output_is_deterministic_with_controlled_fixture(tmp_path: Path) -> None:
    root = complete_project(tmp_path)

    first = build_runtime_evidence_pack_and_readiness_snapshot_v2(
        project_root=root,
        output_dir=root / "out",
        no_write=True,
        now=FIXED_NOW,
    )
    second = build_runtime_evidence_pack_and_readiness_snapshot_v2(
        project_root=root,
        output_dir=root / "out",
        no_write=True,
        now=FIXED_NOW,
    )

    assert first.evidence_pack == second.evidence_pack
    assert first.readiness_snapshot == second.readiness_snapshot


def test_runtime_artifacts_are_not_versioned_by_policy() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    for token in ("data/", "reports/", "logs/", "evidence/", "*.sqlite", "*.parquet", "*.csv", "*.xlsx", "*.jsonl", "*.zip"):
        assert token in gitignore
