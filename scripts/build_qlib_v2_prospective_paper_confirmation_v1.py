#!/usr/bin/env python3
"""Build the frozen Qlib V2 prospective Paper confirmation report."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_paper_confirmation import (
    DEFAULT_FREEZE_SPEC_PATH,
    DEFAULT_REPORT_PATH,
    PROSPECTIVE_START_UTC,
    build_qlib_v2_prospective_paper_confirmation_v1,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze the Qlib V2 policy on certified pre-boundary Paper data and "
            "score only later closed trades using point-in-time market features."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--outcome-path", default="data/feedback/outcome_events.parquet")
    parser.add_argument(
        "--market-features-path",
        default="data/features/market_features_60d.parquet",
    )
    parser.add_argument(
        "--prospective-start-utc",
        default=PROSPECTIVE_START_UTC.isoformat(),
    )
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


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.project_root).resolve()
    freeze_path = _resolve(root, args.freeze_spec)
    report_path = _resolve(root, args.output_json)
    expected = _read_json(freeze_path)
    report = build_qlib_v2_prospective_paper_confirmation_v1(
        project_root=root,
        outcome_path=args.outcome_path,
        market_features_path=args.market_features_path,
        prospective_start_utc=args.prospective_start_utc,
        additional_execution_stress_bps=args.additional_execution_stress_bps,
        expected_freeze_spec=expected,
    )

    write_freeze_performed = False
    write_report_performed = False
    if args.write_freeze_spec:
        freeze = report.get("freeze_spec")
        if not isinstance(freeze, dict):
            raise RuntimeError("freeze_spec_not_available_for_write")
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
