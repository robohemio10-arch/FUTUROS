#!/usr/bin/env python3
"""Preview or explicitly append paper feedback trades into trades_master."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_autolearning.master_consolidation import (  # noqa: E402
    build_paper_feedback_master_consolidation_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--source", default=None, help="Optional explicit input source path.")
    parser.add_argument("--trades-master-xlsx", default=None, help="Optional trades_master.xlsx path.")
    parser.add_argument("--trades-master-parquet", default=None, help="Optional trades_master.parquet path.")
    parser.add_argument("--preview-json", default=None, help="Optional preview JSON output path.")
    parser.add_argument("--preview-markdown", default=None, help="Optional preview Markdown output path.")
    parser.add_argument("--backup-root", default=None, help="Optional backup root used only with --write-master.")
    parser.add_argument("--write-preview", action="store_true", help="Write preview JSON/Markdown under data/reports.")
    parser.add_argument("--write-master", action="store_true", help="Append accepted rows to trades_master after backup.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_paper_feedback_master_consolidation_report(
        project_root=args.project_root,
        source_path=args.source,
        trades_master_xlsx_path=args.trades_master_xlsx,
        trades_master_parquet_path=args.trades_master_parquet,
        preview_json_path=args.preview_json,
        preview_markdown_path=args.preview_markdown,
        backup_root=args.backup_root,
        write_preview=args.write_preview,
        write_master=args.write_master,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
