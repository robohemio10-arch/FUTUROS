from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.ops.paper_shadow_soak_continuity import (
    DEFAULT_DIAGNOSTIC_SOAK_DAYS,
    DEFAULT_MAX_CRITICAL_GAP_MINUTES,
    DEFAULT_MAX_WARNING_GAP_MINUTES,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_REQUIRED_SOAK_DAYS,
    audit_paper_shadow_soak_continuity,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita continuidade paper/shadow soak e contabiliza gaps operacionais.",
    )
    parser.add_argument("--project-root", default=".", help="Raiz do projeto FUTUROS.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Caminho do JSON de saída. Default: data/reports/paper_shadow_soak_continuity_audit.json",
    )
    parser.add_argument("--required-soak-days", type=int, default=DEFAULT_REQUIRED_SOAK_DAYS)
    parser.add_argument("--diagnostic-soak-days", type=int, default=DEFAULT_DIAGNOSTIC_SOAK_DAYS)
    parser.add_argument("--max-warning-gap-minutes", type=int, default=DEFAULT_MAX_WARNING_GAP_MINUTES)
    parser.add_argument("--max-critical-gap-minutes", type=int, default=DEFAULT_MAX_CRITICAL_GAP_MINUTES)
    parser.add_argument("--no-write", action="store_true", help="Não grava o relatório em disco.")
    parser.add_argument("--json", action="store_true", help="Imprime o relatório completo como JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = audit_paper_shadow_soak_continuity(
        project_root=Path(args.project_root),
        output=Path(args.output),
        required_soak_days=args.required_soak_days,
        diagnostic_soak_days=args.diagnostic_soak_days,
        max_warning_gap_minutes=args.max_warning_gap_minutes,
        max_critical_gap_minutes=args.max_critical_gap_minutes,
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
                    "live_release_allowed": result.report["live_release_allowed"],
                    "critical_gap_count": result.report["critical_gap_count"],
                    "warning_gap_count": result.report["warning_gap_count"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
