#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from smartcrypto.research.ocr_v11_dataset import build_from_paths, json_safe, resolve_paths


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the read-only OCR V1.1 trade and candle research dataset."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--master")
    parser.add_argument("--master-projection")
    parser.add_argument("--candles")
    parser.add_argument("--output")
    parser.add_argument("--report")
    parser.add_argument("--executive-reports-dir")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-write", action="store_true", help="Validate in memory (default).")
    mode.add_argument("--write", action="store_true", help="Write research-only runtime outputs.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = resolve_paths(
        args.project_root,
        master_path=args.master,
        master_projection_path=args.master_projection,
        candles_path=args.candles,
        output_path=args.output,
        report_path=args.report,
        executive_reports_dir=args.executive_reports_dir,
    )
    result = build_from_paths(paths, write=bool(args.write))
    payload = json.dumps(json_safe(result.report), ensure_ascii=False, sort_keys=True)
    print(payload if args.json else json.dumps(json_safe(result.report), ensure_ascii=False, indent=2, sort_keys=True))
    return {"ok": 0, "blocked": 1, "failed": 2}.get(str(result.report["status"]), 2)


if __name__ == "__main__":
    raise SystemExit(main())
