from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.dashboard.risk_readiness_soak_panel import (  # noqa: E402
    DEFAULT_ACTIVE_SIGNALS_PATH,
    DEFAULT_AI_GOVERNANCE_REPORT_PATH,
    DEFAULT_ANTI_LEAKAGE_REPORT_PATH,
    DEFAULT_BACKTEST_REPORT_PATH,
    DEFAULT_DATA_QUALITY_REPORT_PATH,
    DEFAULT_DATASET_MANIFEST_PATH,
    DEFAULT_KILL_SWITCH_PATH,
    DEFAULT_MONTE_CARLO_REPORT_PATH,
    DEFAULT_PAPER_SESSION_REPORT_PATH,
    DEFAULT_PAPER_SOAK_REPORT_PATH,
    DEFAULT_SIGNAL_DECISIONS_PATH,
    load_risk_readiness_soak_state,
)

DEFAULT_REPORT_PATH = Path("data/reports/risk_readiness_soak_dashboard_sources_report.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect read-only risk readiness and paper/shadow soak sources.")
    parser.add_argument("--paper-soak-report", default=str(DEFAULT_PAPER_SOAK_REPORT_PATH))
    parser.add_argument("--paper-session-report", default=str(DEFAULT_PAPER_SESSION_REPORT_PATH))
    parser.add_argument("--ai-governance-report", default=str(DEFAULT_AI_GOVERNANCE_REPORT_PATH))
    parser.add_argument("--data-quality-report", default=str(DEFAULT_DATA_QUALITY_REPORT_PATH))
    parser.add_argument("--dataset-manifest", default=str(DEFAULT_DATASET_MANIFEST_PATH))
    parser.add_argument("--anti-leakage-report", default=str(DEFAULT_ANTI_LEAKAGE_REPORT_PATH))
    parser.add_argument("--monte-carlo-report", default=str(DEFAULT_MONTE_CARLO_REPORT_PATH))
    parser.add_argument("--backtest-report", default=str(DEFAULT_BACKTEST_REPORT_PATH))
    parser.add_argument("--kill-switch", default=str(DEFAULT_KILL_SWITCH_PATH))
    parser.add_argument("--active-signals", default=str(DEFAULT_ACTIVE_SIGNALS_PATH))
    parser.add_argument("--signal-decisions", default=str(DEFAULT_SIGNAL_DECISIONS_PATH))
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--required-paper-days", type=int, default=7)
    parser.add_argument("--max-stale-signal-age-seconds", type=int, default=900)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    state = load_risk_readiness_soak_state(
        source_paths={
            "paper_soak_report": args.paper_soak_report,
            "paper_session_report": args.paper_session_report,
            "ai_governance_report": args.ai_governance_report,
            "data_quality_report": args.data_quality_report,
            "dataset_manifest": args.dataset_manifest,
            "anti_leakage_report": args.anti_leakage_report,
            "monte_carlo_report": args.monte_carlo_report,
            "backtest_report": args.backtest_report,
            "kill_switch": args.kill_switch,
            "active_signals": args.active_signals,
            "signal_decisions": args.signal_decisions,
        },
        required_paper_days=args.required_paper_days,
        max_stale_signal_age_seconds=args.max_stale_signal_age_seconds,
        strict=args.strict,
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(state, ensure_ascii=False, sort_keys=True))
    return 1 if state.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
