#!/usr/bin/env python3
"""Materialize checksum-pinned public WQ6 PIT observations outside Git, explicitly."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.aibot_parity.market_intelligence_pit_source_foundation import (  # noqa: E402
    build_source_foundation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--dataset")
    parser.add_argument("--feature-contract")
    parser.add_argument("--dataset-manifest")
    parser.add_argument("--split-manifest")
    parser.add_argument(
        "--write", action="store_true", help="Download/cache public archives and create outputs"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = build_source_foundation(
            project_root=args.project_root,
            output_root=args.output_root,
            write=args.write,
            dataset_path=args.dataset,
            feature_contract_path=args.feature_contract,
            dataset_manifest_path=args.dataset_manifest,
            split_manifest_path=args.split_manifest,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        report = {
            "status": "blocked",
            "reason": f"{type(exc).__name__}:{exc}",
            "write_performed": False,
            "partial_external_cache_writes_possible": args.write,
        }
    print(json.dumps(report, sort_keys=True, allow_nan=False, indent=None if args.json else 2))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
