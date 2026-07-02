#!/usr/bin/env python3
"""Build purged walk-forward anti-leakage split evidence without training."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.walkforward import build_walkforward_anti_leakage_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--feature-contract", default=None, help="Optional FeatureContract JSON path.")
    parser.add_argument("--dataset-manifest", default=None, help="Optional DatasetManifest JSON path.")
    parser.add_argument("--target-store", default=None, help="Optional TargetStore JSON path.")
    parser.add_argument("--dataset", default=None, help="Optional selected dataset path.")
    parser.add_argument("--output-json", default=None, help="Optional split engine JSON report path.")
    parser.add_argument("--output-markdown", default=None, help="Optional split engine Markdown report path.")
    parser.add_argument("--baseline-json", default=None, help="Optional baseline JSON report path.")
    parser.add_argument("--baseline-markdown", default=None, help="Optional baseline Markdown report path.")
    parser.add_argument("--embargo-seconds", type=int, default=None, help="Explicit embargo seconds override.")
    parser.add_argument("--write", action="store_true", help="Write JSON/Markdown reports under data/reports.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_walkforward_anti_leakage_report(
        project_root=args.project_root,
        write=args.write,
        feature_contract_path=args.feature_contract,
        dataset_manifest_path=args.dataset_manifest,
        target_store_path=args.target_store,
        dataset_path=args.dataset,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
        baseline_json_path=args.baseline_json,
        baseline_markdown_path=args.baseline_markdown,
        embargo_seconds_override=args.embargo_seconds,
    )
    payload = cli_payload(report)
    if args.json:
        print(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


def cli_payload(report: dict) -> dict:
    payload = dict(report)
    split_engine = payload.get("split_engine")
    if isinstance(split_engine, dict):
        public_engine = dict(split_engine)
        splits = public_engine.get("splits", [])
        if isinstance(splits, list):
            public_engine["splits_count"] = len(splits)
            public_engine["splits_sample"] = splits[:3]
            public_engine.pop("splits", None)
        payload["split_engine"] = public_engine
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
