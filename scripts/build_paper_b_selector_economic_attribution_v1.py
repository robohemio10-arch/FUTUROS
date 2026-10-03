#!/usr/bin/env python3
"""Build read-only Paper-B selector economic attribution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.canonical_treatment.selector_economic_attribution import (  # noqa: E402
    run_attribution,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = run_attribution(
        project_root=args.project_root,
        runtime_root=args.runtime_root,
        write_report=args.write_report,
    )
    print(json.dumps(report, sort_keys=True, allow_nan=False, default=str,
                     separators=(",", ":") if args.json else None,
                     indent=None if args.json else 2))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
