from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.ops.trade_event_notifications import (
    DEFAULT_REPORT_PATH,
    DEFAULT_STATE_DB_PATH,
    VALID_CHANNEL_MODES,
    run_trade_event_notification_daemon,
    run_trade_event_notification_scan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dispatch NTFY/Telegram trade-event notifications for Freqtrade paper trades."
    )
    parser.add_argument(
        "--source-db",
        required=True,
        help="Read-only Freqtrade paper SQLite snapshot/database path.",
    )
    parser.add_argument(
        "--state-db",
        default=str(DEFAULT_STATE_DB_PATH),
        help="Runtime SQLite state path used for idempotency.",
    )
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT_PATH),
        help="Runtime JSON report output path.",
    )
    parser.add_argument(
        "--channels",
        choices=sorted(VALID_CHANNEL_MODES),
        default="telegram",
        help="Notification channel mode: telegram, ntfy, or all.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Do not send network requests; produce dry-run delivery results.",
    )
    parser.add_argument(
        "--send-real",
        action="store_true",
        default=False,
        help="Send real notifications through the selected channels. Still paper/shadow only and no orders.",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        default=False,
        help="Mark currently detected historical events as known without sending notifications.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        default=False,
        help="Run continuous polling loop over the paper DB.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=10.0,
        help="Polling interval for --daemon.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum daemon iterations. Intended for tests/controlled validation.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum pending events to process per scan.",
    )
    parser.add_argument(
        "--persist-dry-run",
        action="store_true",
        default=False,
        help="Persist dry-run events as processed. Use only for tests.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.dry_run and args.send_real:
        parser.error("--dry-run and --send-real are mutually exclusive")

    if args.baseline and args.daemon:
        parser.error("--baseline and --daemon are mutually exclusive")

    if args.persist_dry_run and args.send_real:
        parser.error("--persist-dry-run is only valid with dry-run/baseline flows")

    dry_run = not bool(args.send_real)

    if args.baseline:
        dry_run = True

    if args.daemon:
        report = run_trade_event_notification_daemon(
            source_db_path=Path(args.source_db),
            state_db_path=Path(args.state_db),
            report_path=Path(args.report),
            dry_run=dry_run,
            limit=args.limit,
            channels=args.channels,
            poll_seconds=args.poll_seconds,
            max_iterations=args.max_iterations,
        )
    else:
        report = run_trade_event_notification_scan(
            source_db_path=Path(args.source_db),
            state_db_path=Path(args.state_db),
            report_path=Path(args.report),
            dry_run=dry_run,
            limit=args.limit,
            persist_dry_run=bool(args.persist_dry_run),
            channels=args.channels,
            baseline=bool(args.baseline),
        )

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))

    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
