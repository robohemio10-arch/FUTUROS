#!/usr/bin/env python3
"""Materialize, verify or activate the immutable Qlib V3 research epoch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from smartcrypto.learning.paper_autolearning.qlib_market_context_economic_challenger import (
    DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
)
from smartcrypto.learning.paper_autolearning.qlib_v3_reproducible_freeze_epoch import (
    DEFAULT_ACTIVATION_DELAY_SECONDS,
    DEFAULT_ACTIVATION_REPORT_PATH,
    DEFAULT_ACTIVATION_ROOT,
    DEFAULT_ARTIFACT_ROOT,
    DEFAULT_MARKET_FEATURES_PATH,
    DEFAULT_OUTCOME_PATH,
    DEFAULT_REPORT_PATH,
    FreezeV3Error,
    activate_qlib_v3_freeze_epoch_v1,
    blocked_report,
    materialize_qlib_v3_freeze_epoch_v1,
    verify_qlib_v3_freeze_epoch_v1,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create, independently verify or explicitly activate an immutable, "
            "research-only Qlib V3 epoch. The default is no-write and fail-closed."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--source-outcome-path", default=str(DEFAULT_OUTCOME_PATH))
    parser.add_argument(
        "--source-market-features-path",
        default=str(DEFAULT_MARKET_FEATURES_PATH),
    )
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--activation-report-path", default=str(DEFAULT_ACTIVATION_REPORT_PATH))
    parser.add_argument("--activation-root", default=str(DEFAULT_ACTIVATION_ROOT))
    parser.add_argument("--checkpoint-root")
    parser.add_argument("--source-tree-archive")
    parser.add_argument("--git-commit-sha")
    parser.add_argument("--git-tree-sha")
    parser.add_argument("--epoch-dir")
    parser.add_argument(
        "--additional-execution-stress-bps",
        type=float,
        default=DEFAULT_ADDITIONAL_EXECUTION_STRESS_BPS,
    )
    parser.add_argument(
        "--activation-delay-seconds",
        type=int,
        default=DEFAULT_ACTIVATION_DELAY_SECONDS,
    )
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--materialize-freeze", action="store_true")
    operation.add_argument("--verify-freeze", action="store_true")
    operation.add_argument("--activate-freeze", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.materialize_freeze:
        if not args.checkpoint_root:
            return blocked_report("checkpoint_root_required_for_materialization")
        return materialize_qlib_v3_freeze_epoch_v1(
            project_root=Path(args.project_root),
            outcome_path=args.source_outcome_path,
            market_features_path=args.source_market_features_path,
            artifact_root=args.artifact_root,
            report_path=args.report_path,
            checkpoint_root=Path(args.checkpoint_root),
            additional_execution_stress_bps=args.additional_execution_stress_bps,
            prepared_source_archive=args.source_tree_archive,
            prepared_git_commit_sha=args.git_commit_sha,
            prepared_git_tree_sha=args.git_tree_sha,
        )
    if args.verify_freeze:
        if not args.epoch_dir:
            return blocked_report("epoch_dir_required_for_verification")
        return verify_qlib_v3_freeze_epoch_v1(
            project_root=Path(args.project_root),
            epoch_dir=Path(args.epoch_dir),
        )
    if args.activate_freeze:
        if not args.epoch_dir:
            return blocked_report("epoch_dir_required_for_activation")
        if not args.checkpoint_root:
            return blocked_report("checkpoint_root_required_for_activation")
        return activate_qlib_v3_freeze_epoch_v1(
            project_root=Path(args.project_root),
            epoch_dir=Path(args.epoch_dir),
            checkpoint_root=Path(args.checkpoint_root),
            activation_root=args.activation_root,
            report_path=args.activation_report_path,
            activation_delay_seconds=args.activation_delay_seconds,
        )
    return blocked_report("explicit_materialize_verify_or_activate_operation_required")


def main() -> int:
    args = _parser().parse_args()
    try:
        report = run(args)
    except FreezeV3Error as exc:
        report = blocked_report(exc.reason)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, ImportError) as exc:
        report = blocked_report(f"controlled_input_environment_or_io_error:{type(exc).__name__}")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    else:
        print(
            f"status={report.get('status')} "
            f"reason={report.get('reason')} "
            f"v3_freeze_dod={report.get('v3_freeze_dod')}"
        )
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
