#!/usr/bin/env python3
"""Resolve Qlib V2 prospective signal observations to exact Paper outcomes."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_outcome_resolver import (
    DEFAULT_FREEZE_SPEC_PATH,
    DEFAULT_OBSERVER_LEDGER_PATH,
    DEFAULT_OUTCOME_PATH,
    DEFAULT_PAPER_SNAPSHOT_DB_PATH,
    DEFAULT_REPORT_PATH,
    build_qlib_v2_prospective_outcome_resolution_v1,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve the immutable Qlib V2 prospective signal ledger through exact "
            "decision_event_id -> Freqtrade enter_tag -> paper_trade_id -> "
            "outcome_events.trade_id correlation."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--freeze-spec", default=str(DEFAULT_FREEZE_SPEC_PATH))
    parser.add_argument("--observer-ledger", default=str(DEFAULT_OBSERVER_LEDGER_PATH))
    parser.add_argument(
        "--paper-snapshot-db",
        default=str(DEFAULT_PAPER_SNAPSHOT_DB_PATH),
    )
    parser.add_argument("--outcome-path", default=str(DEFAULT_OUTCOME_PATH))
    parser.add_argument("--output-json", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _validate_report_path(root: Path, path: Path) -> None:
    allowed_root = (root / "data" / "research" / "qlib_v2").resolve()
    candidate = path.resolve()
    try:
        candidate.relative_to(allowed_root)
    except ValueError as exc:
        raise RuntimeError(
            f"prospective_resolution_report_outside_research_root:{candidate}"
        ) from exc
    if candidate.suffix.lower() != ".json":
        raise RuntimeError("prospective_resolution_report_must_be_json")


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    ) + "\n"
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


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.project_root).resolve()
    output_path = _resolve(root, args.output_json)
    if args.write_report:
        _validate_report_path(root, output_path)

    report = build_qlib_v2_prospective_outcome_resolution_v1(
        project_root=root,
        freeze_spec_path=args.freeze_spec,
        observer_ledger_path=args.observer_ledger,
        paper_snapshot_db_path=args.paper_snapshot_db,
        outcome_path=args.outcome_path,
    )

    write_performed = False
    if args.write_report:
        output = dict(report)
        output["write_requested"] = True
        output["write_performed"] = True
        output["writes_research_report"] = True
        output["writes_runtime"] = False
        _write_json_atomic(output_path, output)
        write_performed = True

    report["write_requested"] = bool(args.write_report)
    report["write_performed"] = write_performed
    report["writes_research_report"] = write_performed
    report["writes_runtime"] = False
    report["output_json_path"] = str(output_path)
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
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "promotion_allowed": False,
            "prospective_profit_certified": False,
            "changes_risk": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "writes_runtime": False,
            "writes_research_report": False,
            "write_performed": False,
        }
    if args.json:
        print(json.dumps(report, sort_keys=True, default=str))
    else:
        print(
            f"status={report.get('status')} reason={report.get('reason')} "
            f"resolved={report.get('resolved_decision_count', 0)} "
            f"selected={report.get('selected_resolved_trade_count', 0)}"
        )
    return 2 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
