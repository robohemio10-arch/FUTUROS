from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.ops.ai_shadow_threshold_readiness import (
    DEFAULT_MAX_ACCEPTANCE_RATE,
    DEFAULT_MIN_ACCEPTANCE_RATE,
    DEFAULT_MIN_DECISIONS,
    DEFAULT_MIN_PROFIT_FACTOR,
    DEFAULT_OUTPUT_PATH,
    build_ai_shadow_threshold_readiness_evidence,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gera evidência read-only de readiness do threshold AI Shadow sem liberar live.",
    )
    parser.add_argument("--project-root", default=".", help="Raiz do projeto FUTUROS.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--min-decisions", type=int, default=DEFAULT_MIN_DECISIONS)
    parser.add_argument("--min-acceptance-rate", type=float, default=DEFAULT_MIN_ACCEPTANCE_RATE)
    parser.add_argument("--max-acceptance-rate", type=float, default=DEFAULT_MAX_ACCEPTANCE_RATE)
    parser.add_argument("--min-profit-factor", type=float, default=DEFAULT_MIN_PROFIT_FACTOR)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = build_ai_shadow_threshold_readiness_evidence(
        project_root=Path(args.project_root),
        output=Path(args.output),
        min_decisions=args.min_decisions,
        min_acceptance_rate=args.min_acceptance_rate,
        max_acceptance_rate=args.max_acceptance_rate,
        min_profit_factor=args.min_profit_factor,
        no_write=args.no_write,
    )
    if args.json:
        print(json.dumps(result.report, indent=2, sort_keys=True))
    else:
        print(
            json.dumps(
                {
                    "status": result.report["status"],
                    "output": str(result.output_path),
                    "write_performed": result.write_performed,
                    "threshold_readiness_evidence_approved": result.report["threshold_readiness_evidence_approved"],
                    "live_release_allowed": result.report["live_release_allowed"],
                    "metrics": result.report["metrics"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
