from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_paper_shadow_soak_anchor_continuity_pack.py"


def test_script_json_mode_is_readonly_by_default(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == "paper_shadow_soak_anchor_continuity_pack_v1"
    assert payload["write_performed"] is False
    assert payload["live_release_allowed"] is False
    assert payload["canary_release_allowed"] is False
    assert not (tmp_path / "data/reports/paper_shadow_soak_anchor_continuity_pack.json").exists()


def test_script_summary_mode_contains_safety_flags(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["write_performed"] is False
    assert payload["live_release_allowed"] is False
    assert payload["canary_release_allowed"] is False
    assert payload["sends_orders"] is False
    assert payload["changes_risk"] is False


def test_script_write_flag_materializes_output(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--write", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    output = tmp_path / "data/reports/paper_shadow_soak_anchor_continuity_pack.json"
    assert payload["write_performed"] is True
    assert output.exists()
