from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.ops.monte_carlo_no_trade_recovery import (
    DEFAULT_OUTPUT_PATH,
    build_monte_carlo_no_trade_recovery_diagnostics,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnostica causas de no_trade/bloqueio em Monte Carlo sem liberar live.",
    )
    parser.add_argument("--project-root", default=".", help="Raiz do projeto FUTUROS.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Caminho do JSON de saída. Default: data/reports/monte_carlo_no_trade_recovery_diagnostics.json",
    )
    parser.add_argument("--no-write", action="store_true", help="Não grava o relatório em disco.")
    parser.add_argument("--json", action="store_true", help="Imprime o relatório completo como JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = build_monte_carlo_no_trade_recovery_diagnostics(
        project_root=Path(args.project_root),
        output=Path(args.output),
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
                    "no_trade_detected": result.report["no_trade_detected"],
                    "live_release_allowed": result.report["live_release_allowed"],
                    "root_cause_categories": result.report["root_cause_categories"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
