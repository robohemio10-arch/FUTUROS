from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.export_freqtrade_paper_db_snapshot import (  # noqa: E402
    DEFAULT_OUTPUT as DEFAULT_SNAPSHOT_OUTPUT,
    DEFAULT_REPORT as DEFAULT_SNAPSHOT_REPORT,
    export_local_sqlite_snapshot,
    write_json,
)
from smartcrypto.data.paper_trade_lifecycle import (  # noqa: E402
    PaperFeedbackConfig,
    collect_closed_feedback,
    collect_summary,
    inspect_open_positions,
    inspect_outputs,
)


DEFAULT_OPERATIONAL_DB = Path("/paper-db/tradesv3.paper.sqlite")
DEFAULT_REPORT = Path("data/reports/phase14_runtime_feedback_sync_report.json")
DEFAULT_INTERVAL_SECONDS = 120


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safety_payload() -> dict[str, Any]:
    return {
        "runtime_mode": "paper",
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }


def phase_status(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    return payload.get("status")


def resolve_runtime_status(
    *,
    snapshot_report: dict[str, Any],
    open_report: dict[str, Any],
    closed_report: dict[str, Any],
) -> tuple[str, str | None]:
    if phase_status(snapshot_report) != "ok":
        return "blocked", "snapshot_export_failed"
    if phase_status(open_report) == "blocked":
        return "blocked", "open_positions_blocked"
    if phase_status(closed_report) == "blocked":
        return "blocked", "closed_feedback_blocked"
    return "ok", None


def run_feedback_sync_once(
    *,
    source_db: Path = DEFAULT_OPERATIONAL_DB,
    snapshot_output: Path = DEFAULT_SNAPSHOT_OUTPUT,
    snapshot_report_path: Path = DEFAULT_SNAPSHOT_REPORT,
    report_path: Path = DEFAULT_REPORT,
    config: PaperFeedbackConfig | None = None,
    snapshot_exporter: Callable[[Path, Path], dict[str, Any]] = export_local_sqlite_snapshot,
) -> dict[str, Any]:
    snapshot_report = snapshot_exporter(Path(source_db), Path(snapshot_output))
    write_json(Path(snapshot_report_path), snapshot_report)

    open_report: dict[str, Any] = {}
    closed_report: dict[str, Any] = {}
    output_report: dict[str, Any] = {}
    summary_report: dict[str, Any] = {}

    if snapshot_report.get("status") == "ok":
        open_report = inspect_open_positions(config)
        closed_report = collect_closed_feedback(config)
        output_report = inspect_outputs(config)
        summary_report = collect_summary(config)

    status, reason = resolve_runtime_status(
        snapshot_report=snapshot_report,
        open_report=open_report,
        closed_report=closed_report,
    )
    report = {
        "status": status,
        "reason": reason,
        "source_db": str(source_db),
        "source_db_read_only": True,
        "snapshot_output": str(snapshot_output),
        "snapshot_status": snapshot_report.get("status"),
        "snapshot_reason": snapshot_report.get("reason"),
        "snapshot_report_path": str(snapshot_report_path),
        "open_positions_status": open_report.get("status"),
        "open_rows": open_report.get("open_rows"),
        "closed_feedback_status": closed_report.get("status"),
        "closed_rows": closed_report.get("closed_rows"),
        "raw_rows": closed_report.get("raw_rows"),
        "output_summary_status": (output_report.get("phase14_status") or {}).get("status"),
        "phase14_summary_status": summary_report.get("status"),
        "reports_generated": {
            "snapshot_export": str(snapshot_report_path),
            "open_positions": str(config.open_positions_report) if config else "data/reports/phase14_open_positions_report.json",
            "closed_feedback": str(config.closed_feedback_report) if config else "data/reports/phase14_closed_feedback_report.json",
            "output_summary": str(config.output_summary) if config else "data/reports/phase14_output_summary.json",
            "summary": str(config.summary) if config else "data/reports/phase14_summary.json",
            "runtime_sync": str(report_path),
        },
        "dashboard_inputs_refreshed": status == "ok",
        "created_at": utc_now(),
        **safety_payload(),
    }
    write_json(Path(report_path), report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 14 paper runtime feedback sync service.")
    parser.add_argument("--source-db", default=str(DEFAULT_OPERATIONAL_DB))
    parser.add_argument("--snapshot-output", default=str(DEFAULT_SNAPSHOT_OUTPUT))
    parser.add_argument("--snapshot-report", default=str(DEFAULT_SNAPSHOT_REPORT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--interval-seconds", type=int, default=0)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    interval = int(args.interval_seconds)
    once = bool(args.once or interval <= 0)

    while True:
        report = run_feedback_sync_once(
            source_db=Path(args.source_db),
            snapshot_output=Path(args.snapshot_output),
            snapshot_report_path=Path(args.snapshot_report),
            report_path=Path(args.report),
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
        if once:
            return 0 if report["status"] == "ok" else 1
        time.sleep(max(interval, 1))


if __name__ == "__main__":
    raise SystemExit(main())
