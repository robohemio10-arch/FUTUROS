#!/usr/bin/env python3
"""Build the research-only Paper A/B Edge Selector V1 evidence report."""

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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--paper-db", required=True)
    parser.add_argument("--feature-source")
    parser.add_argument("--qlib-source")
    parser.add_argument("--regime-source")
    parser.add_argument("--trader-master-source")
    parser.add_argument("--execution-cost-source")
    parser.add_argument(
        "--qlib-security-report",
        default="data/reports/qlib_dependency_security_hardening_v1.json",
    )
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--minimum-observations-per-arm", type=int, default=200)
    parser.add_argument("--minimum-observation-days", type=int, default=45)
    parser.add_argument("--minimum-profit-factor", type=float, default=1.10)
    parser.add_argument("--bootstrap-iterations", type=int, default=5000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260820)
    parser.add_argument("--confidence-level", type=float, default=0.95)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--write-assignments", action="store_true")
    parser.add_argument("--output-report")
    parser.add_argument("--output-assignments")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Force no-write mode; no-write is also the default unless an explicit write flag is used.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def _controlled_failure(reason: str, detail: str) -> dict[str, Any]:
    from smartcrypto.research.paper_ab_edge_selector import DECISION, SAFETY_FLAGS

    return {
        "schema_version": "paper_ab_edge_selector_v1",
        "status": "BLOCKED",
        "reason": reason,
        "decision": DECISION,
        "candidate_linked_rows": 0,
        "eligible_treatment_count": 0,
        "treatment_evaluable": False,
        "financial_evidence": {
            "status": "EVIDENCE_BLOCKED",
            "reason": reason,
            "treatment_evaluable": False,
            "eligible_treatment_count": 0,
            "blockers": [reason],
        },
        "software_dod": {
            "status": "BLOCKED",
            "reason": reason,
            "error_detail": detail[:512],
        },
        "treatment_release": {
            "status": "BLOCKED",
            "allowed": False,
            "reason": "RESEARCH_ONLY_NO_OPERATIONAL_AUTHORITY",
        },
        "treatment_release_allowed": False,
        "safety": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
        "write_requested": False,
        "write_performed": False,
        "write_report_performed": False,
        "write_assignments_performed": False,
        "assignments_appended": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from smartcrypto.research.paper_ab_edge_selector import (  # noqa: PLC0415
        build_paper_ab_edge_selector_v1,
    )

    write_report = bool(args.write_report and not args.no_write)
    write_assignments = bool(args.write_assignments and not args.no_write)
    try:
        report = build_paper_ab_edge_selector_v1(
            project_root=args.project_root,
            paper_db=args.paper_db,
            feature_source=args.feature_source,
            qlib_source=args.qlib_source,
            regime_source=args.regime_source,
            trader_master_source=args.trader_master_source,
            execution_cost_source=args.execution_cost_source,
            qlib_security_report=args.qlib_security_report,
            experiment_id=args.experiment_id,
            minimum_observations_per_arm=args.minimum_observations_per_arm,
            minimum_observation_days=args.minimum_observation_days,
            minimum_profit_factor=args.minimum_profit_factor,
            bootstrap_iterations=args.bootstrap_iterations,
            bootstrap_seed=args.bootstrap_seed,
            confidence_level=args.confidence_level,
            write_report_requested=write_report,
            write_assignments_requested=write_assignments,
            output_report=args.output_report,
            output_assignments=args.output_assignments,
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        report = _controlled_failure(type(exc).__name__, str(exc))

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
        print(f"STATUS={report['status']}")
        print(f"REASON={report['reason']}")
        print(f"DECISION={report['decision']}")
        print(
            "FINANCIAL_EVIDENCE="
            + str(report.get("financial_evidence", {}).get("status"))
        )
        print(
            "ELIGIBLE_TREATMENT_COUNT="
            + str(report.get("eligible_treatment_count", 0))
        )

    # A blocked financial-evidence state is an expected research outcome, not
    # a process failure. Controlled failures are emitted as valid fail-closed JSON.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
