from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.ops.dashboard_snapshots.source_catalog import (
    DASHBOARD_SNAPSHOT_FILENAMES,
    GLOBAL_STATUS_SNAPSHOT_FILENAME,
    SNAPSHOT_BUILD_SUMMARY_FILENAME,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_dashboard_snapshots.py"


def run_builder(project_root: Path, output_dir: Path, strict: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--once",
            "--strict",
            str(strict).lower(),
            "--project-root",
            str(project_root),
            "--output-dir",
            str(output_dir),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_generates_all_snapshots_with_custom_output_dir(tmp_path) -> None:
    output = tmp_path / "snapshots"
    result = run_builder(tmp_path, output, strict=False)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    expected = set(DASHBOARD_SNAPSHOT_FILENAMES.values()) | {
        GLOBAL_STATUS_SNAPSHOT_FILENAME,
        SNAPSHOT_BUILD_SUMMARY_FILENAME,
    }
    assert {path.name for path in output.iterdir()} == expected
    assert set(payload["generated_files"]) == expected
    assert payload["missing_required_sources"]
    assert payload["elapsed_seconds"] >= 0
    assert payload["safety"]["sends_orders"] is False
    assert payload["safety"]["uses_private_exchange"] is False
    assert not (output / "dashboard_alerts_queue_snapshot.json").exists()


def test_strict_mode_returns_controlled_exit_code_for_missing_required(tmp_path) -> None:
    result = run_builder(tmp_path, tmp_path / "strict-output", strict=True)

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert payload["missing_required_sources"]
    assert "Traceback" not in result.stderr


def test_script_import_is_safe() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import importlib.util; p=r'%s'; s=importlib.util.spec_from_file_location('dashboard_build_cli', p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('ok')" % SCRIPT],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "ok"
