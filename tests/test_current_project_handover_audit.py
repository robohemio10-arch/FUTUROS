from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_current_project_handover import (
    REQUIRED_VERSIONED_FILES,
    build_current_project_handover_audit,
)


def seed_required_files(root: Path) -> None:
    for path in REQUIRED_VERSIONED_FILES:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if path == Path("docs/CURRENT_PROJECT_HANDOVER_AFTER_NTFY_TELEGRAM.md"):
            target.write_text(
                "\n".join(
                    [
                        "e18c6a1cbdcba9e864ed53cc0f55ee1f5f923e3b",
                        "PR #125",
                        "ntfy-telegram-critical-notifications",
                        "codex/zip-standalone-dynamic-import-audit-fix",
                        "codex/critical-notifications-dashboard-panel",
                        "paper_only=true",
                        "shadow_only=true",
                        "sends_orders=false",
                        "changes_risk=false",
                        "exchange_private_access=false",
                    ]
                ),
                encoding="utf-8",
            )
        elif path == Path("docs/CANONICAL_SOURCE_OF_TRUTH_INDEX.md"):
            target.write_text(
                "\n".join(
                    [
                        "repositório Git",
                        "docs canônicos versionados",
                        "PROJECT_MANIFEST_CLEAN.json",
                        "data/reports",
                        "handover técnico atualizado",
                    ]
                ),
                encoding="utf-8",
            )
        else:
            target.write_text("placeholder\n", encoding="utf-8")


def test_handover_audit_blocks_when_required_files_missing(tmp_path: Path) -> None:
    result = build_current_project_handover_audit(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert result.report["paper_only"] is True
    assert result.report["sends_orders"] is False
    assert any(reason.startswith("missing_file:") for reason in result.report["blocking_reasons"])


def test_handover_audit_ok_with_required_files(tmp_path: Path) -> None:
    seed_required_files(tmp_path)

    result = build_current_project_handover_audit(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "ok"
    assert result.report["blocking_reasons"] == []
    assert result.report["shadow_only"] is True
    assert result.report["changes_risk"] is False
    assert result.report["runtime_logic_changed"] is False
    assert result.report["dashboard_changed"] is False


def test_handover_audit_blocks_when_handover_marker_missing(tmp_path: Path) -> None:
    seed_required_files(tmp_path)
    path = tmp_path / "docs/CURRENT_PROJECT_HANDOVER_AFTER_NTFY_TELEGRAM.md"
    text = path.read_text(encoding="utf-8").replace("PR #125", "")
    path.write_text(text, encoding="utf-8")

    result = build_current_project_handover_audit(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert "handover_missing_marker:PR #125" in result.report["blocking_reasons"]


def test_handover_audit_writes_report(tmp_path: Path) -> None:
    seed_required_files(tmp_path)

    result = build_current_project_handover_audit(project_root=tmp_path, no_write=False)

    assert result.write_performed is True
    assert result.output_path.exists()
    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "current_project_handover_after_ntfy_telegram_v1"
    assert payload["status"] == "ok"
    assert payload["source_of_truth_order"][0] == "git_repository_dev"
