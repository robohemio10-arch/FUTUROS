#!/usr/bin/env python3
"""Run the deterministic AIBOT Parity V2 software DoD audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.research.aibot_parity_v2_closeout import audit_aibot_parity_v2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="Repository root to audit (default: current directory).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = audit_aibot_parity_v2(args.project_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["aibot_parity_v2_software_dod"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
