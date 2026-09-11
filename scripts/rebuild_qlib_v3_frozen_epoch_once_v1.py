#!/usr/bin/env python3
"""Execute exactly one no-write Qlib V3 rebuild from a frozen source tree."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from smartcrypto.learning.paper_autolearning.qlib_v3_reproducible_freeze_epoch import (
    FreezeV3Error,
    blocked_report,
    rebuild_qlib_v3_freeze_once_v1,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute one isolated read-only rebuild of a frozen Qlib V3 epoch."
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--epoch-dir", required=True)
    parser.add_argument("--manifest-name", default="freeze_v3.json")
    parser.add_argument("--output-json", required=True)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    return rebuild_qlib_v3_freeze_once_v1(
        project_root=Path(args.project_root),
        epoch_dir=Path(args.epoch_dir),
        manifest_name=str(args.manifest_name),
    )


def main() -> int:
    args = _parser().parse_args()
    try:
        report = run(args)
    except FreezeV3Error as exc:
        report = blocked_report(exc.reason)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, ImportError) as exc:
        report = blocked_report(f"controlled_rebuild_failure:{type(exc).__name__}")

    output = Path(args.output_json).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
