from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.ops.live_canary_contract import (
    DEFAULT_MANUAL_GOVERNANCE_PATH,
    DEFAULT_OUTPUT_PATH,
    build_live_canary_contract_with_hard_blocks,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gera contrato read-only de canário com hard blocks.")
    parser.add_argument("--project-root", default=".", help="Raiz do projeto FUTUROS.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--manual-governance-path", default=str(DEFAULT_MANUAL_GOVERNANCE_PATH))
    parser.add_argument("--candidate-config-path", default=None)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = build_live_canary_contract_with_hard_blocks(
        project_root=Path(args.project_root),
        output=Path(args.output),
        manual_governance_path=Path(args.manual_governance_path),
        candidate_config_path=Path(args.candidate_config_path) if args.candidate_config_path else None,
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
                    "manual_go_no_go_required": result.report["manual_go_no_go_required"],
                    "hard_blocks_enforced": result.report["hard_blocks_enforced"],
                    "release_allowed": result.report["release_allowed"],
                    "live_release_allowed": result.report["live_release_allowed"],
                    "canary_release_allowed": result.report["canary_release_allowed"],
                    "sends_orders": result.report["sends_orders"],
                    "changes_risk": result.report["changes_risk"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
