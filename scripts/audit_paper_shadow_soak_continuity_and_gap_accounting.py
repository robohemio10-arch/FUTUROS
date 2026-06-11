from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.paper_shadow_soak_gap_accounting import (  # noqa: E402
    DEFAULT_OUTPUT_PATH,
    audit_paper_shadow_soak_continuity_and_gap_accounting,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit SMART FUTUROS paper/shadow soak continuity and gap accounting."
    )
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="Output JSON path under project root.")
    parser.add_argument("--diagnostic-soak-days", type=int, default=7, help="Diagnostic-only soak threshold.")
    parser.add_argument("--required-soak-days", type=int, default=30, help="Readiness soak threshold.")
    parser.add_argument("--max-warning-gap-minutes", type=int, default=60, help="Warning gap threshold.")
    parser.add_argument("--max-critical-gap-minutes", type=int, default=360, help="Critical gap threshold.")
    parser.add_argument("--write", action="store_true", help="Materialize the report to the output path.")
    parser.add_argument("--json", action="store_true", help="Emit the full JSON report to stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = audit_paper_shadow_soak_continuity_and_gap_accounting(
        project_root=args.project_root,
        output=args.output,
        diagnostic_soak_days=args.diagnostic_soak_days,
        required_soak_days=args.required_soak_days,
        max_warning_gap_minutes=args.max_warning_gap_minutes,
        max_critical_gap_minutes=args.max_critical_gap_minutes,
        write=args.write,
    )
    payload = result.report if args.json else compact_summary(result.report)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def compact_summary(report: dict[str, object]) -> dict[str, object]:
    keys = (
        "schema_version",
        "status",
        "reason",
        "observed_calendar_days",
        "continuous_valid_soak_days",
        "critical_gap_count",
        "warning_gap_count",
        "seven_day_diagnostic_status",
        "thirty_day_readiness_status",
        "canary_release_allowed",
        "live_release_allowed",
        "write_performed",
    )
    return {key: report.get(key) for key in keys}


if __name__ == "__main__":
    raise SystemExit(main())
