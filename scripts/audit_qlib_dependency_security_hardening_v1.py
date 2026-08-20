\
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.learning.qlib_dependency_security_hardening import (  # noqa: E402
    DEFAULT_POLICY_PATH,
    DEFAULT_REPORT_PATH,
    audit_project,
    write_report_atomic,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the research-only Qlib dependency security contract without "
            "installing packages, accessing external services, or mutating runtime."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--resolver-report")
    parser.add_argument("--pip-audit-report")
    parser.add_argument(
        "--write-report",
        nargs="?",
        const=str(DEFAULT_REPORT_PATH),
        help=(
            "Optionally write the audit JSON under data/reports. "
            "Default execution is no-write."
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.project_root).resolve()
    policy = Path(args.policy)
    resolver_report = Path(args.resolver_report) if args.resolver_report else None
    pip_audit_report = Path(args.pip_audit_report) if args.pip_audit_report else None

    report = audit_project(
        root,
        policy_path=policy,
        resolver_report=resolver_report,
        pip_audit_report=pip_audit_report,
    )

    if args.write_report:
        destination = Path(args.write_report)
        persisted_report = {
            **report,
            "write_requested": True,
            "write_performed": True,
            "report_path": destination.as_posix(),
        }
        try:
            target = write_report_atomic(root, destination, persisted_report)
            report = {
                **persisted_report,
                "report_path": target.relative_to(root).as_posix(),
            }
        except (OSError, ValueError) as exc:
            report = {
                **report,
                "status": "blocked",
                "reason": "report_write_failed",
                "write_requested": True,
                "write_performed": False,
                "report_path": None,
                "write_error": str(exc),
            }
    else:
        report = {
            **report,
            "write_requested": False,
            "write_performed": False,
            "report_path": None,
        }

    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    else:
        print(
            f"status={report['status']} "
            f"reason={report['reason']} "
            f"decision={report['decision']}"
        )

    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
