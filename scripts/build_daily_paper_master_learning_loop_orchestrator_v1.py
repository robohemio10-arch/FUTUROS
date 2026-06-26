#!/usr/bin/env python
"""CLI for the SMART FUTUROS Daily Learning Loop research orchestrator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_FOR_IMPORTS))

from smartcrypto.research.daily_paper_master_learning_loop_orchestrator import (
    build_daily_paper_master_learning_loop_orchestrator_report,
    validate_daily_paper_master_learning_loop_orchestrator_report,
)

_BLOCKED_OUTPUT_ROOTS = {"data", "runtime", "reports", "logs", "freqtrade"}


def _json_default(value: Any) -> str:
    return str(value)


def _is_blocked_output_path(project_root: Path, output: Path) -> bool:
    resolved_root = project_root.resolve()
    resolved_output = output.resolve()
    try:
        relative = resolved_output.relative_to(resolved_root)
    except ValueError:
        return False
    if not relative.parts:
        return True
    return relative.parts[0].lower() in _BLOCKED_OUTPUT_ROOTS


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Daily Paper/Master Learning Loop research-only orchestrator payload."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true", dest="emit_json")
    parser.add_argument("--no-write", action="store_true", default=False)
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--execute-stage-builders",
        action="store_true",
        default=False,
        help="Optionally call existing research-only stage builders. Default is disabled.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv or sys.argv[1:]))
    project_root = Path(args.project_root)
    report = build_daily_paper_master_learning_loop_orchestrator_report(
        project_root=project_root,
        execute_stage_builders=bool(args.execute_stage_builders),
    )

    output_path: Path | None = Path(args.output) if args.output else None
    write_requested = bool(output_path and not args.no_write)
    report["write_requested"] = write_requested
    report["write_performed"] = False
    report["output_path"] = str(output_path) if output_path else None
    report["cli_reason"] = "no_write_requested" if not write_requested else "write_requested"

    if write_requested and output_path is not None:
        if _is_blocked_output_path(project_root, output_path):
            report["status"] = "blocked"
            report.setdefault("validation_errors", [])
            report["validation_errors"] = list(report["validation_errors"]) + [
                "output_path_under_blocked_project_area"
            ]
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(report, indent=2, sort_keys=True, default=_json_default),
                encoding="utf-8",
            )
            report["write_performed"] = True

    report["validation_errors"] = sorted(
        set(validate_daily_paper_master_learning_loop_orchestrator_report(report))
        | set(report.get("validation_errors", []))
    )

    if args.emit_json:
        print(json.dumps(report, sort_keys=True, default=_json_default))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
