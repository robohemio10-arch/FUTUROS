from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.backup_restore import DEFAULT_REPORT_PATH, create_backup_snapshot  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an offline paper/shadow backup snapshot.")
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--project-root", default=str(ROOT))
    parser.add_argument("--allow-freqtrade-db", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = create_backup_snapshot(
        inputs=args.inputs,
        output_dir=args.output_dir,
        report_path=args.report,
        project_root=args.project_root,
        allow_freqtrade_db=args.allow_freqtrade_db,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
