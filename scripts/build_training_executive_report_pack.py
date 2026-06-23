#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from smartcrypto.research.training_executive_report_pack import (
    ExecutiveReportPackConfig,
    resolve_paths,
    run_training_executive_report_pack,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the read-only SMART FUTUROS training executive report pack."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--output-html")
    parser.add_argument("--strict", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Write runtime report outputs.")
    mode.add_argument("--no-write", action="store_true", help="Validate in memory (default).")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = resolve_paths(
        args.project_root,
        output_json=args.output_json,
        output_md=args.output_md,
        output_html=args.output_html,
    )
    config = ExecutiveReportPackConfig(strict=bool(args.strict))
    try:
        result = run_training_executive_report_pack(
            paths,
            config,
            write=bool(args.write),
        )
    except Exception as exc:
        payload = {
            "status": "error",
            "reason": "unexpected_structural_error",
            "error_type": type(exc).__name__,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 1
    encoded = json.dumps(result.pack, ensure_ascii=False, sort_keys=True, allow_nan=False)
    print(encoded if args.json else json.dumps(result.pack, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
