"""Build explicit-ID paper/shadow correlation evidence without runtime authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from smartcrypto.runtime.integrity_traceability_v2 import (
    AtomicWriteError,
    AtomicWritePolicy,
    atomic_write_json,
    build_correlation_ledger,
)

DEFAULT_REPORT_PATH = (
    "data/reports/runtime_integrity_traceability_ledger_v2.json"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a research-only correlation ledger from explicit identifiers."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--input")
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def load_events(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    raw_events = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(raw_events, list):
        raise ValueError("correlation_input_events_list_required")
    if not all(isinstance(item, dict) for item in raw_events):
        raise ValueError("correlation_input_event_object_required")
    return raw_events


def build_report(
    *,
    project_root: Path,
    input_path: Path | None,
    report_path: Path,
    write_report: bool,
) -> dict[str, Any]:
    root = project_root.resolve(strict=False)
    resolved_input = _resolve(root, input_path) if input_path else None
    resolved_report = _resolve(root, report_path)
    events = load_events(resolved_input) if resolved_input else []
    ledger = build_correlation_ledger(events)
    payload = ledger.model_dump(mode="json")
    payload.update(
        {
            "input_path": str(resolved_input) if resolved_input else None,
            "input_read_performed": resolved_input is not None,
            "report_path": str(resolved_report),
            "write_requested": write_report,
            "write_performed": write_report,
            "writes_runtime": False,
            "writes_sqlite": False,
            "writes_parquet": False,
        }
    )
    if write_report:
        atomic_write_json(
            resolved_report,
            payload,
            policy=AtomicWritePolicy.restricted(
                (root / "data",),
                working_directory=root,
            ),
        )
    return payload


def blocked_report(
    *,
    reason: str,
    report_path: Path,
    write_requested: bool,
) -> dict[str, Any]:
    safety_flags = {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "live_trading_enabled": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "automatic_promotion_allowed": False,
        "publishes_active_signals": False,
        "writes_financial_ledger": False,
    }
    return {
        "schema_version": "runtime_integrity_traceability_v2",
        "status": "blocked",
        "reason": reason,
        "input_event_count": 0,
        "complete_chain_count": 0,
        "quarantine_count": 0,
        "ids_synthesized_count": 0,
        "records": [],
        "quarantine": [],
        "report_path": str(report_path),
        "write_requested": write_requested,
        "write_performed": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "safety_flags": safety_flags,
        **safety_flags,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.project_root).resolve(strict=False)
    report_path = _resolve(root, Path(args.report))
    try:
        report = build_report(
            project_root=root,
            input_path=Path(args.input) if args.input else None,
            report_path=report_path,
            write_report=bool(args.write_report),
        )
    except (
        AtomicWriteError,
        json.JSONDecodeError,
        OSError,
        ValidationError,
        ValueError,
    ) as exc:
        report = blocked_report(
            reason=f"correlation_ledger_build_failed:{type(exc).__name__}",
            report_path=report_path,
            write_requested=bool(args.write_report),
        )
    print(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.json
        else f"{report['status']}:{report['reason']}"
    )
    return 1 if report["status"] == "blocked" else 0


def _resolve(root: Path, path: Path) -> Path:
    return path.resolve(strict=False) if path.is_absolute() else (root / path).resolve(
        strict=False
    )


if __name__ == "__main__":
    raise SystemExit(main())
