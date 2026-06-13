from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smartcrypto.ops.daily_evidence_pack import run_daily_evidence_pack  # noqa: E402


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the allowlisted SMART FUTUROS daily paper/shadow evidence pack.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--output-dir", default="data/reports")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--include-container-snapshot", action="store_true")
    parser.add_argument("--date", type=parse_date)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_daily_evidence_pack(
            project_root=args.project_root,
            output_dir=args.output_dir,
            no_write=bool(args.no_write),
            timeout_seconds=float(args.timeout_seconds),
            include_container_snapshot=bool(args.include_container_snapshot),
            pack_date=args.date,
        )
    except ValueError as exc:
        report = {
            "status": "blocked",
            "reason": str(exc),
            "paper_only": True,
            "shadow_only": True,
            "sends_orders": False,
            "changes_risk": False,
            "exchange_private_access": False,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "canary_release_allowed": False,
            "live_release_allowed": False,
            "write_performed": False,
        }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{report['status']}: {report['reason']}")
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
