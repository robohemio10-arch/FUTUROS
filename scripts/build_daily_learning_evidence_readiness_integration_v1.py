"""CLI builder for the Daily Learning evidence/readiness integration snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_CURRENT_FILE = Path(__file__).resolve()
_PROJECT_ROOT = _CURRENT_FILE.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from smartcrypto.research.daily_learning_evidence_readiness_integration import (  # noqa: E402
    build_daily_learning_evidence_readiness_integration_snapshot,
    validate_daily_learning_evidence_readiness_integration_snapshot,
)
from smartcrypto.learning.paper_autotrain_feedback_loop import build_paper_autotrain_feedback_loop_v1  # noqa: E402
from smartcrypto.learning.paper_autotrain_feedback_loop.loop import (  # noqa: E402
    DEFAULT_REPORT_JSON as DEFAULT_PAPER_AUTOTRAIN_REPORT_JSON,
)

_FORBIDDEN_OUTPUT_PARTS = {
    "data",
    "runtime",
    "reports",
    "logs",
    "freqtrade",
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_payload(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {
            "schema_version": "paper_autotrain_feedback_loop_v1",
            "status": "blocked",
            "reason": "invalid_paper_autotrain_feedback_loop_report_json",
            "decision": "BLOCKED",
            "report_sha256": _file_sha256(path),
            "blockers": ["invalid_paper_autotrain_feedback_loop_report_json"],
            "warnings": [],
            "write_performed": False,
            "research_only": True,
            "read_only": True,
            "paper_only": True,
            "shadow_only": True,
        }
    if not isinstance(payload, dict):
        return {
            "schema_version": "paper_autotrain_feedback_loop_v1",
            "status": "blocked",
            "reason": "invalid_paper_autotrain_feedback_loop_report_root",
            "decision": "BLOCKED",
            "report_sha256": _file_sha256(path),
            "blockers": ["invalid_paper_autotrain_feedback_loop_report_root"],
            "warnings": [],
            "write_performed": False,
            "research_only": True,
            "read_only": True,
            "paper_only": True,
            "shadow_only": True,
        }
    payload["source_report_write_performed"] = payload.get("write_performed")
    payload["source_report_run_qlib_train_requested"] = payload.get("run_qlib_train_requested")
    payload["source_report_run_ai_shadow_train_requested"] = payload.get("run_ai_shadow_train_requested")
    payload["write_performed"] = False
    payload["run_qlib_train_requested"] = False
    payload["run_ai_shadow_train_requested"] = False
    payload["report_sha256"] = _file_sha256(path)
    payload["report_path"] = str(path)
    return payload


def _resolve_path(project_root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_root / path


def _load_or_probe_paper_autotrain_payload(args: argparse.Namespace, project_root: Path) -> dict[str, Any] | None:
    if args.skip_paper_autotrain_probe:
        return None

    report_path = _resolve_path(project_root, args.paper_autotrain_report, DEFAULT_PAPER_AUTOTRAIN_REPORT_JSON)
    loaded = _load_json_payload(report_path)
    if loaded is not None:
        loaded["daily_evidence_source_mode"] = "existing_report"
        return loaded

    payload = build_paper_autotrain_feedback_loop_v1(
        project_root=project_root,
        write_report=False,
        allow_runtime_read=False,
        run_qlib_train=False,
        run_ai_shadow_train=False,
    )
    payload["daily_evidence_source_mode"] = "no_write_probe"
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the blocked Daily Learning evidence/readiness integration snapshot."
    )
    parser.add_argument("--project-root", default=".", help="Project root used for metadata only.")
    parser.add_argument("--json", action="store_true", help="Emit compact JSON to stdout.")
    parser.add_argument("--no-write", action="store_true", help="Do not write an output file.")
    parser.add_argument("--output", default=None, help="Optional explicit output path outside forbidden runtime trees.")
    parser.add_argument(
        "--paper-autotrain-report",
        default=None,
        help="Optional existing paper auto-train feedback loop report. Defaults to data/reports/paper_autotrain_feedback_loop_v1.json.",
    )
    parser.add_argument(
        "--skip-paper-autotrain-probe",
        action="store_true",
        help="Do not enrich the snapshot with the safe no-write paper auto-train feedback loop probe.",
    )
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
    paper_autotrain_payload = _load_or_probe_paper_autotrain_payload(args, project_root)
    snapshot = build_daily_learning_evidence_readiness_integration_snapshot(
        project_root=project_root,
        paper_autotrain_feedback_loop_payload=paper_autotrain_payload,
    )
    validation_errors = validate_daily_learning_evidence_readiness_integration_snapshot(snapshot)
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
