#!/usr/bin/env python3
"""Audit execution evidence offline; no write, daemon, collector or network mode."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.execution_intelligence.evidence_readiness import (  # noqa: E402
    EvidencePacket,
    build_execution_evidence_readiness,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument(
        "--source-profile",
        type=Path,
        default=PROJECT_ROOT / "config/freqtrade_paper_closed_trades_source_profile_v2.json",
    )
    parser.add_argument("--execution-evidence", type=Path)
    parser.add_argument("--execution-evidence-sha256")
    parser.add_argument(
        "--as-of-utc", help="Explicit UTC for reproducible observation; default now."
    )
    parser.add_argument("--max-source-age-seconds", type=float, default=300)
    parser.add_argument("--max-quote-age-seconds", type=float, default=5)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(EvidencePacket.model_json_schema(), sort_keys=True, indent=2))
        return 0
    if args.runtime_root is None:
        parser.error("--runtime-root is required for the read-only audit")
    if args.execution_evidence and not args.execution_evidence_sha256:
        parser.error("--execution-evidence requires its independently sealed SHA256")
    try:
        as_of = (
            datetime.fromisoformat(args.as_of_utc.replace("Z", "+00:00"))
            if args.as_of_utc
            else datetime.now(timezone.utc)
        )
        report = build_execution_evidence_readiness(
            runtime_root=args.runtime_root,
            profile_path=args.source_profile,
            as_of_utc=as_of,
            evidence_path=args.execution_evidence,
            evidence_sha256=args.execution_evidence_sha256,
            max_source_age_seconds=args.max_source_age_seconds,
            max_quote_age_seconds=args.max_quote_age_seconds,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, sort_keys=True, allow_nan=False, indent=None if args.json else 2))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
