"""Build the read-only AI research training track closeout handover."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_api() -> tuple[Any, Any]:
    project_root = Path(__file__).resolve().parents[1]
    root_text = str(project_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from smartcrypto.research.ai_research_training_track_closeout_handover import (
        resolve_paths,
        run_ai_research_training_track_closeout_handover,
    )

    return resolve_paths, run_ai_research_training_track_closeout_handover


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Consolida evidencias JSON das branches 01-09 em um handover "
            "research-only, sem autoridade operacional."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--report-output")
    parser.add_argument("--markdown-output")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    resolve_paths, run_closeout = _load_api()
    try:
        paths = resolve_paths(
            args.project_root,
            report_output=args.report_output,
            markdown_output=args.markdown_output,
        )
        result = run_closeout(paths, write=bool(args.write))
        payload = result.report
        exit_code = 0
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        payload = {
            "status": "blocked",
            "reason": "closeout_handover_failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "sends_orders": False,
        }
        exit_code = 1
    output = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if args.json_output:
        print(output)
    else:
        print(
            "CLOSEOUT_STATUS=" + str(payload.get("track_status") or payload["status"])
        )
        print("CLOSEOUT_DECISION=" + str(payload.get("decision") or "UNKNOWN"))
        print("CLOSEOUT_JSON=" + output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
