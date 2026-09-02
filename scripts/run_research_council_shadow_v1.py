#!/usr/bin/env python3
"""Run the offline, research-only context council against a JSON fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.research_council import (  # noqa: E402
    CouncilRequest,
    ResearchCouncilService,
    load_research_council_config,
)
from smartcrypto.research.research_council.service import blocked_report  # noqa: E402

MAX_INPUT_BYTES = 2 * 1024 * 1024


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--config",
        default="config/research/research_council.yaml",
    )
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json")
    write_mode = parser.add_mutually_exclusive_group()
    write_mode.add_argument("--write-report", action="store_true")
    write_mode.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _load_input(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve(strict=False)
    if path.is_symlink():
        raise ValueError("research_council_input_symlink_forbidden")
    if not path.is_file():
        raise ValueError("research_council_input_missing")
    if path.suffix.casefold() != ".json":
        raise ValueError("research_council_input_extension_invalid")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("research_council_input_too_large")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("research_council_input_root_must_be_mapping")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.project_root).resolve()
    try:
        config = load_research_council_config(root, args.config)
        request = CouncilRequest.model_validate(_load_input(args.input_json))
        report = ResearchCouncilService(config).evaluate(
            request,
            project_root=root,
            write_report=bool(args.write_report),
            output_json=args.output_json,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, ValidationError) as exc:
        reason = str(exc).splitlines()[0][:240] or type(exc).__name__
        report = blocked_report(
            f"invalid_research_council_input:{reason}",
            write_requested=bool(args.write_report),
        )
    payload = report.model_dump(mode="json")
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.json else 2,
            allow_nan=False,
        )
    )
    return 0 if report.status in {"SUCCESS", "PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
