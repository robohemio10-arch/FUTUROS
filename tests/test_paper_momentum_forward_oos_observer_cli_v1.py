from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_paper_momentum_forward_oos_observer_v1.py"


def test_cli_bootstrap_runs_without_pythonpath() -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "post-freeze paper trades" in completed.stdout
    assert "--allow-runtime-read" in completed.stdout
