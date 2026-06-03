from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.critical_alerting import (  # noqa: E402
    DEFAULT_ALERT_REPORT_PATH,
    build_critical_alerting_report,
)
from smartcrypto.ops.financial_event_log import (  # noqa: E402
    DEFAULT_EVENT_LOG_PATH,
    FinancialEvent,
    FinancialEventLogError,
    read_event_log,
    summarize_events,
    validate_event,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida o financial event log e gera alerting report read-only."
    )
    parser.add_argument("--event-log", default=str(DEFAULT_EVENT_LOG_PATH))
    parser.add_argument("--alert-report", default=str(DEFAULT_ALERT_REPORT_PATH))
    parser.add_argument("--max-risk-rejections", type=int, default=5)
    parser.add_argument("--max-prediction-stale", type=int, default=3)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    event_log = Path(args.event_log)
    validation_errors = validate_log_file(event_log, strict=args.strict)
    report = build_critical_alerting_report(
        event_log_path=event_log,
        report_path=args.alert_report,
        max_risk_rejections=args.max_risk_rejections,
        max_prediction_stale=args.max_prediction_stale,
        strict=args.strict,
    )
    if validation_errors:
        report["status"] = "blocked"
        report["reason"] = ";".join(sorted(set(validation_errors)))
        report["blocked_findings"] = sorted(set([*report.get("blocked_findings", []), *validation_errors]))
        Path(args.alert_report).write_text(
            json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    if event_log.exists():
        report["summary"] = summarize_events(read_event_log(event_log))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") == "blocked" else 0


def validate_log_file(path: Path, *, strict: bool) -> list[str]:
    if not path.exists():
        return ["missing_event_log"] if strict else []
    errors: list[str] = []
    for index, row in enumerate(read_event_log(path), start=1):
        try:
            validate_event(FinancialEvent(**row))
        except (FinancialEventLogError, TypeError) as exc:
            errors.append(f"invalid_event_line:{index}:{exc}")
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
