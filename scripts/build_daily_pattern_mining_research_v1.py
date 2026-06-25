"""Build the Daily Pattern Mining Research report."""

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
    from smartcrypto.research.daily_pattern_mining_research import (
        DEFAULT_MIN_CONFIDENCE,
        DEFAULT_MIN_SUPPORT_COUNT,
        build_daily_pattern_mining_research_report,
        validate_daily_pattern_mining_research_report,
    )

    return (
        build_daily_pattern_mining_research_report,
        validate_daily_pattern_mining_research_report,
        DEFAULT_MIN_SUPPORT_COUNT,
        DEFAULT_MIN_CONFIDENCE,
    )


def build_parser() -> argparse.ArgumentParser:
    _, _, default_support, default_confidence = _load_api()
    parser = argparse.ArgumentParser(
        description=(
            "Minera padroes descritivos apenas com entradas em memoria. "
            "O CLI nao le dados reais."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--min-support-count", type=int, default=default_support)
    parser.add_argument("--min-confidence", type=float, default=default_confidence)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    build_report, validate_report, _, _ = _load_api()
    project_root = Path(args.project_root).expanduser().resolve()
    payload = build_report(
        project_root,
        min_support_count=args.min_support_count,
        min_confidence=args.min_confidence,
    )
    payload["write_requested"] = bool(args.output) and not bool(args.no_write)
    payload["write_performed"] = False
    payload["output_path"] = None
    payload["cli_reason"] = "no_write_default"

    exit_code = 0
    if args.output and not args.no_write:
        output = _resolve_output_path(project_root, args.output)
        payload["output_path"] = str(output)
        path_error = _validate_output_path(project_root, output)
        if path_error is not None:
            payload["status"] = "blocked"
            payload["reason"] = path_error
            payload["cli_reason"] = path_error
            payload["validation_errors"] = validate_report(payload)
            exit_code = 1
        else:
            payload["cli_reason"] = "explicit_output_written"
            payload["validation_errors"] = validate_report(payload)
            payload["write_performed"] = True
            _atomic_write_json(output, payload)
    else:
        payload["validation_errors"] = validate_report(payload)

    output_text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )
    if args.json_output:
        print(output_text)
    else:
        print(f"DAILY_PATTERN_MINING_RESEARCH_STATUS={payload['status']}")
        print(f"DAILY_PATTERN_MINING_RESEARCH_DECISION={payload['decision']}")
        print(f"DAILY_PATTERN_MINING_RESEARCH_JSON={output_text}")
    return exit_code


def _resolve_output_path(project_root: Path, output: str) -> Path:
    candidate = Path(output).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (project_root / candidate).resolve()


def _validate_output_path(project_root: Path, output: Path) -> str | None:
    restricted = (
        project_root / "data",
        project_root / "runtime",
        project_root / "reports",
        project_root / "logs",
        project_root / "freqtrade",
    )
    try:
        output.relative_to(project_root)
    except ValueError:
        return None
    for directory in restricted:
        try:
            output.relative_to(directory.resolve())
        except ValueError:
            continue
        return "output_path_in_runtime_or_data_scope"
    return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(content + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
