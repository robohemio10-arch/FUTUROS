#!/usr/bin/env python3
"""Read-only host preflight; public connectivity requires explicit opt-in."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument(
        "--diagnose-public-connectivity",
        action="store_true",
        help="Permit one public GET with bounded timeout; never start collection or archive the message.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        from smartcrypto.ops.execution_decision_l1.deployment_preflight import audit_deployment

        report = audit_deployment(
            project_root=args.project_root,
            runtime_root=args.runtime_root,
            archive_root=args.archive_root,
            diagnose_public_connectivity=args.diagnose_public_connectivity,
        )
    except ImportError as exc:
        report = {
            "status": "blocked",
            "decision": "BLOCKED_PREFLIGHT_FAILURE",
            "reason": f"critical_dependency_{type(exc).__name__}",
            "manual_activation_allowed": False,
            "write_performed": False,
            "network_calls_executed": False,
            "collector_initialized": False,
            "collection_started": False,
            "EXECUTION_READINESS": "BLOCKED_MISSING_EXECUTION_EVIDENCE",
        }
    print(json.dumps(report, sort_keys=True, allow_nan=False, indent=None if args.json else 2))
    return 0 if report["manual_activation_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
