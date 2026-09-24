#!/usr/bin/env python3
"""Audit active Paper A/B runtimes; optionally register the causal cohort once."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.learning.qlib_v3_prospective.contracts import EvidenceError
from smartcrypto.research.canonical_treatment.causal_governance import run_governance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run_governance(args.project_root, args.runtime_root, activate=args.activate)
    except (EvidenceError, OSError) as exc:
        report = {
            "status": "blocked",
            "reason": str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__,
        }
    print(json.dumps(report, sort_keys=True, indent=None if args.json else 2, allow_nan=False))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
