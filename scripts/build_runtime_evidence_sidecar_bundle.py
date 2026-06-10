from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.runtime_evidence_sidecar import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    build_runtime_evidence_sidecar_bundle,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build runtime evidence sidecar bundle.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-containers", action="store_true")
    parser.add_argument("--container-timeout-seconds", type=float, default=3.0)
    parser.add_argument(
        "--no-refresh-runtime-evidence",
        action="store_true",
        help="Do not refresh runtime_evidence_pack_v2/readiness_snapshot_v2 before bundling.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_runtime_evidence_sidecar_bundle(
        project_root=args.project_root,
        output_root=args.output_root,
        no_write=bool(args.no_write),
        refresh_runtime_evidence=not bool(args.no_refresh_runtime_evidence),
        include_containers=bool(args.include_containers),
        container_timeout_seconds=float(args.container_timeout_seconds),
    )
    if args.json:
        print(json.dumps(result.summary, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{result.summary['status']}: {result.summary['reason']}")
        print(result.summary["bundle_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
