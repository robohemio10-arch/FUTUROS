from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.paper_shadow_soak_report import (  # noqa: E402
    DEFAULT_AI_GOVERNANCE_REPORT,
    DEFAULT_ANTI_LEAKAGE_REPORT,
    DEFAULT_CRITICAL_ALERTING_REPORT,
    DEFAULT_DATASET_MANIFEST,
    DEFAULT_DATA_QUALITY_REPORT,
    DEFAULT_DRIFT_REPORT,
    DEFAULT_EVENT_BACKTEST_REPORT,
    DEFAULT_FINANCIAL_EVENT_LOG,
    DEFAULT_FINANCIAL_THRESHOLD_REPORT,
    DEFAULT_LEDGER_REPORT,
    DEFAULT_MARKET_HEALTH_REPORT,
    DEFAULT_MONTE_CARLO_REPORT,
    DEFAULT_REPORT_PATH,
    DEFAULT_RISK_READINESS_REPORT,
    DEFAULT_RISK_RECOVERY_REPORT,
    DEFAULT_STATE_RECONCILIATION_REPORT,
    build_paper_shadow_soak_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the paper/shadow soak evidence report.")
    parser.add_argument("--financial-event-log", default=str(DEFAULT_FINANCIAL_EVENT_LOG))
    parser.add_argument("--critical-alerting-report", default=str(DEFAULT_CRITICAL_ALERTING_REPORT))
    parser.add_argument("--risk-recovery-report", default=str(DEFAULT_RISK_RECOVERY_REPORT))
    parser.add_argument("--market-health-report", default=str(DEFAULT_MARKET_HEALTH_REPORT))
    parser.add_argument("--state-reconciliation-report", default=str(DEFAULT_STATE_RECONCILIATION_REPORT))
    parser.add_argument("--ledger-report", default=str(DEFAULT_LEDGER_REPORT))
    parser.add_argument("--ai-governance-report", default=str(DEFAULT_AI_GOVERNANCE_REPORT))
    parser.add_argument("--risk-readiness-report", default=str(DEFAULT_RISK_READINESS_REPORT))
    parser.add_argument("--drift-report", default=str(DEFAULT_DRIFT_REPORT))
    parser.add_argument("--financial-threshold-report", default=str(DEFAULT_FINANCIAL_THRESHOLD_REPORT))
    parser.add_argument("--anti-leakage-report", default=str(DEFAULT_ANTI_LEAKAGE_REPORT))
    parser.add_argument("--monte-carlo-report", default=str(DEFAULT_MONTE_CARLO_REPORT))
    parser.add_argument("--event-backtest-report", default=str(DEFAULT_EVENT_BACKTEST_REPORT))
    parser.add_argument("--data-quality-report", default=str(DEFAULT_DATA_QUALITY_REPORT))
    parser.add_argument("--dataset-manifest", default=str(DEFAULT_DATASET_MANIFEST))
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--required-soak-days", type=int, default=7)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_paper_shadow_soak_report(
        financial_event_log=args.financial_event_log,
        critical_alerting_report=args.critical_alerting_report,
        risk_recovery_report=args.risk_recovery_report,
        market_health_report=args.market_health_report,
        state_reconciliation_report=args.state_reconciliation_report,
        ledger_report=args.ledger_report,
        ai_governance_report=args.ai_governance_report,
        risk_readiness_report=args.risk_readiness_report,
        drift_report=args.drift_report,
        financial_threshold_report=args.financial_threshold_report,
        anti_leakage_report=args.anti_leakage_report,
        monte_carlo_report=args.monte_carlo_report,
        event_backtest_report=args.event_backtest_report,
        data_quality_report=args.data_quality_report,
        dataset_manifest=args.dataset_manifest,
        report_path=args.report,
        required_soak_days=args.required_soak_days,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") in {"blocked", "insufficient_soak"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
