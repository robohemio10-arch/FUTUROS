#!/usr/bin/env python3
"""Run no-write flow, basis and funding OOS ablation on a PIT event archive."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.aibot_parity.market_intelligence_v2_oos_ablation import (  # noqa: E402
    build_market_intelligence_v2_oos_ablation_v1,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--project-root", default=".")
    value.add_argument("--market-events-jsonl", default=None)
    value.add_argument("--market-events-sha256", default=None)
    value.add_argument("--dataset", default=None)
    value.add_argument("--feature-contract", default=None)
    value.add_argument("--dataset-manifest", default=None)
    value.add_argument("--split-manifest", default=None)
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = build_market_intelligence_v2_oos_ablation_v1(
        project_root=args.project_root,
        market_events_path=args.market_events_jsonl,
        market_events_sha256=args.market_events_sha256,
        dataset_path=args.dataset,
        feature_contract_path=args.feature_contract,
        dataset_manifest_path=args.dataset_manifest,
        split_manifest_path=args.split_manifest,
    )
    print(json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":") if args.json else None,
        indent=None if args.json else 2,
    ))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
