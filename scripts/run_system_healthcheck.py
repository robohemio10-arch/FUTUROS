from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.system_healthcheck import (  # noqa: E402
    DEFAULT_BACKUP_REPORT,
    DEFAULT_COMPOSE_FILE,
    DEFAULT_CRITICAL_ALERTING_REPORT,
    DEFAULT_DOCKERFILE,
    DEFAULT_LEDGER_REPORT,
    DEFAULT_MARKET_HEALTH_REPORT,
    DEFAULT_PAPER_SOAK_REPORT,
    DEFAULT_READINESS_REPORT,
    DEFAULT_REPORT_PATH,
    DEFAULT_RESTORE_REPORT,
    DEFAULT_RISK_RECOVERY_REPORT,
    DEFAULT_STATE_RECONCILIATION_REPORT,
    run_system_healthcheck,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Docker/paper/shadow system healthcheck.")
    parser.add_argument("--readiness-report", default=str(DEFAULT_READINESS_REPORT))
    parser.add_argument("--paper-soak-report", default=str(DEFAULT_PAPER_SOAK_REPORT))
    parser.add_argument("--critical-alerting-report", default=str(DEFAULT_CRITICAL_ALERTING_REPORT))
    parser.add_argument("--risk-recovery-report", default=str(DEFAULT_RISK_RECOVERY_REPORT))
    parser.add_argument("--market-health-report", default=str(DEFAULT_MARKET_HEALTH_REPORT))
    parser.add_argument("--state-reconciliation-report", default=str(DEFAULT_STATE_RECONCILIATION_REPORT))
    parser.add_argument("--ledger-report", default=str(DEFAULT_LEDGER_REPORT))
    parser.add_argument("--backup-report", default=str(DEFAULT_BACKUP_REPORT))
    parser.add_argument("--restore-report", default=str(DEFAULT_RESTORE_REPORT))
    parser.add_argument("--dockerfile", default=str(DEFAULT_DOCKERFILE))
    parser.add_argument("--compose-file", default=str(DEFAULT_COMPOSE_FILE))
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--max-report-age-seconds", type=int, default=900)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_system_healthcheck(
        readiness_report=args.readiness_report,
        paper_soak_report=args.paper_soak_report,
        critical_alerting_report=args.critical_alerting_report,
        risk_recovery_report=args.risk_recovery_report,
        market_health_report=args.market_health_report,
        state_reconciliation_report=args.state_reconciliation_report,
        ledger_report=args.ledger_report,
        backup_report=args.backup_report,
        restore_report=args.restore_report,
        dockerfile=args.dockerfile,
        compose_file=args.compose_file,
        report_path=args.report,
        max_report_age_seconds=args.max_report_age_seconds,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
