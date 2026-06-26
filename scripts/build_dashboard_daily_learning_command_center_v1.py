"""CLI builder for the Dashboard Daily Learning Command Center snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_CURRENT_FILE = Path(__file__).resolve()
_PROJECT_ROOT = _CURRENT_FILE.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from smartcrypto.dashboard.services.daily_learning_command_center import (  # noqa: E402
    build_dashboard_daily_learning_command_center_snapshot,
    validate_dashboard_daily_learning_command_center_snapshot,
)

_FORBIDDEN_OUTPUT_PARTS = {
    "data",
    "runtime",
    "reports",
    "logs",
    "freqtrade",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the read-only Dashboard Daily Learning Command Center snapshot."
    )
    parser.add_argument("--project-root", default=".", help="Project root used for metadata only.")
    parser.add_argument("--json", action="store_true", help="Emit compact JSON to stdout.")
    parser.add_argument("--no-write", action="store_true", help="Do not write an output file.")
    parser.add_argument("--output", default=None, help="Optional explicit output path outside forbidden runtime trees.")
    return parser


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_output_path(project_root: Path, output_path: Path) -> list[str]:
    errors: list[str] = []
    resolved_project = project_root.resolve()
    resolved_output = output_path.resolve()
    if _is_relative_to(resolved_output, resolved_project):
        relative_parts = {part.lower() for part in resolved_output.relative_to(resolved_project).parts}
        blocked = sorted(relative_parts.intersection(_FORBIDDEN_OUTPUT_PARTS))
        if blocked:
            errors.append("output_path_under_forbidden_runtime_tree:" + ",".join(blocked))
    if resolved_output.suffix.lower() != ".json":
        errors.append("output_path_must_be_json")
    return errors


def build_report_from_args(args: argparse.Namespace) -> dict[str, Any]:
    project_root = Path(args.project_root)
    snapshot = build_dashboard_daily_learning_command_center_snapshot(project_root=project_root)
    validation_errors = validate_dashboard_daily_learning_command_center_snapshot(snapshot)
    snapshot["validation_errors"] = sorted(set(snapshot.get("validation_errors", []) + validation_errors))

    write_requested = bool(args.output) and not args.no_write
    snapshot["write_requested"] = write_requested
    snapshot["write_performed"] = False
    snapshot["output_path"] = None
    snapshot["cli_reason"] = "no_write_requested" if args.no_write or not args.output else "write_requested"

    if args.output:
        output_path = Path(args.output)
        output_errors = _validate_output_path(project_root, output_path)
        if output_errors:
            snapshot["status"] = "blocked"
            snapshot["validation_errors"] = sorted(set(snapshot["validation_errors"] + output_errors))
            snapshot["cli_reason"] = "output_path_blocked"
            return snapshot

        if write_requested:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            snapshot["write_performed"] = True
            snapshot["output_path"] = str(output_path)
            snapshot["cli_reason"] = "explicit_output_written"

    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    snapshot = build_report_from_args(args)
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not snapshot.get("validation_errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
