from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.dashboard.ai_governance_panel import (  # noqa: E402
    DEFAULT_ANTI_LEAKAGE_REPORT_PATH,
    DEFAULT_BACKTEST_REPORT_PATH,
    DEFAULT_DATA_QUALITY_REPORT_PATH,
    DEFAULT_DATASET_MANIFEST_PATH,
    DEFAULT_DECISIONS_JSONL_PATH,
    DEFAULT_DRIFT_REPORT_PATH,
    DEFAULT_FINANCIAL_REPORT_PATH,
    DEFAULT_MONTE_CARLO_RISK_BUDGET_POLICY_REPORT_PATH,
    DEFAULT_MONTE_CARLO_REPORT_PATH,
    DEFAULT_OUTCOMES_REPORT_PATH,
    DEFAULT_PROMOTION_REPORT_PATH,
    DEFAULT_REGISTRY_PATH,
    DEFAULT_TRAINER_REPORT_PATH,
    load_ai_governance_panel_state,
)

DEFAULT_REPORT_PATH = Path("data/reports/ai_governance_dashboard_sources_report.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect read-only AI governance dashboard sources."
    )
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH))
    parser.add_argument("--trainer-report", default=str(DEFAULT_TRAINER_REPORT_PATH))
    parser.add_argument("--promotion-report", default=str(DEFAULT_PROMOTION_REPORT_PATH))
    parser.add_argument("--drift-report", default=str(DEFAULT_DRIFT_REPORT_PATH))
    parser.add_argument("--outcomes-report", default=str(DEFAULT_OUTCOMES_REPORT_PATH))
    parser.add_argument("--financial-report", default=str(DEFAULT_FINANCIAL_REPORT_PATH))
    parser.add_argument("--anti-leakage-report", default=str(DEFAULT_ANTI_LEAKAGE_REPORT_PATH))
    parser.add_argument("--monte-carlo-report", default=str(DEFAULT_MONTE_CARLO_REPORT_PATH))
    parser.add_argument("--monte-carlo-risk-budget-policy-report", default=str(DEFAULT_MONTE_CARLO_RISK_BUDGET_POLICY_REPORT_PATH))
    parser.add_argument("--backtest-report", default=str(DEFAULT_BACKTEST_REPORT_PATH))
    parser.add_argument("--data-quality-report", default=str(DEFAULT_DATA_QUALITY_REPORT_PATH))
    parser.add_argument("--dataset-manifest", default=str(DEFAULT_DATASET_MANIFEST_PATH))
    parser.add_argument("--decisions-jsonl", default=str(DEFAULT_DECISIONS_JSONL_PATH))
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    state = load_ai_governance_panel_state(
        source_paths={
            "registry": args.registry,
            "trainer_report": args.trainer_report,
            "promotion_report": args.promotion_report,
            "drift_report": args.drift_report,
            "outcomes_report": args.outcomes_report,
            "financial_report": args.financial_report,
            "anti_leakage_report": args.anti_leakage_report,
            "monte_carlo_report": args.monte_carlo_report,
            "monte_carlo_risk_budget_policy_report": args.monte_carlo_risk_budget_policy_report,
            "backtest_report": args.backtest_report,
            "data_quality_report": args.data_quality_report,
            "dataset_manifest": args.dataset_manifest,
            "decisions_jsonl": args.decisions_jsonl,
        },
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
