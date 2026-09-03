#!/usr/bin/env python3
"""Run prospective AIBOT Parity Paper A/B + soak evidence accounting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.research.aibot_parity_paper_ab_soak import (
    evaluate_prospective_ab_soak,
    load_preregistration,
)
from smartcrypto.research.paper_ab_edge_selector.persistence import (
    resolve_assignments_path,
    resolve_report_path,
    write_assignments_idempotent,
    write_report,
)

DEFAULT_CONFIG = Path("config/research/aibot_parity_paper_ab_soak_v1.json")
DEFAULT_REPORT = Path("data/reports/aibot_parity/aibot_parity_paper_ab_soak_v1.json")
DEFAULT_ASSIGNMENTS = Path(
    "data/reports/aibot_parity/aibot_parity_paper_ab_soak_assignments_v1.jsonl"
)


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _load_candidate_rows(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        rows = payload.get("candidate_rows", [])
    else:
        raise ValueError("input_json_must_be_list_or_object")
    if not isinstance(rows, list):
        raise ValueError("candidate_rows_must_be_list")
    if not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("candidate_rows_must_contain_objects")
    return [dict(row) for row in rows]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure preregistered prospective Control x AIBOT shadow Treatment "
            "evidence without changing Paper behavior."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--config-json", default=str(DEFAULT_CONFIG))
    parser.add_argument(
        "--input-json",
        default=None,
        help="Explicit normalized prospective candidate rows; omitted means collection has no rows yet.",
    )
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--write-assignments", action="store_true")
    parser.add_argument("--output-report", default=str(DEFAULT_REPORT))
    parser.add_argument("--output-assignments", default=str(DEFAULT_ASSIGNMENTS))
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.project_root).resolve()
    config_path = _resolve(root, args.config_json)
    input_path = _resolve(root, args.input_json) if args.input_json else None

    try:
        preregistration = load_preregistration(config_path)
        candidate_rows = _load_candidate_rows(input_path)
        report, assignments = evaluate_prospective_ab_soak(
            preregistration,
            candidate_rows,
        )
        report_path = resolve_report_path(root, args.output_report)
        assignments_path = resolve_assignments_path(root, args.output_assignments)
        report["sources"] = {
            "preregistration": str(config_path),
            "prospective_input": str(input_path) if input_path is not None else None,
        }
        report["output_report"] = str(report_path)
        report["output_assignments"] = str(assignments_path)
        report["write_requested"] = bool(
            args.write_report or args.write_assignments
        )

        if args.write_assignments:
            appended = write_assignments_idempotent(root, assignments_path, assignments)
            report["assignments_appended"] = int(appended)
            report["write_assignments_performed"] = bool(appended > 0)
            report["write_performed"] = bool(appended > 0)
        if args.write_report:
            report["write_report_performed"] = True
            report["write_performed"] = True
            write_report(root, report_path, report)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        report = {
            "schema_version": "aibot_parity_paper_ab_soak_v1",
            "status": "blocked",
            "reason": f"controlled_input_failure:{type(exc).__name__}",
            "detail": str(exc)[:512],
            "decision": "COLLECT_PROSPECTIVE_EVIDENCE",
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "traffic_split_performed": False,
            "paper_behavior_changed": False,
            "writes_active_signals": False,
            "signal_published": False,
            "sends_orders": False,
            "changes_risk": False,
            "changes_model": False,
            "exchange_private_access": False,
            "paper_treatment_release_allowed": False,
            "paper_activation_performed": False,
            "qlib_security_gate_bypassed": False,
            "write_requested": bool(args.write_report or args.write_assignments),
            "write_performed": False,
        }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    else:
        print(
            "status={status} reason={reason} evidence={evidence} release_allowed={release}".format(
                status=report.get("status"),
                reason=report.get("reason"),
                evidence=(report.get("financial_evidence") or {}).get("status"),
                release=report.get("paper_treatment_release_allowed"),
            )
        )
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
