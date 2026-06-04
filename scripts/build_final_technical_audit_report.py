from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.final_technical_audit import (  # noqa: E402
    DEFAULT_OUTPUT_PATH,
    DEFAULT_REPORTS_ROOT,
    build_final_technical_audit_report,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the final technical audit and 20-pillar reclassification report.")
    parser.add_argument("--reports-root", default=str(DEFAULT_REPORTS_ROOT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--project-root", default=str(Path.cwd()))
    parser.add_argument("--required-target-score", type=float, default=9.0)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_final_technical_audit_report(
        reports_root=args.reports_root,
        output_path=args.output,
        project_root=args.project_root,
        required_target_score=args.required_target_score,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
