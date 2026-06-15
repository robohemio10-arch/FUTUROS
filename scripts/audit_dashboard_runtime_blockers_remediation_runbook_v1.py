from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.dashboard_snapshots.runtime_blockers_remediation import (  # noqa: E402
    build_runtime_blockers_remediation,
)


REPORT_PATH = Path("data/reports/dashboard_runtime_blockers_remediation_runbook_v1.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit the read-only dashboard runtime blockers remediation runbook."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true", help="Emit the full JSON payload.")
    parser.add_argument(
        "--write-report",
        action="store_true",
        help=f"Write only {REPORT_PATH.as_posix()}.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(args.project_root).resolve()
    summary = _load_mapping(project_root / "data/reports/dashboard_snapshot_build_summary.json")
    global_snapshot = _load_mapping(
        project_root / "data/reports/dashboard_global_status_snapshot.json"
    )
    evidence_view = _mapping(
        global_snapshot.get("runtime_evidence_view")
        or summary.get("runtime_evidence_view")
    )

    payload = build_runtime_blockers_remediation(
        now_utc=datetime.now(timezone.utc),
        dashboard_status=str(
            global_snapshot.get("dashboard_status")
            or summary.get("dashboard_status")
            or "UNKNOWN"
        ),
        global_source_health_status=str(
            global_snapshot.get("global_source_health_status")
            or summary.get("global_source_health_status")
            or "UNKNOWN"
        ),
        runtime_evidence_integration_status=str(
            global_snapshot.get("runtime_evidence_integration_status")
            or summary.get("runtime_evidence_integration_status")
            or "UNKNOWN"
        ),
        global_blocking_reasons=_sequence(
            global_snapshot.get("global_blocking_reasons")
            or summary.get("global_blocking_reasons")
        ),
        runtime_evidence_blocking_reasons=_sequence(
            global_snapshot.get("runtime_evidence_blocking_reasons")
            or summary.get("runtime_evidence_blocking_reasons")
        ),
        source_health_matrix=_sequence(
            global_snapshot.get("source_health_matrix")
            or summary.get("source_health_matrix")
        ),
        runtime_evidence_sources=_sequence(evidence_view.get("evidence_sources")),
    )

    if args.write_report:
        reports_root = (project_root / REPORT_PATH.parent).resolve()
        target = (reports_root / REPORT_PATH.name).resolve()
        if target.parent != reports_root or target.name != REPORT_PATH.name:
            raise ValueError("unauthorized_report_path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"status={payload['status']} reason={payload['reason']} "
            f"blockers_total={payload['blockers_total']}"
        )
    return 2 if payload["status"] == "blocked" else 0


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


if __name__ == "__main__":
    raise SystemExit(main())
