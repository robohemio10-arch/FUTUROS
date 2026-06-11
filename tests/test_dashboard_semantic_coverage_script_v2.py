from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_dashboard_semantic_coverage_v2.py"


def test_semantic_audit_script_summary_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(ROOT)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert payload["reason"] == "semantic_coverage_current"
    assert payload["summary"]["page_count"] == 8


def test_semantic_audit_script_json_contains_safety_flags() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(ROOT), "--json"],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == "dashboard_semantic_coverage_audit_v2"
    assert payload["safety"]["dashboard_readonly"] is True
    assert payload["safety"]["sends_orders"] is False
    assert payload["findings"]
