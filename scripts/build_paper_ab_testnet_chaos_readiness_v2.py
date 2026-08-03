#!/usr/bin/env python3
"""Build the B06 paper A/B, testnet, chaos and readiness report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.paper_ab_testnet_chaos_readiness import (  # noqa: E402
    build_paper_ab_testnet_chaos_readiness_v2,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--evidence", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-isolated-testnet", action="store_true")
    parser.add_argument("--run-isolated-chaos", action="store_true")
    parser.add_argument("--initialize-soak", action="store_true")
    parser.add_argument("--soak-state-path", default=None)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-markdown", default=None)
    parser.add_argument("--fail-on-blocked", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_paper_ab_testnet_chaos_readiness_v2(
        project_root=args.project_root,
        evidence_path=args.evidence,
        config_path=args.config,
        run_isolated_testnet=args.run_isolated_testnet,
        run_isolated_chaos=args.run_isolated_chaos,
        initialize_soak=args.initialize_soak,
        soak_state_path=args.soak_state_path,
        write_report=args.write_report,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
    )
    print(
        json.dumps(
            report,
            indent=None if args.json else 2,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
    )
    return 2 if args.fail_on_blocked and report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
