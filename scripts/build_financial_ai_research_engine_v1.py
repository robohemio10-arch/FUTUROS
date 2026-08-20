from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build research-only financial AI evidence from point-in-time sources."
        )
    )
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--paper-db")
    parser.add_argument("--feature-source")
    parser.add_argument("--qlib-source")
    parser.add_argument("--regime-source")
    parser.add_argument("--trader-master-source")
    parser.add_argument("--execution-cost-source")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--write-estimates", action="store_true")
    parser.add_argument("--output-report")
    parser.add_argument("--output-estimates")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from smartcrypto.research.financial_ai_research_engine import (  # noqa: PLC0415
        build_financial_ai_research_engine_v1,
    )

    write_report = bool(args.write_report and not args.no_write)
    write_estimates = bool(args.write_estimates and not args.no_write)
    write_audit = {
        "write_requested": bool(write_report or write_estimates),
        "write_performed": False,
        "write_report_performed": False,
        "write_estimates_performed": False,
        "estimates_appended": 0,
    }

    try:
        report = build_financial_ai_research_engine_v1(
            project_root=args.project_root,
            paper_db=args.paper_db,
            feature_source=args.feature_source,
            qlib_source=args.qlib_source,
            regime_source=args.regime_source,
            trader_master_source=args.trader_master_source,
            execution_cost_source=args.execution_cost_source,
            write_report_requested=write_report,
            write_estimates_requested=write_estimates,
            output_report=args.output_report,
            output_estimates=args.output_estimates,
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        report = _controlled_failure(
            type(exc).__name__,
            str(exc),
            write_audit=write_audit,
        )

    if args.json:
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        )
    else:
        print(f"STATUS={report['status']}")
        print(f"REASON={report['reason']}")
        print(f"DECISION={report['decision']}")

    return 0


def _controlled_failure(
    reason: str,
    detail: str,
    *,
    write_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from smartcrypto.research.financial_ai_research_engine import (  # noqa: PLC0415
        DECISION,
        SAFETY_FLAGS,
    )

    audit = {
        "write_requested": False,
        "write_performed": False,
        "write_report_performed": False,
        "write_estimates_performed": False,
        "estimates_appended": 0,
    }
    if write_audit is not None:
        audit.update(dict(write_audit))

    return {
        "schema_version": "financial_ai_research_engine_v1",
        "status": "BLOCKED",
        "reason": reason,
        "decision": DECISION,
        "error_detail": detail[:256],
        "blockers": [reason],
        "gates": {
            "candidate_ev_ready": False,
            "remaining_position_ev_ready": False,
            "financial_ai_research_ready": False,
        },
        "safety": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
        **audit,
    }


if __name__ == "__main__":
    raise SystemExit(main())
