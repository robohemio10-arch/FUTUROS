#!/usr/bin/env python3
"""Build research-only Paper Capacity Scaleout V1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__
    )
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
    )
    parser.add_argument(
        "--paper-db",
        required=True,
    )
    parser.add_argument(
        "--baseline-commit",
        required=True,
    )
    parser.add_argument(
        "--baseline-capacity",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--branch4-report",
        default=(
            "data/reports/"
            "paper_ab_edge_selector_v1.json"
        ),
    )
    parser.add_argument(
        "--opportunity-report",
        default=(
            "data/reports/"
            "shadow_opportunity_engine_v1.json"
        ),
    )
    parser.add_argument(
        "--opportunity-outcomes",
        default=None,
        help=(
            "Explicit JSONL carrying exact candidate_id, "
            "outcome_available_at_utc and realized net PnL. "
            "Branch-4 assignment JSONL is not an outcome source."
        ),
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
    )
    parser.add_argument(
        "--write-research",
        action="store_true",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
    )
    parser.add_argument("--output-report")
    parser.add_argument("--output-markdown")
    parser.add_argument("--output-research")
    parser.add_argument(
        "--json",
        action="store_true",
    )
    return parser


def _controlled_failure(
    reason: str,
    detail: str,
) -> dict[str, Any]:
    from smartcrypto.research.paper_capacity_scaleout import (
        SAFETY_FLAGS,
    )

    return {
        "schema_version": "paper_capacity_scaleout_v1",
        "status": "blocked",
        "reason": reason,
        "decision": "AGUARDAR_EVIDENCIA",
        "simulation_mode": "RESEARCH_SIMULATION_ONLY",
        "capacity_evidence": {
            "status": "EVIDENCE_BLOCKED",
            "blockers": [reason],
            "activation_allowed": False,
        },
        "software_dod": {
            "status": "BLOCKED",
            "reason": reason,
            "error_detail": detail[:512],
        },
        "capacity_activation_allowed": False,
        "write_requested": False,
        "write_performed": False,
        "safety": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
    }


def main(
    argv: Sequence[str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)

    from smartcrypto.research.paper_capacity_scaleout import (
        build_paper_capacity_scaleout_v1,
    )

    writes_allowed = not args.no_write

    try:
        report = build_paper_capacity_scaleout_v1(
            project_root=args.project_root,
            paper_db=args.paper_db,
            baseline_commit=args.baseline_commit,
            baseline_capacity=args.baseline_capacity,
            branch4_report=args.branch4_report,
            opportunity_report=args.opportunity_report,
            opportunity_outcomes=args.opportunity_outcomes,
            write_report_requested=bool(
                writes_allowed
                and args.write_report
            ),
            write_research_requested=bool(
                writes_allowed
                and args.write_research
            ),
            output_report=args.output_report,
            output_markdown=args.output_markdown,
            output_research=args.output_research,
        )
    except (
        ImportError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        report = _controlled_failure(
            type(exc).__name__,
            str(exc),
        )

    if args.json:
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                default=str,
            )
        )
    else:
        print(
            f"STATUS={report['status']}"
        )
        print(
            f"REASON={report['reason']}"
        )
        print(
            f"DECISION={report['decision']}"
        )
        print(
            "CAPACITY_EVIDENCE="
            + str(
                report.get(
                    "capacity_evidence",
                    {},
                ).get("status")
            )
        )
        print(
            "CAPACITY_ACTIVATION_ALLOWED="
            + str(
                report.get(
                    "capacity_activation_allowed"
                )
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
