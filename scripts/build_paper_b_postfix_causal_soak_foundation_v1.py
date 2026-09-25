#!/usr/bin/env python3
"""Create or audit the immutable Paper-B post-fix causal soak foundation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


from smartcrypto.research.canonical_treatment.postfix_causal_soak_foundation import (  # noqa: E402
    DEFAULT_BASELINE_RELATIVE_PATH,
    DEFAULT_REPORT_RELATIVE_PATH,
    run_foundation,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=__doc__,
    )

    value.add_argument(
        "--project-root",
        default=str(
            PROJECT_ROOT
        ),
    )

    value.add_argument(
        "--runtime-root",
        required=True,
    )

    value.add_argument(
        "--observer-root",
        required=True,
    )

    value.add_argument(
        "--baseline",
        default=str(
            DEFAULT_BASELINE_RELATIVE_PATH
        ),
    )

    value.add_argument(
        "--report",
        default=str(
            DEFAULT_REPORT_RELATIVE_PATH
        ),
    )

    value.add_argument(
        "--create-baseline",
        action="store_true",
    )

    value.add_argument(
        "--fix-deployed-at-utc",
    )

    value.add_argument(
        "--deployment-evidence",
    )

    value.add_argument(
        "--verification-v3-signal-count",
        type=int,
    )

    value.add_argument(
        "--verification-v3-outcome-count",
        type=int,
    )

    value.add_argument(
        "--scheduler-misses",
        type=int,
        default=7,
    )

    value.add_argument(
        "--dns-misses",
        type=int,
        default=2,
    )

    value.add_argument(
        "--unresolved-misses",
        type=int,
        default=2,
    )

    value.add_argument(
        "--write-report",
        action="store_true",
    )

    value.add_argument(
        "--json",
        action="store_true",
    )

    return value


def main(
    argv: list[str] | None = None,
) -> int:
    args = parser().parse_args(
        argv
    )

    report = run_foundation(
        project_root=(
            args.project_root
        ),
        runtime_root=(
            args.runtime_root
        ),
        observer_root=(
            args.observer_root
        ),
        baseline_path=(
            args.baseline
        ),
        report_path=(
            args.report
        ),
        create_baseline=bool(
            args.create_baseline
        ),
        fix_deployed_at_utc=(
            args.fix_deployed_at_utc
        ),
        deployment_evidence_path=(
            args.deployment_evidence
        ),
        verification_v3_signal_count=(
            args.verification_v3_signal_count
        ),
        verification_v3_outcome_count=(
            args.verification_v3_outcome_count
        ),
        scheduler_misses=(
            args.scheduler_misses
        ),
        dns_misses=(
            args.dns_misses
        ),
        unresolved_misses=(
            args.unresolved_misses
        ),
        write_report=bool(
            args.write_report
        ),
    )

    print(
        json.dumps(
            report,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            indent=(
                None
                if args.json
                else 2
            ),
            separators=(
                (",", ":")
                if args.json
                else None
            ),
            default=str,
        )
    )

    return (
        0
        if report.get("status")
        == "ok"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
