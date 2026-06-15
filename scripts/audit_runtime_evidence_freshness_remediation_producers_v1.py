from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.dashboard_snapshots.runtime_evidence_freshness_remediation_producers import (  # noqa: E402
    audit_runtime_evidence_freshness_remediation_producers,
    load_freshness_producer_audit_inputs,
)


REPORT_PATH = Path(
    "data/reports/runtime_evidence_freshness_remediation_producers_audit_v1.json"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit which external producers must refresh runtime freshness evidence."
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
    payload = audit_runtime_evidence_freshness_remediation_producers(
        now_utc=datetime.now(timezone.utc),
        **load_freshness_producer_audit_inputs(project_root),
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
            f"freshness_blockers_total={payload['freshness_blockers_total']}"
        )
    return 2 if payload["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
