#!/usr/bin/env python3
"""Audit paper auto-learning scheduler deployment readiness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autolearning.scheduler_deployment import (  # noqa: E402
    build_paper_autolearning_scheduler_deployment_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--compose", default=None, help="Optional docker-compose.paper.yml path.")
    parser.add_argument("--kill-switch-contract", default=None, help="Optional kill-switch contract/template path.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_paper_autolearning_scheduler_deployment_report(
        project_root=args.project_root,
        compose_path=args.compose,
        kill_switch_contract_path=args.kill_switch_contract,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
