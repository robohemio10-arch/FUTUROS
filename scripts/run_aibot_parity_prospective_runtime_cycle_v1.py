#!/usr/bin/env python3
"""Run one locked AIBOT prospective runtime-foundation cycle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT_TEXT = str(_PROJECT_ROOT)
if _PROJECT_ROOT_TEXT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_TEXT)

from smartcrypto.research.aibot_parity_paper_ab_prospective_collector.runtime_foundation import (  # noqa: E402
    DEFAULT_PREREGISTRATION,
    DEFAULT_RUNTIME_CONFIG,
    RUNTIME_SAFETY_FLAGS,
    run_runtime_foundation_cycle,
)
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import AtomicWriteError  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute one paper/shadow prospective collection cycle. This command does not "
            "register a scheduler, activate Treatment, or start the collection clock."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--aibot-snapshot-json", required=True)
    parser.add_argument("--decision-ledger-jsonl", required=True)
    parser.add_argument("--closed-trades-path", required=True)
    parser.add_argument("--allow-paper-runtime-read", action="store_true")
    parser.add_argument("--write-evidence", action="store_true")
    parser.add_argument("--write-heartbeat", action="store_true")
    parser.add_argument("--runtime-foundation-config", default=str(DEFAULT_RUNTIME_CONFIG))
    parser.add_argument("--preregistration", default=str(DEFAULT_PREREGISTRATION))
    parser.add_argument("--json", action="store_true")
    return parser


def _blocked(reason: str, error_type: str) -> dict[str, object]:
    return {
        "schema_version": "aibot_parity_prospective_runtime_foundation_v1",
        "status": "blocked",
        "reason": reason,
        "error_type": error_type,
        "recurring_runner_available": True,
        "runner_restart_safe": True,
        "runner_lock_safe": True,
        "runner_idempotent": True,
        "heartbeat_available": True,
        "healthcheck_available": True,
        "collection_clock_started": False,
        "prospective_collection_running_proven": False,
        "paper_treatment_release_allowed": False,
        "safety_flags": dict(RUNTIME_SAFETY_FLAGS),
        **RUNTIME_SAFETY_FLAGS,
    }


def main() -> int:
    args = _parser().parse_args()
    try:
        result = run_runtime_foundation_cycle(
            project_root=args.project_root,
            aibot_snapshot_path=args.aibot_snapshot_json,
            decision_ledger_path=args.decision_ledger_jsonl,
            closed_trades_path=args.closed_trades_path,
            allow_paper_runtime_read=bool(args.allow_paper_runtime_read),
            write_evidence=bool(args.write_evidence),
            write_heartbeat=bool(args.write_heartbeat),
            runtime_config_path=args.runtime_foundation_config,
            preregistration_path=args.preregistration,
        )
        report = result.report
    except (OSError, ValueError, TypeError, json.JSONDecodeError, AtomicWriteError) as exc:
        reason = str(exc).splitlines()[0].strip()[:300] or type(exc).__name__
        report = _blocked(reason, type(exc).__name__)
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False))
    else:
        print(
            f"status={report.get('status')} reason={report.get('reason')} "
            f"collection_clock_started={report.get('collection_clock_started')}"
        )
    health = report.get("health") if isinstance(report, dict) else None
    health_ok = not isinstance(health, dict) or health.get("status") == "ok"
    return 0 if report.get("status") == "ok" and health_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
