#!/usr/bin/env python3
"""Opt-in public L1 research observer. Default preflight: no network and no writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.ops.execution_decision_l1.archive import (  # noqa: E402
    ExternalArchive,
    validate_external_root,
)
from smartcrypto.ops.execution_decision_l1.collector import Collector  # noqa: E402
from smartcrypto.ops.execution_decision_l1.kill_switch_authority import (  # noqa: E402
    AuthorityDenied,
    inspect_authority,
)
from smartcrypto.ops.execution_decision_l1.contracts import (  # noqa: E402
    EVENT_ADAPTER,
    SOURCE_URL,
    CollectorConfig,
    schema_sha256,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--ledger-path", type=Path)
    parser.add_argument(
        "--collect",
        action="store_true",
        help="Explicitly permit public collection for this process only.",
    )
    parser.add_argument(
        "--write-archive",
        action="store_true",
        help="Explicitly authorize external archive persistence.",
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--duration-seconds", type=float, default=60)
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--request-timeout-seconds", type=float, default=3)
    parser.add_argument("--queue-capacity", type=int, default=256)
    parser.add_argument("--max-quote-age-seconds", type=float, default=5)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(EVENT_ADAPTER.json_schema(), sort_keys=True, indent=2))
        return 0
    if args.runtime_root is None and args.ledger_path is None:
        parser.error("--runtime-root or --ledger-path is required")
    if args.write_archive and (not args.collect or args.output_root is None):
        parser.error("persistence requires --collect --write-archive --output-root")
    if args.output_root and not args.write_archive:
        parser.error("--output-root does not authorize persistence; use --write-archive")
    ledger = (
        args.ledger_path
        or args.runtime_root / "data/runtime/decision_ledger_paper_v1/decision_ledger_v4_2.jsonl"
    )
    forbidden = (PROJECT_ROOT, args.runtime_root) if args.runtime_root else (PROJECT_ROOT,)
    archive: ExternalArchive | None = None
    try:
        config = CollectorConfig.model_validate(
            {
                "symbols": args.symbols,
                "poll_seconds": args.poll_seconds,
                "request_timeout_seconds": args.request_timeout_seconds,
                "queue_capacity": args.queue_capacity,
                "max_quote_age_seconds": args.max_quote_age_seconds,
            }
        )
        if not 0 < args.duration_seconds <= 86400:
            raise ValueError("invalid_duration")
        if not ledger.is_file() or any(part.is_symlink() for part in (ledger, *ledger.parents)):
            raise ValueError("regular_nonsymlink_ledger_required")
        authority = inspect_authority(args.runtime_root, ledger, config.symbols)
        if args.output_root:
            validate_external_root(args.output_root, forbidden)
        if not args.collect:
            report = {
                "status": "ok",
                "reason": "opt_in_required_no_collection_started",
                "collector_gate": "COLLECTOR_READY_FOR_OPT_IN",
                "execution_readiness": "BLOCKED_MISSING_EXECUTION_EVIDENCE",
                "schema_sha256": schema_sha256(),
                "source": SOURCE_URL,
                "config": config.model_dump(mode="json"),
                "network_calls_executed": False,
                "write_performed": False,
                "runtime_activation_performed": False,
                "canonical_kill_switch": authority,
                "COLLECTOR_KILLSWITCH_ENFORCEMENT": "NOT_RUN",
            }
        else:
            archive = (
                ExternalArchive(
                    args.output_root,
                    authorized=True,
                    forbidden_roots=forbidden,
                    segment_records=config.segment_records,
                    max_bytes=config.max_archive_bytes,
                )
                if args.write_archive
                else None
            )
            report = Collector(ledger, config, runtime_root=args.runtime_root, archive=archive).run(
                args.duration_seconds
            )
    except (OSError, ValueError, ValidationError, KeyboardInterrupt) as exc:
        report = {
            "status": "blocked",
            "reason": exc.reason
            if isinstance(exc, AuthorityDenied)
            else f"collector_preflight_{type(exc).__name__}",
            "collector_gate": "BLOCKED_COLLECTOR_PREFLIGHT",
            "execution_readiness": "BLOCKED_MISSING_EXECUTION_EVIDENCE",
            "write_performed": archive is not None and archive.write_performed,
            "network_calls_executed": False,
            "COLLECTOR_KILLSWITCH_ENFORCEMENT": "BLOCKED",
        }
    print(json.dumps(report, sort_keys=True, allow_nan=False, indent=None if args.json else 2))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
