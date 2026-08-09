from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_paper_momentum_fixed_threshold_walkforward_holdout_v1.py"


def test_cli_runs_directly_without_pythonpath() -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    stdout = completed.stdout.casefold()
    assert "frozen" in stdout
    assert "walk-forward" in stdout
    assert "holdout" in stdout
