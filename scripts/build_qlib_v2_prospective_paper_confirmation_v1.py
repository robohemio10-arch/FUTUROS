#!/usr/bin/env python3
"""Build and materialize the certified Qlib V2 prospective Paper freeze."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_paper_confirmation import (
    DEFAULT_FREEZE_SPEC_PATH,
    DEFAULT_REPORT_PATH,
    build_qlib_v2_prospective_paper_confirmation_v1,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Certify and freeze the Qlib V2 Paper policy only after the exact source "
            "commit has a successful CI run. Initial freeze materialization starts "
            "the prospective boundary at the materialization timestamp."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--source-repo-root",
        default=None,
        help=(
            "Git worktree containing the certified implementation. Required for the "
            "first --write-freeze-spec."
        ),
    )
    parser.add_argument("--outcome-path", default="data/feedback/outcome_events.parquet")
    parser.add_argument(
        "--market-features-path",
        default="data/features/market_features_60d.parquet",
    )
    parser.add_argument(
        "--prospective-start-utc",
        default=None,
        help=(
            "Explicit boundary for research dry-runs. For the first certified freeze "
            "write this is generated automatically at materialization time."
        ),
    )
    parser.add_argument("--certified-implementation-commit", default=None)
    parser.add_argument("--certified-ci-run-id", type=int, default=None)
    parser.add_argument("--certified-ci-completed-at-utc", default=None)
    parser.add_argument("--additional-execution-stress-bps", type=float, default=5.0)
    parser.add_argument("--freeze-spec", default=str(DEFAULT_FREEZE_SPEC_PATH))
    parser.add_argument("--output-json", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--write-freeze-spec", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _git_output(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        reason = (completed.stderr or completed.stdout).strip().splitlines()
        detail = reason[0] if reason else "unknown_git_error"
        raise RuntimeError(f"git_command_failed:{detail[:300]}")
    return completed.stdout.strip()


def _validate_source_certification(
    *,
    source_repo_root: Path,
    certified_implementation_commit: str | None,
) -> str:
    if certified_implementation_commit is None:
        raise RuntimeError("certified_implementation_commit_required_for_freeze_write")
    requested = certified_implementation_commit.strip().lower()
    head = _git_output(source_repo_root, "rev-parse", "HEAD").lower()
    if requested != head:
        raise RuntimeError(
            "certified_implementation_commit_does_not_match_source_head:"
            f"{requested}:{head}"
        )
    status = _git_output(source_repo_root, "status", "--porcelain")
    if status:
        raise RuntimeError("source_repo_must_be_clean_before_freeze_write")
    return head


def _parse_utc(value: str, *, field: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field}_must_be_timezone_aware")
    return parsed.astimezone(UTC)


def _initial_freeze_inputs(
    args: argparse.Namespace,
    *,
    expected: dict[str, Any] | None,
) -> dict[str, Any]:
    if expected is not None:
        return {
            "prospective_start_utc": None,
            "certified_implementation_commit": None,
            "certified_ci_run_id": None,
            "certified_ci_completed_at_utc": None,
            "freeze_materialized_at_utc": None,
        }

    if not args.write_freeze_spec:
        return {
            "prospective_start_utc": args.prospective_start_utc,
            "certified_implementation_commit": args.certified_implementation_commit,
            "certified_ci_run_id": args.certified_ci_run_id,
            "certified_ci_completed_at_utc": args.certified_ci_completed_at_utc,
            "freeze_materialized_at_utc": None,
        }

    if args.source_repo_root is None:
        raise RuntimeError("source_repo_root_required_for_initial_freeze_write")
    if args.certified_ci_run_id is None or args.certified_ci_run_id <= 0:
        raise RuntimeError("certified_ci_run_id_required_for_initial_freeze_write")
    if args.certified_ci_completed_at_utc is None:
        raise RuntimeError("certified_ci_completed_at_utc_required_for_initial_freeze_write")

    source_root = Path(args.source_repo_root).resolve()
    certified_head = _validate_source_certification(
        source_repo_root=source_root,
        certified_implementation_commit=args.certified_implementation_commit,
    )
    ci_completed = _parse_utc(
        args.certified_ci_completed_at_utc,
        field="certified_ci_completed_at_utc",
    )
    freeze_time = datetime.now(UTC)
    if freeze_time < ci_completed:
        raise RuntimeError("freeze_materialization_precedes_certified_ci_completion")
    if args.prospective_start_utc is not None:
        requested_boundary = _parse_utc(
            args.prospective_start_utc,
            field="prospective_start_utc",
        )
        if requested_boundary != freeze_time:
            raise RuntimeError(
                "initial_freeze_boundary_is_automatic_and_must_equal_materialization_time"
            )

    return {
        "prospective_start_utc": freeze_time,
        "certified_implementation_commit": certified_head,
        "certified_ci_run_id": args.certified_ci_run_id,
        "certified_ci_completed_at_utc": ci_completed,
        "freeze_materialized_at_utc": freeze_time,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.project_root).resolve()
    freeze_path = _resolve(root, args.freeze_spec)
    report_path = _resolve(root, args.output_json)
    expected = _read_json(freeze_path)
    freeze_inputs = _initial_freeze_inputs(args, expected=expected)

    report = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=root,
        outcome_path=args.outcome_path,
        market_features_path=args.market_features_path,
        prospective_start_utc=freeze_inputs["prospective_start_utc"],
        certified_implementation_commit=freeze_inputs[
            "certified_implementation_commit"
        ],
        certified_ci_run_id=freeze_inputs["certified_ci_run_id"],
        certified_ci_completed_at_utc=freeze_inputs[
            "certified_ci_completed_at_utc"
        ],
        freeze_materialized_at_utc=freeze_inputs["freeze_materialized_at_utc"],
        additional_execution_stress_bps=args.additional_execution_stress_bps,
        expected_freeze_spec=expected,
    )

    write_freeze_performed = False
    write_report_performed = False

    if args.write_freeze_spec:
        freeze = report.get("freeze_spec")
        if report.get("status") == "blocked":
            raise RuntimeError(
                f"freeze_write_blocked:{report.get('reason', 'unknown_reason')}"
            )
        if not isinstance(freeze, dict):
            raise RuntimeError("freeze_spec_not_available_for_write")
        if freeze.get("freeze_provenance_complete") is not True:
            raise RuntimeError("freeze_provenance_incomplete")
        if expected is not None and expected != freeze:
            raise RuntimeError("existing_freeze_spec_is_immutable_and_does_not_match")
        if expected is None:
            _write_json_atomic(freeze_path, freeze)
            write_freeze_performed = True

    if args.write_report:
        output = dict(report)
        output["write_requested"] = True
        output["write_freeze_spec_performed"] = write_freeze_performed
        output["write_report_performed"] = True
        output["write_performed"] = True
        _write_json_atomic(report_path, output)
        write_report_performed = True

    report["write_requested"] = bool(args.write_freeze_spec or args.write_report)
    report["write_freeze_spec_performed"] = write_freeze_performed
    report["write_report_performed"] = write_report_performed
    report["freeze_spec_path"] = str(freeze_path)
    report["output_json_path"] = str(report_path)
    report["write_performed"] = bool(write_freeze_performed or write_report_performed)
    return report


def main() -> int:
    args = _parser().parse_args()
    try:
        report = run(args)
    except Exception as exc:
        report = {
            "status": "blocked",
            "reason": str(exc).splitlines()[0][:500],
            "error_type": type(exc).__name__,
            "decision": "MANTER_EM_RESEARCH",
            "promotion_allowed": False,
            "operational_authority": False,
            "changes_risk": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "writes_runtime": False,
            "write_performed": False,
        }
    if args.json:
        print(json.dumps(report, sort_keys=True, default=str))
    else:
        print(
            f"status={report.get('status')} reason={report.get('reason')} "
            f"prospective={report.get('prospective_closed_trade_count', 0)} "
            f"selected={report.get('prospective_selected_trade_count', 0)}"
        )
    return 2 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
