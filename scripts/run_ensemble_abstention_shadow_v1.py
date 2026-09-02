#!/usr/bin/env python3
"""Run W4 regime routing + ensemble abstention in research/shadow-only mode."""

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

from smartcrypto.research.ensemble_abstention import (  # noqa: E402
    EnsembleStatus,
    load_aibot_parity_config,
    run_ensemble_abstention,
)
from smartcrypto.research.ensemble_abstention.service import blocked_report  # noqa: E402

MAX_INPUT_BYTES = 8 * 1024 * 1024


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--config", default="config/research/aibot_parity.yaml")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json")
    write_mode = parser.add_mutually_exclusive_group()
    write_mode.add_argument("--write", action="store_true")
    write_mode.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _load_json_file(project_root: Path, source: str | Path) -> dict[str, Any]:
    path = Path(source)
    path = path if path.is_absolute() else project_root / path
    path = path.resolve(strict=False)
    if path.is_symlink():
        raise ValueError("input_json_symlink_forbidden")
    if not path.is_file():
        raise ValueError("input_json_missing")
    if path.suffix.casefold() != ".json":
        raise ValueError("input_json_extension_invalid")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("input_json_too_large")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("input_json_must_be_object")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.project_root).resolve()
    try:
        config = load_aibot_parity_config(root, args.config)
        payload = _load_json_file(root, args.input_json)
        report = run_ensemble_abstention(
            project_root=root,
            config=config,
            request_payload=payload,
            write_report=bool(args.write),
            output_json=args.output_json,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, ValidationError) as exc:
        reason = str(exc).splitlines()[0][:240] or type(exc).__name__
        report = blocked_report(
            reason=f"ensemble_cli_failed:{type(exc).__name__}:{reason}",
            write_requested=bool(args.write),
        )

    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.json else 2,
            allow_nan=False,
        )
    )
    return 2 if report.status is EnsembleStatus.BLOCKED else 0


if __name__ == "__main__":
    raise SystemExit(main())
