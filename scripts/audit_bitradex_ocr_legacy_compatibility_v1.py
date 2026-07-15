#!/usr/bin/env python3
"""Audit historical Bitradex OCR compatibility without authorizing an import."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.bitradex_ocr_legacy_compatibility import (  # noqa: E402
    DEFAULT_CONTRACT_PATH,
    DEFAULT_OUTPUT_JSON,
    DEFAULT_OUTPUT_MARKDOWN,
    build_legacy_compatibility_audit,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT_PATH))
    parser.add_argument("--preview-summary", default=None)
    parser.add_argument("--preview-csv", default=None)
    parser.add_argument("--master-xlsx", default=None)
    parser.add_argument("--master-parquet", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-write", action="store_true")
    mode.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--output-markdown", default=str(DEFAULT_OUTPUT_MARKDOWN))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_legacy_compatibility_audit(
        project_root=args.project_root,
        contract_path=args.contract,
        preview_summary_path=args.preview_summary,
        preview_csv_path=args.preview_csv,
        master_xlsx_path=args.master_xlsx,
        master_parquet_path=args.master_parquet,
        write_report=bool(args.write_report and not args.no_write),
        output_json=args.output_json,
        output_markdown=args.output_markdown,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            indent=None if args.json else 2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
