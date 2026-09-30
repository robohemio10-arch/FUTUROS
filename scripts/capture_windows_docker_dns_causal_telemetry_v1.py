#!/usr/bin/env python3
"""Capture non-authoritative DNS evidence from a Windows host and one exact container."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.ops.dns_causal_telemetry import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    SystemProbes,
    collect_sample,
    persist_sample,
)
from smartcrypto.ops.dns_causal_telemetry.collector import sanitize_error  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Capture one sample (the default).")
    mode.add_argument("--interval-seconds", type=float, help="Repeat until interrupted.")
    parser.add_argument("--container", help="Exact running container name; no fuzzy discovery.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not 0 < args.timeout_seconds <= 30:
        parser.error("--timeout-seconds must be in (0, 30]")
    if args.interval_seconds is not None and not 1 <= args.interval_seconds <= 86400:
        parser.error("--interval-seconds must be in [1, 86400]")

    probes = SystemProbes()
    try:
        while True:
            sample = collect_sample(
                probes, container_name=args.container, timeout_seconds=args.timeout_seconds
            )
            jsonl_path, snapshot_path = persist_sample(args.output_dir, sample)
            output = {
                "sampled_at_utc": sample["sampled_at_utc"],
                "classification": sample["classification"],
                "classification_is_root_cause": False,
                "jsonl_path": str(jsonl_path),
                "snapshot_path": str(snapshot_path),
            }
            print(json.dumps(output, sort_keys=True) if args.json else output, flush=True)
            if args.interval_seconds is None:
                return 0
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "reason": sanitize_error(str(exc))}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
