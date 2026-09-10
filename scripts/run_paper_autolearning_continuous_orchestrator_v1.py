from __future__ import annotations

import argparse
import json
import signal
import threading
from pathlib import Path
from typing import Any

from smartcrypto.learning.paper_autolearning.continuous_orchestrator import (
    DEFAULT_UNATTENDED_INTERVAL_SECONDS,
    run_paper_autolearning_continuous_orchestrator_v1,
    run_paper_autolearning_unattended_service_v2,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the idempotent Paper auto-learning feedback-to-quarantine cycle "
            "once or as an unattended service."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--paper-db", default=None)
    parser.add_argument("--write-feedback", action="store_true")
    parser.add_argument("--train-challenger", action="store_true")
    parser.add_argument("--write-quarantine-artifacts", action="store_true")
    parser.add_argument("--write-reports", action="store_true")
    parser.add_argument("--no-evaluate-candidates", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=DEFAULT_UNATTENDED_INTERVAL_SECONDS,
        help="Polling interval for --daemon. Must be > 0.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Optional bounded daemon run for smoke validation; omitted means continuous.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds must be > 0")
    if args.max_cycles is not None and args.max_cycles <= 0:
        parser.error("--max-cycles must be > 0")
    if args.max_cycles is not None and not args.daemon:
        parser.error("--max-cycles requires --daemon")
    return args


def _print_cycle(report: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str), flush=True)
        return
    print(
        "cycle="
        f"{report.get('cycle_index')} "
        f"status={report.get('status')} "
        f"reason={report.get('reason')} "
        f"new_outcomes={report.get('new_outcome_event_count', 0)} "
        f"microbatch_rows={report.get('microbatch_rows', 0)} "
        f"training_performed={report.get('training_performed', False)} "
        f"candidates={report.get('quarantine_candidate_count', 0)}",
        flush=True,
    )


def _install_stop_handlers(stop_event: threading.Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        value = getattr(signal, signal_name, None)
        if value is not None:
            signal.signal(value, request_stop)


def _run_once(args: argparse.Namespace) -> int:
    report = run_paper_autolearning_continuous_orchestrator_v1(
        project_root=Path(args.project_root),
        explicit_paper_db_path=args.paper_db,
        write_feedback=args.write_feedback,
        train_challenger=args.train_challenger,
        write_quarantine_artifacts=args.write_quarantine_artifacts,
        write_reports=args.write_reports,
        evaluate_candidates=not args.no_evaluate_candidates,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    else:
        print(
            f"status={report['status']} reason={report['reason']} "
            f"new_outcomes={report['new_outcome_event_count']} "
            f"microbatch_rows={report['microbatch_rows']} "
            f"training_performed={report['training_performed']} "
            f"quarantine_candidates={report['quarantine_candidate_count']}"
        )
    return 1 if report["status"] == "blocked" else 0


def _run_daemon(args: argparse.Namespace) -> int:
    stop_event = threading.Event()
    _install_stop_handlers(stop_event)

    summary = run_paper_autolearning_unattended_service_v2(
        project_root=Path(args.project_root),
        explicit_paper_db_path=args.paper_db,
        interval_seconds=args.interval_seconds,
        write_feedback=args.write_feedback,
        train_challenger=args.train_challenger,
        write_quarantine_artifacts=args.write_quarantine_artifacts,
        write_reports=args.write_reports,
        evaluate_candidates=not args.no_evaluate_candidates,
        max_cycles=args.max_cycles,
        stop_requested=stop_event.is_set,
        cycle_observer=lambda report: _print_cycle(report, json_output=args.json),
        sleep_fn=stop_event.wait,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str), flush=True)
    else:
        print(
            f"service_status={summary['status']} reason={summary['reason']} "
            f"cycles={summary['cycles_executed']} "
            f"new_outcomes={summary['new_outcome_event_count']} "
            f"training_cycles={summary['training_cycle_count']} "
            f"candidates={summary['candidate_count']}",
            flush=True,
        )
    return 1 if summary["status"] == "blocked" else 0


def main() -> int:
    args = parse_args()
    return _run_daemon(args) if args.daemon else _run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
