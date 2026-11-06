#!/usr/bin/env python3
"""Build the Branch 17 final forward economic proof, read-only."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.aibot_parity.economic_phase_final_forward_proof import (
    build_economic_phase_final_forward_proof_v1,
)
from smartcrypto.learning.qlib_v3_prospective.activation import parse_json, read_bytes
from smartcrypto.learning.qlib_v3_prospective.contracts import EvidenceError


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--project-root", default=".")
    value.add_argument("--runtime-root")
    value.add_argument("--v2-descriptor")
    value.add_argument("--v2-predecessor-registration")
    value.add_argument("--v2-fingerprints")
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    v2_paths = (args.v2_descriptor, args.v2_predecessor_registration, args.v2_fingerprints)
    v2_context = {}
    if any(path is not None for path in v2_paths):
        if any(path is None for path in v2_paths):
            arguments.error("V2 requires descriptor, predecessor registration and fingerprints")
        try:
            descriptor_raw, predecessor_raw, fingerprints_raw = (
                read_bytes(Path(path)) for path in v2_paths
            )
            descriptor, predecessor, fingerprints = (
                parse_json(raw) for raw in (descriptor_raw, predecessor_raw, fingerprints_raw)
            )
            if not all(isinstance(value, dict) for value in (descriptor, predecessor, fingerprints)):
                raise ValueError("V2 inputs must be JSON objects")
        except (EvidenceError, OSError, ValueError) as exc:
            arguments.error("V2 input read failed: " + type(exc).__name__)
        v2_context = {
            "v2_descriptor": descriptor,
            "v2_predecessor_registration": predecessor,
            "v2_predecessor_raw_sha256": hashlib.sha256(predecessor_raw).hexdigest(),
            "v2_known_fingerprints": fingerprints,
        }
    elif args.runtime_root is None:
        arguments.error("--runtime-root is required for historical V1 collection")
    report = build_economic_phase_final_forward_proof_v1(
        project_root=args.project_root,
        runtime_root=args.runtime_root or args.project_root,
        **v2_context,
    )
    print(
        json.dumps(
            report,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            default=str,
            separators=(",", ":") if args.json else None,
            indent=None if args.json else 2,
        )
    )
    return 0 if report.get("status") in {"ok", "waiting"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
