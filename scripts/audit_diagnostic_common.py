"""Shared safe runtime for local audit and diagnostic scripts.

The helpers in this module intentionally do not submit orders, do not read
private keys, do not mutate .env files, and do not call private exchange APIs.
Scripts using this module inspect local inputs and write JSON reports only to
runtime paths that are expected to be ignored by git.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}
TRUE_VALUES = {"1", "true", "yes", "y", "on"}
DEFAULT_REPORT_DIR = Path("data/reports/audit_diagnostic")
FORBIDDEN_RUNTIME_FLAGS = (
    "LIVE_ENABLED",
    "ORDER_SUBMISSION_ENABLED",
    "REAL_ORDER_SUBMISSION_ENABLED",
)


class AuditDiagnosticError(RuntimeError):
    """Raised when a script cannot run inside the safe audit runtime."""


@dataclass(frozen=True)
class ScriptSpec:
    name: str
    purpose: str
    default_inputs: tuple[str, ...] = ()
    default_output: str | None = None
    risks: tuple[str, ...] = ()
    safe_mode: str = "local_readonly"
    network_policy: str = "no_network"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def env_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUE_VALUES


def build_parser(spec: ScriptSpec) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=spec.name,
        description=spec.purpose,
    )
    parser.add_argument(
        "--runtime-mode",
        choices=sorted(SAFE_RUNTIME_MODES),
        default="research",
        help="Safe runtime mode. Live mode is intentionally unsupported.",
    )
    parser.add_argument(
        "--input",
        dest="inputs",
        action="append",
        default=[],
        help="Local file or directory to inspect. Can be provided multiple times.",
    )
    parser.add_argument(
        "--output-report",
        default=spec.default_output or str(DEFAULT_REPORT_DIR / f"{spec.name}.json"),
        help="Runtime JSON report path. Defaults under data/reports/audit_diagnostic/.",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="Write the JSON report to the runtime output path.",
    )
    parser.add_argument(
        "--fail-on-missing-input",
        action="store_true",
        help="Return a non-zero exit code if any requested input path is missing.",
    )
    parser.add_argument(
        "--allow-public-network",
        action="store_true",
        help="Accepted for explicitness, but these institutional scripts do not fetch data.",
    )
    return parser


def run_script(spec: ScriptSpec, argv: Sequence[str] | None = None) -> int:
    parser = build_parser(spec)
    args = parser.parse_args(argv)
    try:
        report = build_report(spec, args)
        text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        print(text)
        if args.write_report:
            write_report(Path(args.output_report), report)
        if args.fail_on_missing_input and report["summary"]["missing_inputs"] > 0:
            return 2
        return 0
    except Exception as exc:
        error_report = {
            "script": spec.name,
            "status": "ERROR",
            "error": str(exc),
            "traceback": traceback.format_exc(limit=5),
            "timestamp_utc": utc_timestamp(),
        }
        print(
            json.dumps(error_report, ensure_ascii=False, indent=2, sort_keys=True),
            file=sys.stderr,
        )
        return 1


def build_report(spec: ScriptSpec, args: argparse.Namespace) -> dict[str, Any]:
    validate_runtime(args.runtime_mode)
    requested_inputs = tuple(args.inputs) if args.inputs else spec.default_inputs
    inspected_inputs = [inspect_path(Path(item)) for item in requested_inputs]
    missing_inputs = sum(1 for item in inspected_inputs if not item["exists"])
    report_path = Path(args.output_report)
    validate_runtime_report_path(report_path)
    return {
        "schema_version": 1,
        "script": asdict(spec),
        "runtime_mode": args.runtime_mode,
        "status": "OK" if missing_inputs == 0 else "INPUTS_MISSING",
        "timestamp_utc": utc_timestamp(),
        "safety": {
            "live_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "private_exchange_calls": False,
            "real_order_submission": False,
            "public_network_requested": bool(args.allow_public_network),
            "public_network_executed": False,
        },
        "inputs": inspected_inputs,
        "output_report": str(report_path),
        "summary": {
            "input_count": len(inspected_inputs),
            "missing_inputs": missing_inputs,
            "present_inputs": len(inspected_inputs) - missing_inputs,
        },
    }


def validate_runtime(runtime_mode: str) -> None:
    normalized = str(runtime_mode or "").strip().lower()
    if normalized not in SAFE_RUNTIME_MODES:
        raise AuditDiagnosticError(f"runtime_mode_not_allowed:{runtime_mode}")
    unsafe = [name for name in FORBIDDEN_RUNTIME_FLAGS if env_enabled(name)]
    if unsafe:
        raise AuditDiagnosticError(
            "unsafe_runtime_flags:" + ",".join(f"{name}=true" for name in unsafe)
        )


def validate_runtime_report_path(path: Path) -> None:
    normalized = path.as_posix()
    allowed_prefixes = (
        "data/reports/",
        "data/runtime/",
        "data/evidence/",
    )
    if path.is_absolute():
        return
    if not normalized.startswith(allowed_prefixes):
        raise AuditDiagnosticError(
            "output_report_must_be_runtime_path:data/reports_or_data/runtime"
        )


def inspect_path(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "kind": "missing",
    }
    if not path.exists():
        return result
    if path.is_dir():
        children = list(path.iterdir())
        result.update(
            {
                "kind": "directory",
                "child_count": len(children),
                "sample_children": sorted(child.name for child in children[:20]),
            }
        )
        return result
    stat = path.stat()
    result.update(
        {
            "kind": "file",
            "suffix": path.suffix.lower(),
            "size_bytes": stat.st_size,
            "modified_utc": datetime.fromtimestamp(
                stat.st_mtime,
                timezone.utc,
            )
            .isoformat()
            .replace("+00:00", "Z"),
        }
    )
    return result


def write_report(path: Path, report: dict[str, Any]) -> None:
    validate_runtime_report_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def main_for(spec: ScriptSpec) -> int:
    return run_script(spec)
