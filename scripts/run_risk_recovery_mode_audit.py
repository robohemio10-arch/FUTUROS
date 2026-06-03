from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.risk.risk_recovery_modes import (  # noqa: E402
    DEFAULT_REPORT_PATH,
    RiskRecoveryLimits,
    run_risk_recovery_mode_audit,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita modos de recuperação/drawdown do RiskManager em modo paper/shadow read-only."
    )
    parser.add_argument("--equity-curve")
    parser.add_argument("--closed-trades")
    parser.add_argument("--paper-session-report")
    parser.add_argument("--market-health-report")
    parser.add_argument("--readiness-report")
    parser.add_argument("--monte-carlo-report")
    parser.add_argument("--backtest-report")
    parser.add_argument("--kill-switch")
    parser.add_argument("--incidents")
    parser.add_argument("--state-divergence-report")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--max-daily-loss-pct", type=float, default=3.0)
    parser.add_argument("--max-weekly-loss-pct", type=float, default=7.0)
    parser.add_argument("--max-drawdown-pct", type=float, default=10.0)
    parser.add_argument("--max-consecutive-losses", type=int, default=4)
    parser.add_argument("--required-clean-streak-days", type=int, default=3)
    parser.add_argument("--previous-mode", default="NORMAL")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_risk_recovery_mode_audit(
        equity_curve_path=args.equity_curve,
        closed_trades_path=args.closed_trades,
        paper_session_report_path=args.paper_session_report,
        market_health_report_path=args.market_health_report,
        readiness_report_path=args.readiness_report,
        monte_carlo_report_path=args.monte_carlo_report,
        backtest_report_path=args.backtest_report,
        kill_switch_path=args.kill_switch,
        incidents_path=args.incidents,
        state_divergence_report_path=args.state_divergence_report,
        report_path=args.report,
        limits=RiskRecoveryLimits(
            max_daily_loss_pct=args.max_daily_loss_pct,
            max_weekly_loss_pct=args.max_weekly_loss_pct,
            max_drawdown_pct=args.max_drawdown_pct,
            max_consecutive_losses=args.max_consecutive_losses,
            required_clean_streak_days=args.required_clean_streak_days,
        ),
        previous_mode=args.previous_mode,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
