#!/usr/bin/env python
"""Build the offline, record-only AI Shadow feedback evidence loop."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from smartcrypto.research.ai_shadow_online_feedback_learning_loop import (
    AIShadowFeedbackLoopConfig,
    resolve_paths,
    run_ai_shadow_online_feedback_learning_loop,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate existing AI Shadow research evidence without training or "
            "changing runtime state."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--training-summary")
    parser.add_argument("--executive-pack")
    parser.add_argument("--shadow-candidate-report")
    parser.add_argument("--shadow-candidate-registry")
    parser.add_argument("--outcome-attribution-report")
    parser.add_argument("--financial-threshold-report")
    parser.add_argument("--threshold-readiness-report")
    parser.add_argument("--drift-monitor-report")
    parser.add_argument("--decision-logger-report")
    parser.add_argument("--outcome-tracker-report")
    parser.add_argument("--incremental-trainer-report")
    parser.add_argument("--report-output")
    parser.add_argument("--events-output")
    parser.add_argument("--strict", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Write runtime report and events.")
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
        training_summary=args.training_summary,
        executive_pack=args.executive_pack,
        shadow_candidate_report=args.shadow_candidate_report,
        shadow_candidate_registry=args.shadow_candidate_registry,
        outcome_attribution_report=args.outcome_attribution_report,
        financial_threshold_report=args.financial_threshold_report,
        threshold_readiness_report=args.threshold_readiness_report,
        drift_monitor_report=args.drift_monitor_report,
        decision_logger_report=args.decision_logger_report,
        outcome_tracker_report=args.outcome_tracker_report,
        incremental_trainer_report=args.incremental_trainer_report,
        report_output=args.report_output,
        events_output=args.events_output,
    )
    try:
        result = run_ai_shadow_online_feedback_learning_loop(
            paths,
            AIShadowFeedbackLoopConfig(strict=bool(args.strict)),
            write=bool(args.write),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        payload = {
            "status": "blocked",
            "reason": "invalid_feedback_loop_structure",
            "error_type": type(exc).__name__,
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "sends_orders": False,
            "changes_risk": False,
            "changes_model": False,
            "runs_training": False,
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
