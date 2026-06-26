#!/usr/bin/env python
"""CLI for the Branch 11 research-only IA Shadow feedback bridge."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORTS))

from smartcrypto.research.daily_learning_ai_shadow_feedback_bridge import (
    build_daily_learning_ai_shadow_feedback_bridge_report,
    validate_daily_learning_ai_shadow_feedback_bridge_report,
)

BLOCKED_OUTPUT_PARTS = {"data", "runtime", "reports", "logs", "freqtrade"}


def _is_blocked_output_path(project_root: Path, output_path: Path) -> bool:
    try:
        relative = output_path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return False
    parts = {part.lower() for part in relative.parts}
    return bool(parts & BLOCKED_OUTPUT_PARTS)


def _json_dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the SMART FUTUROS daily learning IA Shadow feedback bridge payload."
    )
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode.")
    parser.add_argument("--output", default=None, help="Optional output path outside blocked runtime/data dirs.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    project_root = Path(args.project_root)
    output_path = Path(args.output) if args.output else None
    write_requested = output_path is not None and not args.no_write

    report = build_daily_learning_ai_shadow_feedback_bridge_report(project_root=project_root)
    report["write_requested"] = bool(write_requested)
    report["write_performed"] = False
    report["output_path"] = str(output_path) if output_path is not None else None
    report["cli_reason"] = "no_write_requested" if args.no_write else "no_write_default"

    if write_requested and output_path is not None:
        if _is_blocked_output_path(project_root, output_path):
            report["validation_errors"] = list(report.get("validation_errors", [])) + [
                "blocked_output_path"
            ]
            if args.json:
                print(_json_dump(report))
            return 2
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report["validation_errors"] = validate_daily_learning_ai_shadow_feedback_bridge_report(report)
        output_path.write_text(_json_dump(report) + "\n", encoding="utf-8")
        report["write_performed"] = True
        report["cli_reason"] = "output_written_to_allowed_path"

    if args.json:
        print(_json_dump(report))
    else:
        print(f"status={report['status']} decision={report['decision']} input_mode={report['input_mode']}")

    return 0 if not report.get("validation_errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
