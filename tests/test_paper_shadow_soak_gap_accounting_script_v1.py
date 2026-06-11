from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_paper_shadow_soak_continuity_and_gap_accounting.py"


def test_script_emits_compact_summary(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema_version"] == "paper_shadow_soak_continuity_gap_accounting_v1"
    assert payload["status"] == "evidence_missing"
    assert payload["live_release_allowed"] is False
    assert payload["canary_release_allowed"] is False


def test_script_json_and_write_materializes_runtime_report(tmp_path: Path) -> None:
    output = tmp_path / "data/reports/paper_shadow_soak_gap_accounting_report.json"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--output", str(output), "--write", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert output.exists()
    assert payload["write_performed"] is True
    assert payload["sends_orders"] is False
