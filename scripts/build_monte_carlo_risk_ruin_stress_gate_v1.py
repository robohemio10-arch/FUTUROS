#!/usr/bin/env python3
"""Build the research-only Monte Carlo risk-of-ruin stress gate."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.risk.monte_carlo_risk_ruin_stress_gate import (  # noqa: E402
    build_monte_carlo_risk_ruin_stress_gate_v1,
)
from smartcrypto.risk.monte_carlo_risk_ruin_stress_gate.gate import (  # noqa: E402
    json_safe,
    render_markdown,
)


DEFAULT_REPORT_JSON = Path("data/reports/monte_carlo_risk_ruin_stress_gate_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/monte_carlo_risk_ruin_stress_gate_v1.md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    parser.add_argument("--write", action="store_true", help="Write JSON/Markdown under data/reports.")
    parser.add_argument("--no-write", action="store_true", help="Force no-write mode. This is the default.")
    parser.add_argument("--report-json", default=None, help="Optional JSON report path.")
    parser.add_argument("--report-markdown", default=None, help="Optional Markdown report path.")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--simulation-count", type=int, default=1000)
    parser.add_argument("--sample-size", type=int, default=None)
    parser.add_argument("--initial-capital", type=float, default=100.0)
    parser.add_argument("--capital-floor", type=float, default=70.0)
    parser.add_argument("--ruin-floor", type=float, default=50.0)
    parser.add_argument("--cost-per-trade", type=float, default=0.02)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    project_root = Path(args.project_root).resolve()
    write_requested = bool(args.write and not args.no_write)

    report_json = resolve_report_path(project_root, args.report_json, DEFAULT_REPORT_JSON)
    report_markdown = resolve_report_path(project_root, args.report_markdown, DEFAULT_REPORT_MD)

    report = build_monte_carlo_risk_ruin_stress_gate_v1(
        project_root=project_root,
        write=write_requested,
        report_json_path=report_json,
        report_markdown_path=report_markdown,
        seed=args.seed,
        simulation_count=args.simulation_count,
        sample_size=args.sample_size,
        initial_capital=args.initial_capital,
        capital_floor=args.capital_floor,
        ruin_floor=args.ruin_floor,
        cost_per_trade=args.cost_per_trade,
    )

    if write_requested:
        write_reports(report, report_json, report_markdown)
        report["write_performed"] = True
    else:
        report["write_performed"] = False

    report["write_requested"] = write_requested
    report["output_paths"] = {
        "json": str(report_json),
        "markdown": str(report_markdown),
    }

    if write_requested:
        write_reports(report, report_json, report_markdown)

    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=json_safe))
    else:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe))

    return 0


def resolve_report_path(project_root: Path, value: str | None, default: Path) -> Path:
    path = Path(value) if value else default
    return path if path.is_absolute() else project_root / path


def write_reports(report: Mapping[str, Any], report_json: Path, report_markdown: Path) -> None:
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_markdown.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_json, report)
    report_markdown.write_text(render_markdown(report), encoding="utf-8")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())