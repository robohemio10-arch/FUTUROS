from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build SMART FUTUROS read-only dashboard snapshots.")
    parser.add_argument("--once", action="store_true", help="Build one snapshot round and exit.")
    parser.add_argument("--strict", type=parse_bool, default=False)
    parser.add_argument("--output-dir", default="data/reports")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
    from smartcrypto.ops.dashboard_snapshots.builder_registry import build_all_dashboard_snapshots

    args = build_parser().parse_args(argv)
    context = create_dashboard_build_context(
        args.project_root,
        output_dir=args.output_dir,
        runtime_mode="paper",
        strict=args.strict,
        allow_writes_to_output_dir=True,
    )
    result = build_all_dashboard_snapshots(context)
    summary = result["summary"]
    if args.json_output:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
