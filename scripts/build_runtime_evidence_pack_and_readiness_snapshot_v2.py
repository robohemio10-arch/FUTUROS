from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.runtime_evidence_pack import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    build_runtime_evidence_pack_and_readiness_snapshot_v2,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build runtime evidence pack v2 and readiness snapshot v2.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print controlled JSON summary.")
    parser.add_argument(
        "--include-containers",
        action="store_true",
        help="Include best-effort docker ps container snapshot. Safe and read-only.",
    )
    parser.add_argument("--container-timeout-seconds", type=float, default=3.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_runtime_evidence_pack_and_readiness_snapshot_v2(
        project_root=args.project_root,
        output_dir=args.output_dir,
        no_write=args.no_write,
        include_containers=bool(args.include_containers),
        container_timeout_seconds=float(args.container_timeout_seconds),
    )
    runtime = result.evidence_pack.get("runtime_observability", {})
    containers = result.evidence_pack.get("container_snapshot", {})
    summary = {
        "status": result.readiness_snapshot["status"],
        "reason": result.readiness_snapshot["reason"],
        "evidence_pack_status": result.evidence_pack["status"],
        "readiness_snapshot_status": result.readiness_snapshot["status"],
        "runtime_observability_status": runtime.get("status"),
        "runtime_observability_reason": runtime.get("reason"),
        "container_snapshot_status": containers.get("status"),
        "container_snapshot_reason": containers.get("reason"),
        "runtime_evidence_pack_path": str(result.evidence_pack_path),
        "readiness_snapshot_path": str(result.readiness_snapshot_path),
        "write_performed": result.write_performed,
        "missing_evidence": result.readiness_snapshot["missing_evidence"],
        "blocking_reasons": result.readiness_snapshot["blocking_reasons"],
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_training_dataset": False,
        "writes_trades_master": False,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{summary['status']}: {summary['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
