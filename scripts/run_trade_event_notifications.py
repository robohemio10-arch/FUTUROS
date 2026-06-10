from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.ops.trade_event_notifications import (
    DEFAULT_REPORT_PATH,
    DEFAULT_STATE_DB_PATH,
    run_trade_event_notification_scan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dispatch Telegram trade-event notifications for Freqtrade paper trades."
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
        "--dry-run",
        action="store_true",
        default=False,
        help="Do not send Telegram request; produce dry-run delivery results.",
    )
    parser.add_argument(
        "--send-real",
        action="store_true",
        default=False,
        help="Send real Telegram messages. Still paper/shadow only and no orders.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum pending events to process.",
    )
    parser.add_argument(
        "--persist-dry-run",
        action="store_true",
        default=False,
        help="Persist dry-run events as sent. Use only for tests.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.dry_run and args.send_real:
        parser.error("--dry-run and --send-real are mutually exclusive")

    dry_run = not bool(args.send_real)

    report = run_trade_event_notification_scan(
        source_db_path=Path(args.source_db),
        state_db_path=Path(args.state_db),
        report_path=Path(args.report),
        dry_run=dry_run,
        limit=args.limit,
        persist_dry_run=bool(args.persist_dry_run),
    )

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))

    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
