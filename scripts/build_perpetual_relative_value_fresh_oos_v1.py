#!/usr/bin/env python3
"""Calibrate old history, seal configuration, then evaluate the fresh OOS once."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.aibot_parity.perpetual_relative_value_fresh_oos import run_relative_value  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--phase", choices=("calibrate", "evaluate"), required=True)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--write-evidence", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = run_relative_value(
        project_root=args.project_root,
        artifact_root=args.artifact_root,
        phase=args.phase,
        allow_download=args.allow_download,
        write_evidence=args.write_evidence,
    )
    print(json.dumps(report, sort_keys=True, allow_nan=False, indent=None if args.json else 2))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
