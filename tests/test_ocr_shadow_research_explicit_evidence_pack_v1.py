from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from smartcrypto.research.ocr_shadow_research_explicit_evidence_pack import (
    ALLOWED_STAGE_IDS,
    SCHEMA_VERSION,
    build_ocr_shadow_research_explicit_evidence_pack_report,
    run_stage,
    validate_stage_selection,
)
from smartcrypto.research.ocr_shadow_research_explicit_evidence_pack.evidence_pack import (
    STAGE_BY_ID,
)


def _stage_result(stage_id: str, *, status: str = "blocked", returncode: int = 0) -> dict[str, object]:
    return {
        "stage_id": stage_id,
        "label": stage_id,
        "script_path": f"scripts/{stage_id}.py",
        "status": status,
        "reason": "fixture_result",
        "returncode": returncode,
        "command": [sys.executable, f"scripts/{stage_id}.py"],
        "shell": False,
        "timeout_seconds": 300,
        "stdout_sample": "",
        "stderr_sample": "",
        "output_path": f"data/reports/{stage_id}.json",
        "output_exists": True,
        "sha256": "0" * 64,
        "decision": "MANTER_EM_RESEARCH",
        "write_performed": True,
        "paper_observation_allowed": False,
        "ready_for_shadow_observation": False,
        "operational_authority": False,
        "sends_orders": False,
        "changes_risk": False,
        "writes_runtime": False,
    }


def _complete_stage_results() -> list[dict[str, object]]:
    return [_stage_result(stage_id, status="blocked", returncode=0) for stage_id in ALLOWED_STAGE_IDS]


