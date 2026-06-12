from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.paper_runtime_health_and_freshness import audit_paper_runtime_health_and_freshness  # noqa: E402
from smartcrypto.ops.paper_runtime_health_and_freshness.contracts import DEFAULT_OUTPUT_PATH  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit paper runtime health and freshness evidence.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--collect-containers",
        action="store_true",
        help="Collect a read-only Docker Compose service snapshot.",
    )
    parser.add_argument(
        "--include-containers",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--container-timeout-seconds", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_paper_runtime_health_and_freshness(
        project_root=args.project_root,
        output=args.output,
        write=bool(args.write),
        collect_containers=bool(args.collect_containers or args.include_containers),
        container_timeout_seconds=float(args.container_timeout_seconds),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{report['status']}: {report['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
