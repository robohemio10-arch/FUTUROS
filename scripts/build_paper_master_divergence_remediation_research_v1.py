#!/usr/bin/env python3
"""Build Paper/Master divergence remediation research report.

Default execution is no-write and no-runtime-row loaded. The script is safe to
run directly as ``python scripts/build_paper_master_divergence_remediation_research_v1.py``
without relying on PYTHONPATH or an editable install.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.paper_master_divergence_remediation import (  # noqa: E402
    build_paper_master_divergence_remediation_report,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a research-only Paper/Master divergence remediation report "
            "without applying changes to Freqtrade, RiskManager, Qlib, IA Shadow, "
            "models, rules, runtime, or orders."
        )
    )
    parser.add_argument("--project-root", default=".", help="Accepted for CLI compatibility; no runtime rows are loaded.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Request write mode; blocked by read-only contract.")
    mode.add_argument("--no-write", action="store_true", help="Evaluate in memory without writing (default).")
    parser.add_argument("--json", action="store_true", help="Emit compact JSON.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    _ = Path(args.project_root).resolve()
    report = build_paper_master_divergence_remediation_report(write_requested=bool(args.write))
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.json else 2,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
