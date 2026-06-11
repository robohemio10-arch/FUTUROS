from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.paper_shadow_soak_anchor import (  # noqa: E402
    DEFAULT_DIAGNOSTIC_SOAK_DAYS,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_REQUIRED_SOAK_DAYS,
    audit_paper_shadow_soak_anchor_continuity_pack,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audita o anchor/continuity pack do paper-shadow soak sem side effects por padrão.",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--diagnostic-soak-days", type=int, default=DEFAULT_DIAGNOSTIC_SOAK_DAYS)
    parser.add_argument("--required-soak-days", type=int, default=DEFAULT_REQUIRED_SOAK_DAYS)
    parser.add_argument("--write", action="store_true", help="Materializa o JSON em data/reports. Default: read-only/no-write.")
    parser.add_argument("--json", action="store_true", help="Imprime o relatório completo em JSON compacto.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = audit_paper_shadow_soak_anchor_continuity_pack(
        project_root=args.project_root,
        output=args.output,
        diagnostic_soak_days=args.diagnostic_soak_days,
        required_soak_days=args.required_soak_days,
        write=bool(args.write),
    )
    if args.json:
        print(json.dumps(result.report, ensure_ascii=False, sort_keys=True))
    else:
        summary = {
            "status": result.report["status"],
            "reason": result.report["reason"],
            "output": str(result.output_path),
            "write_performed": result.write_performed,
            "observed_soak_days": result.report["observed_soak_days"],
            "seven_day_diagnostic_status": result.report["seven_day_diagnostic_status"],
            "thirty_day_readiness_status": result.report["thirty_day_readiness_status"],
            "critical_gap_count": result.report["critical_gap_count"],
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "sends_orders": False,
            "changes_risk": False,
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
