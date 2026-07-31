from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.learning.g00_targeted_feedback_backfill import (
    CONFIRMATION_TEXT,
    run_g00_targeted_feedback_backfill_v1,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Target-only G00 feedback backfill for paper trades 599 and 600."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--execute-targeted-backfill",
        action="store_true",
    )
    parser.add_argument("--expected-plan-hash")
    parser.add_argument("--expected-dryrun-hash")
    parser.add_argument("--expected-target-batch-hash")
    parser.add_argument("--expected-source-fingerprint-hash")
    parser.add_argument("--authorization-reference")
    parser.add_argument("--confirmation-text")
    parser.add_argument("--allow-paper-db-read", action="store_true")
    parser.add_argument("--paper-db-path")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--print-confirmation-text",
        action="store_true",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.print_confirmation_text:
        print(CONFIRMATION_TEXT)
        return 0

    report = run_g00_targeted_feedback_backfill_v1(
        project_root=Path(args.project_root),
        execute_targeted_backfill=bool(
            args.execute_targeted_backfill
        ),
        expected_plan_hash=args.expected_plan_hash,
        expected_dryrun_hash=args.expected_dryrun_hash,
        expected_target_batch_hash=(
            args.expected_target_batch_hash
        ),
        expected_source_fingerprint_hash=(
            args.expected_source_fingerprint_hash
        ),
        authorization_reference=args.authorization_reference,
        confirmation_text=args.confirmation_text,
        allow_paper_db_read=bool(args.allow_paper_db_read),
        paper_db_path=args.paper_db_path,
        write_report=bool(args.write_report),
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
    )
    if args.json:
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        print(
            "status={status} reason={reason} decision={decision}".format(
                status=report.get("status"),
                reason=report.get("reason"),
                decision=report.get("decision"),
            )
        )
    return 0 if report.get("status") in {"ok", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
