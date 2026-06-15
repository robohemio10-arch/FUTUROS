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

from smartcrypto.ops.dashboard_snapshots.runtime_blockers_operator_pack import (  # noqa: E402
    build_runtime_blockers_operator_pack,
)


REPORT_PATH = Path("data/reports/dashboard_runtime_blockers_operator_pack_v1.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the read-only dashboard runtime blockers operator pack."
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
    remediation = _load_remediation(project_root)
    payload = build_runtime_blockers_operator_pack(
        remediation=remediation,
        now_utc=datetime.now(timezone.utc),
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


def _load_remediation(project_root: Path) -> dict[str, Any]:
    for filename in (
        "dashboard_global_status_snapshot.json",
        "dashboard_snapshot_build_summary.json",
        "dashboard_runtime_blockers_remediation_runbook_v1.json",
    ):
        payload = _load_mapping(project_root / "data/reports" / filename)
        remediation = payload.get("runtime_blockers_remediation")
        if isinstance(remediation, dict):
            return dict(remediation)
        if payload.get("schema_version") == "dashboard_runtime_blockers_remediation_runbook_v1":
            return payload
    return {}


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
