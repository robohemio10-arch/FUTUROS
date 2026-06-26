#!/usr/bin/env python3
"""CLI for the SMART FUTUROS Daily Learning paper scheduler contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _bootstrap_project_root() -> Path:
    script_path = Path(__file__).resolve()
    project_root = script_path.parents[1]
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
    return project_root


_BOOTSTRAPPED_PROJECT_ROOT = _bootstrap_project_root()

from smartcrypto.research.daily_learning_scheduler_paper import (  # noqa: E402
    build_daily_learning_scheduler_paper_report,
    output_path_is_forbidden,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the SMART FUTUROS Daily Learning paper scheduler contract. "
            "The command is research-only by default and never registers a real scheduler."
        )
    )
    parser.add_argument("--project-root", default=".", help="Project root used in the generated scheduler contract.")
    parser.add_argument("--hour-utc", type=int, default=3, help="UTC hour for the deterministic paper scheduler contract.")
    parser.add_argument("--minute-utc", type=int, default=15, help="UTC minute for the deterministic paper scheduler contract.")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON output path. Refused under data/, runtime/, reports/, logs/ or freqtrade/.",
    )
    parser.add_argument("--no-write", action="store_true", help="Do not write output even when --output is provided.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON to stdout.")
    return parser


def _serialize(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    project_root = Path(args.project_root)
    write_requested = bool(args.output) and not bool(args.no_write)
    output_path = Path(args.output) if args.output else None

    payload = build_daily_learning_scheduler_paper_report(
        project_root=str(project_root),
        hour_utc=args.hour_utc,
        minute_utc=args.minute_utc,
    )
    payload["write_requested"] = write_requested
    payload["cli_reason"] = (
        "no_write_requested"
        if args.no_write
        else "write_requested"
        if args.output
        else "no_output_requested"
    )

    if output_path is not None:
        payload["output_path"] = str(output_path)

    if write_requested:
        if output_path_is_forbidden(project_root, output_path):
            payload["status"] = "blocked"
            payload["reason"] = "output_path_under_forbidden_runtime_root"
            payload["write_performed"] = False
            payload.setdefault("validation_errors", []).append("output_path_under_forbidden_runtime_root")
            if args.json:
                print(_serialize(payload))
            else:
                print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
            return 2

        assert output_path is not None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        payload["write_performed"] = True
    else:
        payload["write_performed"] = False

    if args.json:
        print(_serialize(payload))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
