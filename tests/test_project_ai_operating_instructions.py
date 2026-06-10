from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_project_ai_operating_instructions import (
    REQUIRED_FILES,
    REQUIRED_MARKERS,
    build_project_ai_operating_instructions_audit,
)


def seed_required_files(root: Path) -> None:
    for relpath in REQUIRED_FILES:
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if relpath.suffix == ".md":
            path.write_text("
".join(REQUIRED_MARKERS), encoding="utf-8")
        else:
            path.write_text("{}
", encoding="utf-8")


def test_project_ai_operating_instructions_blocks_when_files_missing(tmp_path: Path) -> None:
    result = build_project_ai_operating_instructions_audit(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert result.report["paper_only"] is True
    assert result.report["shadow_only"] is True
    assert result.report["sends_orders"] is False
    assert any(reason.startswith("missing_file:") for reason in result.report["blocking_reasons"])


def test_project_ai_operating_instructions_ok_with_required_files_and_markers(tmp_path: Path) -> None:
    seed_required_files(tmp_path)

    result = build_project_ai_operating_instructions_audit(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "ok"
    assert result.report["blocking_reasons"] == []
    assert result.report["exchange_private_access"] is False
    assert result.report["changes_risk"] is False
    assert result.report["model_promoted"] is False


def test_project_ai_operating_instructions_blocks_when_marker_missing(tmp_path: Path) -> None:
    seed_required_files(tmp_path)
    for relpath in ("docs/PROJECT_AI_OPERATING_INSTRUCTIONS.md", "docs/PROJECT_AI_NEW_CHAT_BOOTSTRAP_PROMPT.md"):
        path = tmp_path / relpath
        text = path.read_text(encoding="utf-8").replace("RiskManager", "")
        path.write_text(text, encoding="utf-8")

    result = build_project_ai_operating_instructions_audit(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert "missing_marker:RiskManager" in result.report["blocking_reasons"]


def test_project_ai_operating_instructions_writes_report(tmp_path: Path) -> None:
    seed_required_files(tmp_path)

    result = build_project_ai_operating_instructions_audit(project_root=tmp_path, no_write=False)

    assert result.write_performed is True
    assert result.output_path.exists()

    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "project_ai_operating_instructions_v1"
    assert payload["status"] == "ok"
    assert payload["runtime_logic_changed"] is False
    assert payload["dashboard_changed"] is False
