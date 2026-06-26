#!/usr/bin/env python3
"""Build SMART FUTUROS Daily Learning Loop closeout handover V1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap_project_root(project_root: str) -> Path:
    root = Path(project_root).resolve()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build Daily Learning Loop closeout handover V1 as blocked research-only evidence."
    )
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON payload.")
    parser.add_argument("--no-write", action="store_true", help="Do not write any output file.")
    parser.add_argument("--output", default=None, help="Optional JSON output path outside runtime/data/log folders.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    project_root = _bootstrap_project_root(args.project_root)

    from smartcrypto.research.daily_learning_loop_closeout_handover import (
        build_daily_learning_loop_closeout_handover,
        is_output_path_forbidden,
        write_json_payload,
    )

    payload = build_daily_learning_loop_closeout_handover(project_root=args.project_root)
    payload["write_requested"] = bool(args.output and not args.no_write)
    payload["cli_reason"] = "no_write_requested" if args.no_write else "write_not_requested"

    if args.output and not args.no_write:
        if is_output_path_forbidden(project_root, args.output):
            payload["validation_errors"].append("forbidden_output_path")
            payload["cli_reason"] = "forbidden_output_path"
        else:
            output_path = write_json_payload(payload, args.output)
            payload["output_path"] = str(output_path)
            payload["write_performed"] = True
            payload["cli_reason"] = "explicit_output_written"

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"status={payload['status']}")
        print(f"decision={payload['decision']}")
        print(f"reason={payload['reason']}")
        print(f"daily_learning_loop_closed={str(payload['daily_learning_loop_closed']).lower()}")
        print(f"write_performed={str(payload['write_performed']).lower()}")
        if payload["validation_errors"]:
            print("validation_errors=" + ",".join(payload["validation_errors"]))

    return 0 if not payload["validation_errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
