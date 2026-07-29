"""Audit shared runtime report writers without importing audited modules."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartcrypto.runtime.integrity_traceability_v2 import (
    AtomicWriteError,
    AtomicWritePolicy,
    atomic_write_json,
)
from smartcrypto.runtime.integrity_traceability_v2.writer_audit import (
    audit_runtime_shared_report_writers,
)

DEFAULT_REPORT_PATH = (
    "data/reports/runtime_shared_report_writer_audit_v2.json"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Statically audit shared paper/shadow report writers."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.project_root).resolve(strict=False)
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = (root / report_path).resolve(strict=False)

    report = audit_runtime_shared_report_writers(root)
    report.update(
        {
            "report_path": str(report_path),
            "write_requested": bool(args.write_report),
            "write_performed": bool(args.write_report),
            "writes_runtime": False,
        }
    )
    if args.write_report:
        try:
            atomic_write_json(
                report_path,
                report,
                policy=AtomicWritePolicy.restricted(
                    (root / "data",),
                    working_directory=root,
                ),
            )
            report["write_performed"] = True
        except AtomicWriteError as exc:
            report.update(
                {
                    "status": "blocked",
                    "reason": f"writer_audit_report_write_failed:{exc.reason}",
                    "write_performed": False,
                }
            )

    print(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.json
        else f"{report['status']}:{report['reason']}"
    )
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
