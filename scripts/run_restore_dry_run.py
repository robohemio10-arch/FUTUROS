from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.backup_restore import DEFAULT_RESTORE_REPORT_PATH, run_restore_dry_run  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a backup snapshot without restoring files.")
    parser.add_argument("--backup-dir", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--report", default=str(DEFAULT_RESTORE_REPORT_PATH))
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_restore_dry_run(
        backup_dir=args.backup_dir,
        manifest=args.manifest,
        report_path=args.report,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
