#!/usr/bin/env python3
"""Read-only healthcheck for the AIBOT prospective runtime foundation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT_TEXT = str(_PROJECT_ROOT)
if _PROJECT_ROOT_TEXT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_TEXT)

from smartcrypto.research.aibot_parity_paper_ab_prospective_collector.runtime_foundation import (  # noqa: E402
    DEFAULT_RUNTIME_CONFIG,
    RUNTIME_SAFETY_FLAGS,
    check_runtime_foundation_health,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-foundation-config", default=str(DEFAULT_RUNTIME_CONFIG))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        report = check_runtime_foundation_health(
            project_root=args.project_root,
            runtime_config_path=args.runtime_foundation_config,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        report = {
            "schema_version": "aibot_parity_prospective_runtime_health_v1",
            "status": "blocked",
            "reason": str(exc).splitlines()[0].strip()[:300] or type(exc).__name__,
            "collection_clock_started": False,
            "prospective_collection_running_proven": False,
            "safety_flags": dict(RUNTIME_SAFETY_FLAGS),
            **RUNTIME_SAFETY_FLAGS,
        }
    if not args.quiet:
        if args.json:
            print(json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False))
        else:
            print(f"status={report.get('status')} reason={report.get('reason')}")
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
