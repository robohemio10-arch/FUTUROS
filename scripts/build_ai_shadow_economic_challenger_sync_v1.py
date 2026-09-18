#!/usr/bin/env python3
"""Build the read-only Branch 09 AI Shadow economic challenger comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.research.aibot_parity.ai_shadow_economic_challenger import (
    build_ai_shadow_economic_challenger_sync_v1,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="Data/project root used to resolve the canonical V3 evidence store.",
    )
    parser.add_argument(
        "--evidence-path",
        type=Path,
        default=None,
        help="Optional explicit canonical evidence.json path.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_ai_shadow_economic_challenger_sync_v1(
        project_root=args.project_root,
        evidence_path=args.evidence_path,
    )

    if args.json:
        print(json.dumps(report, sort_keys=True, allow_nan=False))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))

    return 2 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
