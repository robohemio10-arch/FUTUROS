#!/usr/bin/env python3
"""Build the research-only paper model candidate registry gate report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.paper_model_candidate_registry_gate.registry_gate import (  # noqa: E402
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MD,
    build_paper_model_candidate_registry_gate_v1,
    render_markdown,
    resolve,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    parser.add_argument("--write", action="store_true", help="Write JSON/Markdown under data/reports.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode. This is the default.")
    parser.add_argument("--output-json", default=None, help="Optional JSON report path.")
    parser.add_argument("--output-markdown", default=None, help="Optional Markdown report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.project_root).resolve()
    write_requested = bool(args.write and not args.no_write)
    report = build_paper_model_candidate_registry_gate_v1(
        project_root=root,
        write=write_requested,
        output_json_path=args.output_json,
        output_markdown_path=args.output_markdown,
    )
    output_json = resolve(root, args.output_json, DEFAULT_REPORT_JSON)
    output_markdown = resolve(root, args.output_markdown, DEFAULT_REPORT_MD)
    report["output_paths"] = {"json": str(output_json), "markdown": str(output_markdown)}
    if write_requested:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        report["write_performed"] = True
        write_json(output_json, report)
        output_markdown.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
