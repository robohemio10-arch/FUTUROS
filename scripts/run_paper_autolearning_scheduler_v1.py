#!/usr/bin/env python3
"""Run paper auto-learning daily scheduler dry-run or once."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autolearning.scheduler import (  # noqa: E402
    build_paper_autolearning_scheduler_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--source", default=None, help="Optional closed trades source forwarded to the foundation runner.")
    parser.add_argument("--once", action="store_true", help="Invoke the foundation runner once.")
    parser.add_argument("--write-feedback", action="store_true", help="Allow foundation writes only under data/feedback and data/reports.")
    parser.add_argument("--train-smoke", action="store_true", help="Run advisory Qlib/IA Shadow challenger smoke checks.")
    parser.add_argument("--register-scheduler", action="store_true", help="Blocked in this branch; real scheduler registration is deferred.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_paper_autolearning_scheduler_report(
        project_root=args.project_root,
        once=args.once,
        write_feedback=args.write_feedback,
        train_smoke=args.train_smoke,
        register_scheduler=args.register_scheduler,
        source_path=args.source,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
