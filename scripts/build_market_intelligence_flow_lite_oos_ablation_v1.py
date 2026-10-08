#!/usr/bin/env python3
"""Evaluate public closed-kline FLOW_LITE; no report writes or downloads by default."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.aibot_parity.market_intelligence_flow_lite_oos_ablation import (  # noqa: E402
    build_flow_lite_oos_ablation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--feature-contract", default=None)
    parser.add_argument("--dataset-manifest", default=None)
    parser.add_argument("--split-manifest", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_flow_lite_oos_ablation(
        project_root=args.project_root,
        cache_root=args.cache_root,
        allow_download=args.allow_download,
        dataset_path=args.dataset,
        feature_contract_path=args.feature_contract,
        dataset_manifest_path=args.dataset_manifest,
        split_manifest_path=args.split_manifest,
    )
    print(json.dumps(report, sort_keys=True, allow_nan=False, indent=None if args.json else 2))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
