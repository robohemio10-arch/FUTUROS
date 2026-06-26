#!/usr/bin/env python
"""Build the read-only paper Freqtrade AI selector observability report."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.freqtrade_paper_ai_selector_integration import (
    FreqtradePaperAISelectorConfig,
    resolve_paths,
    run_freqtrade_paper_ai_selector_integration,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Observe the paper Freqtrade AI selector surface without changing "
            "strategy, risk, signals, models, or orders."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--freqtrade-config")
    parser.add_argument("--freqtrade-strategy")
    parser.add_argument("--training-summary")
    parser.add_argument("--executive-pack")
    parser.add_argument("--shadow-candidate-report")
    parser.add_argument("--feedback-loop-report")
    parser.add_argument("--report-output")
    parser.add_argument("--observations-output")
    parser.add_argument("--strict", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Write runtime outputs.")
    mode.add_argument(
        "--no-write",
        action="store_true",
        help="Evaluate in memory without writing (default).",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = resolve_paths(
        args.project_root,
        freqtrade_config=args.freqtrade_config,
        freqtrade_strategy=args.freqtrade_strategy,
        training_summary=args.training_summary,
        executive_pack=args.executive_pack,
        shadow_candidate_report=args.shadow_candidate_report,
        feedback_loop_report=args.feedback_loop_report,
        report_output=args.report_output,
        observations_output=args.observations_output,
    )
    try:
        result = run_freqtrade_paper_ai_selector_integration(
            paths,
            FreqtradePaperAISelectorConfig(strict=bool(args.strict)),
            write=bool(args.write),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        payload = {
            "status": "blocked",
            "reason": "invalid_selector_integration_structure",
            "error_type": type(exc).__name__,
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "sends_orders": False,
            "changes_risk": False,
            "updates_freqtrade": False,
            "updates_qlib_runtime": False,
            "updates_risk_manager": False,
            "updates_ai_shadow_runtime": False,
            "selector_authority": "none",
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 1
    encoded = json.dumps(
        result.report,
        ensure_ascii=False,
        indent=None if args.json else 2,
        sort_keys=True,
        allow_nan=False,
    )
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
