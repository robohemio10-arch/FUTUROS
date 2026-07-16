#!/usr/bin/env python3
"""Plan, explicitly apply, or verify the canonical XLSX transition V2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.bitradex_ocr_legacy_canonical_xlsx_transition_v2 import (  # noqa: E402
    DEFAULT_TRANSITION_CONTRACT,
    apply_canonical_xlsx_transition,
    build_canonical_xlsx_transition_plan,
    verify_canonical_xlsx_transition,
)
from smartcrypto.data.bitradex_ocr_legacy_canonical_xlsx_transition_v2.planner import (  # noqa: E402
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MARKDOWN,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    for command in ("plan", "apply", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--project-root", default=".")
        subparser.add_argument(
            "--transition-contract",
            default=str(DEFAULT_TRANSITION_CONTRACT),
        )
        subparser.add_argument("--json", action="store_true")
        if command == "plan":
            subparser.add_argument("--write-report", action="store_true")
            subparser.add_argument(
                "--output-json", default=str(DEFAULT_REPORT_JSON)
            )
            subparser.add_argument(
                "--output-markdown",
                default=str(DEFAULT_REPORT_MARKDOWN),
            )
        elif command == "apply":
            subparser.add_argument("--expected-plan-sha256", default=None)
            subparser.add_argument("--authorization-phrase", default=None)
            subparser.add_argument(
                "--output-json", default=str(DEFAULT_REPORT_JSON)
            )
            subparser.add_argument(
                "--output-markdown",
                default=str(DEFAULT_REPORT_MARKDOWN),
            )
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not raw_args or raw_args[0] not in {"plan", "apply", "verify"}:
        raw_args.insert(0, "plan")
    return parser.parse_args(raw_args)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "apply":
        report = apply_canonical_xlsx_transition(
            project_root=args.project_root,
            transition_contract_path=args.transition_contract,
            expected_plan_sha256=args.expected_plan_sha256,
            authorization_phrase=args.authorization_phrase,
            output_json=args.output_json,
            output_markdown=args.output_markdown,
        )
    elif args.command == "verify":
        report = verify_canonical_xlsx_transition(
            project_root=args.project_root,
            transition_contract_path=args.transition_contract,
        )
    else:
        report = build_canonical_xlsx_transition_plan(
            project_root=args.project_root,
            transition_contract_path=args.transition_contract,
            write_report=args.write_report,
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
