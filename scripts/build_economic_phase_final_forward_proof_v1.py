#!/usr/bin/env python3
"""Build the Branch 17 final forward economic proof, read-only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.aibot_parity.economic_phase_final_forward_proof import (
    build_economic_phase_final_forward_proof_v1,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--project-root", default=".")
    value.add_argument("--runtime-root", required=True)
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = build_economic_phase_final_forward_proof_v1(
        project_root=args.project_root,
        runtime_root=args.runtime_root,
    )
    print(
        json.dumps(
            report,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            default=str,
            separators=(",", ":") if args.json else None,
            indent=None if args.json else 2,
        )
    )
    return 0 if report.get("status") in {"ok", "waiting"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
