"""Build sanitized interruption-quarantine evidence in memory only."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any, cast

from pydantic import ValidationError

from smartcrypto.execution.decision_ledger_paper_runtime_writer_v1 import (
    InterruptionStage,
    build_runtime_interruption_quarantine,
)

STAGES = (
    "preflight",
    "lock_acquisition",
    "append",
    "file_fsync",
    "health_update",
    "parent_directory_fsync",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build runtime_interruption_quarantine_v1_1 without persistence."
    )
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--interrupted-at-utc", required=True)
    parser.add_argument("--interruption-stage", required=True, choices=STAGES)
    parser.add_argument("--error-type", required=True)
    parser.add_argument("--error-message-sha256", required=True)
    parser.add_argument("--payload-sha256")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        quarantine = build_runtime_interruption_quarantine(
            event_id=args.event_id,
            interrupted_at_utc=_parse_datetime(args.interrupted_at_utc),
            interruption_stage=cast(InterruptionStage, args.interruption_stage),
            error_type=args.error_type,
            error_message_sha256=args.error_message_sha256,
            payload_sha256=args.payload_sha256,
        )
        report: dict[str, Any] = {
            "status": "ok",
            "reason": "quarantine_built_in_memory",
            "decision": "QUARANTINE_REQUIRES_OPERATOR_REVIEW",
            "quarantine": quarantine.model_dump(mode="json"),
            "write_performed": False,
            "writes_runtime": False,
            "writes_sqlite": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
        }
    except (ValueError, ValidationError) as exc:
        report = {
            "status": "blocked",
            "reason": f"invalid_quarantine_evidence:{type(exc).__name__}",
            "decision": "BLOCK_INVALID_QUARANTINE_EVIDENCE",
            "write_performed": False,
            "writes_runtime": False,
            "writes_sqlite": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
        }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}:{report['reason']}")
    return 1 if report["status"] == "blocked" else 0


def _parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)


if __name__ == "__main__":
    raise SystemExit(main())
