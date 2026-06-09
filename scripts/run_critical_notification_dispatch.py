from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.critical_alerting import DEFAULT_ALERT_REPORT_PATH  # noqa: E402
from smartcrypto.ops.notification_channels import (  # noqa: E402
    dispatch_alert_report,
    load_alert_report,
    settings_from_env,
    write_dispatch_report,
)


DEFAULT_DISPATCH_REPORT_PATH = Path("data/reports/critical_notification_dispatch_report.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Envia alertas críticos do FUTUROS para ntfy e Telegram em modo paper/shadow only."
    )
    parser.add_argument("--alert-report", default=str(DEFAULT_ALERT_REPORT_PATH))
    parser.add_argument("--dispatch-report", default=str(DEFAULT_DISPATCH_REPORT_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-ok", action="store_true")
    parser.add_argument("--no-write-report", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    alert_report = load_alert_report(args.alert_report)
    result = dispatch_alert_report(
        alert_report,
        settings=settings_from_env(),
        dry_run=bool(args.dry_run),
        include_ok=bool(args.include_ok),
    )
    if not args.no_write_report:
        write_dispatch_report(args.dispatch_report, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
