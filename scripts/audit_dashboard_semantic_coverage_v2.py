from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from smartcrypto.ops.dashboard_semantic_audit import audit_dashboard_semantic_coverage


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit semantic coverage of the SMART FUTUROS Command Center."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true", help="Emit the full JSON report.")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Emit only status and summary fields.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_dashboard_semantic_coverage(Path(args.project_root))
    payload = report.to_dict()
    if args.summary_only:
        payload = {
            "schema_version": payload["schema_version"],
            "project_name": payload["project_name"],
            "dashboard_name": payload["dashboard_name"],
            "status": payload["status"],
            "summary": payload["summary"],
            "safety": payload["safety"],
        }
    elif not args.json:
        payload = {
            "status": payload["status"],
            "reason": "semantic_coverage_current" if payload["status"] == "ok" else "semantic_coverage_blocked",
            "summary": payload["summary"],
        }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if report.status.value == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
