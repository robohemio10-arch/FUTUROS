#!/usr/bin/env python3
"""Build unified AI feature contract and dataset manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.feature_contracts import build_unified_feature_contract_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--contract-json", default=None, help="Optional feature contract JSON output path.")
    parser.add_argument("--contract-markdown", default=None, help="Optional feature contract Markdown output path.")
    parser.add_argument("--manifest-json", default=None, help="Optional dataset manifest JSON output path.")
    parser.add_argument("--manifest-markdown", default=None, help="Optional dataset manifest Markdown output path.")
    parser.add_argument("--write", action="store_true", help="Write contract and manifest reports under data/reports.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_unified_feature_contract_report(
        project_root=args.project_root,
        write=args.write,
        contract_json_path=args.contract_json,
        contract_markdown_path=args.contract_markdown,
        manifest_json_path=args.manifest_json,
        manifest_markdown_path=args.manifest_markdown,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