def test_no_runtime_read_by_default_blocks_safely(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_explicit_evidence_pack_report(project_root=tmp_path)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["reason"] == "explicit_evidence_pack_requires_allow_runtime_read"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["evidence_pack_decision"] == "MANTER_EM_RESEARCH"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["execute_builders"] is False
    assert report["write_performed"] is False
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_shadow_observation"] is False


def test_no_execute_builders_by_default(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_explicit_evidence_pack_report(
        project_root=tmp_path,
        allow_runtime_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "explicit_evidence_pack_requires_execute_builders"
    assert report["execute_builders"] is False
    assert report["executed_stage_count"] == 0


def test_allowlist_blocks_unknown_stage(tmp_path: Path) -> None:
    selected, unknown = validate_stage_selection(["closeout", "unknown_stage"])
    report = build_ocr_shadow_research_explicit_evidence_pack_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        execute_builders=True,
        selected_stage_ids=["closeout", "unknown_stage"],
    )

    assert selected == []
    assert unknown == ["unknown_stage"]
    assert report["status"] == "blocked"
    assert report["reason"] == "unknown_stage_not_in_allowlist"
    assert report["unknown_stage_ids"] == ["unknown_stage"]
    assert report["executed_stage_count"] == 0


def test_stage_runner_uses_shell_false(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        payload = {"status": "blocked", "reason": "fixture", "decision": "MANTER_EM_RESEARCH"}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    result = run_stage(
        STAGE_BY_ID["closeout"],
        project_root=tmp_path,
        write_requested=False,
        timeout_seconds=7,
        runner=fake_runner,
    )

    assert captured["shell"] is False
    assert captured["timeout"] == 7
    assert captured["check"] is False
    assert isinstance(captured["command"], list)
    assert result["shell"] is False
    assert result["timeout_seconds"] == 7


def test_stage_timeout_is_enforced(tmp_path: Path) -> None:
    def timeout_runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=command, timeout=3, output="partial", stderr="timeout")

    result = run_stage(
        STAGE_BY_ID["closeout"],
        project_root=tmp_path,
        write_requested=False,
        timeout_seconds=3,
        runner=timeout_runner,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "stage_timeout"
    assert result["returncode"] is None
    assert result["timeout_seconds"] == 3


def test_evidence_pack_requires_explicit_write(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_explicit_evidence_pack_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        execute_builders=True,
        fixture_stage_results=_complete_stage_results(),
    )

    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data").exists()


def test_evidence_pack_only_writes_json_and_markdown_under_reports(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_explicit_evidence_pack_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        execute_builders=True,
        fixture_stage_results=_complete_stage_results(),
        write=True,
        no_write=False,
    )

    json_output = tmp_path / "data" / "reports" / "ocr_shadow_research_explicit_evidence_pack_v1.json"
    markdown_output = tmp_path / "data" / "reports" / "ocr_shadow_research_explicit_evidence_pack_v1.md"
    assert report["write_performed"] is True
    assert report["output_path"] == "data/reports/ocr_shadow_research_explicit_evidence_pack_v1.json"
    assert report["markdown_output_path"] == "data/reports/ocr_shadow_research_explicit_evidence_pack_v1.md"
    assert json_output.exists()
    assert markdown_output.exists()
    assert not (tmp_path / "data" / "runtime").exists()
    assert json.loads(json_output.read_text(encoding="utf-8"))["evidence_pack_decision"] == "MANTER_EM_RESEARCH"
    assert "research-only" in markdown_output.read_text(encoding="utf-8")


def test_failed_stage_returns_structured_blocked(tmp_path: Path) -> None:
    def failing_runner(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 2, stdout='{"status":"blocked","reason":"fixture_failed"}', stderr="failed")

    report = build_ocr_shadow_research_explicit_evidence_pack_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        execute_builders=True,
        selected_stage_ids=["closeout"],
        runner=failing_runner,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "explicit_evidence_pack_stage_failed"
    assert report["failed_stage_count"] == 1
    assert report["stage_results"][0]["reason"] == "fixture_failed"
    assert report["stage_results"][0]["returncode"] == 2


def test_complete_fixture_keeps_decision_manter_em_research(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_explicit_evidence_pack_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        execute_builders=True,
        fixture_stage_results=_complete_stage_results(),
    )

    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["evidence_pack_decision"] == "MANTER_EM_RESEARCH"
    assert report["executed_stage_count"] == len(ALLOWED_STAGE_IDS)
    assert report["evidence_sources_present"] == len(ALLOWED_STAGE_IDS)


def test_ready_for_shadow_observation_remains_false(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_explicit_evidence_pack_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        execute_builders=True,
        fixture_stage_results=_complete_stage_results(),
    )

    assert report["ready_for_shadow_observation"] is False
    assert report["gate_summary"]["ready_for_shadow_observation"] is False


def test_paper_observation_allowed_remains_false(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_explicit_evidence_pack_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        execute_builders=True,
        fixture_stage_results=_complete_stage_results(),
    )

    assert report["paper_observation_allowed"] is False
    assert report["safety_flags"]["paper_observation_allowed"] is False
    assert report["gate_summary"]["paper_observation_allowed"] is False


def test_no_operational_authority_flags(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_explicit_evidence_pack_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        execute_builders=True,
        fixture_stage_results=_complete_stage_results(),
    )

    assert report["operational_authority"] is False
    assert report["can_promote_rules"] is False
    assert report["can_apply_to_freqtrade"] is False
    assert report["can_apply_to_risk_manager"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False


def test_does_not_register_or_apply_shadow_rules(tmp_path: Path) -> None:
    report = build_ocr_shadow_research_explicit_evidence_pack_report(
        project_root=tmp_path,
        allow_runtime_read=True,
        execute_builders=True,
        fixture_stage_results=_complete_stage_results(),
    )

    assert report["registers_shadow_rules"] is False
    assert report["applies_shadow_rules"] is False
    assert report["updates_ai_shadow_runtime"] is False
    assert report["updates_freqtrade"] is False
    assert report["updates_risk_manager"] is False
    assert report["gate_summary"]["result_can_be_used_for_operations"] is False


def test_cli_no_runtime_json_executes() -> None:
    script = Path("scripts/build_ocr_shadow_research_explicit_evidence_pack_v1.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--no-write", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["evidence_pack_decision"] == "MANTER_EM_RESEARCH"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["paper_observation_allowed"] is False
    assert payload["ready_for_shadow_observation"] is False
    assert payload["write_performed"] is False
