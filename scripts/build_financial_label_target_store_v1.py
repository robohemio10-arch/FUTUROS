#!/usr/bin/env python3
"""Build financial label and triple-barrier-compatible target-store evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.target_store import build_financial_label_target_store_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--feature-contract", default=None, help="Optional unified feature contract JSON path.")
    parser.add_argument("--dataset-manifest", default=None, help="Optional unified dataset manifest JSON path.")
    parser.add_argument("--dataset", default=None, help="Optional closed-trade training microbatch path.")
    parser.add_argument("--output-json", default=None, help="Optional target-store JSON report path.")
    parser.add_argument("--output-markdown", default=None, help="Optional target-store Markdown report path.")
    parser.add_argument("--summary-json", default=None, help="Optional summary JSON report path.")
    parser.add_argument("--summary-markdown", default=None, help="Optional summary Markdown report path.")
    parser.add_argument("--write", action="store_true", help="Write JSON/Markdown report artifacts under data/reports.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_financial_label_target_store_report(
        project_root=args.project_root,
        write=args.write,
        feature_contract_path=args.feature_contract,
        dataset_manifest_path=args.dataset_manifest,
        dataset_path=args.dataset,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
        summary_json_path=args.summary_json,
        summary_markdown_path=args.summary_markdown,
    )
    payload = cli_payload(report)
    if args.json:
        print(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


def cli_payload(report: dict) -> dict:
    """Return a stdout-safe report without full target records."""

    payload = dict(report)
    target_store = payload.get("target_store")
    if isinstance(target_store, dict):
        public_store = dict(target_store)
        records = public_store.pop("target_records", [])
        if isinstance(records, list):
            public_store["target_records_count"] = len(records)
            public_store["target_records_sample"] = records[:5]
        payload["target_store"] = public_store
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
