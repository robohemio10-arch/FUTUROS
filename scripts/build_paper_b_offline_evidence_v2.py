#!/usr/bin/env python3
"""Evaluate explicit local V2 research evidence and emit JSON to stdout only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.qlib_v3_prospective.activation import read_object
from smartcrypto.research.canonical_treatment.offline_evidence_readers_v2 import (
    READERS,
    SAFETY,
    build_offline_evidence_reader_v2,
)
from smartcrypto.research.canonical_treatment.storage_cohort_contract_v2 import (
    ContractV2Error,
    load_descriptor,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", required=True, type=Path)
    parser.add_argument("--predecessor", required=True, type=Path)
    parser.add_argument("--fingerprints", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--reader", required=True, choices=READERS)
    args = parser.parse_args(argv)
    try:
        fingerprints = read_object(args.fingerprints)
        observation_document = read_object(args.observations)
        if set(observation_document) != {"observations"} or not isinstance(
            observation_document["observations"], list
        ):
            raise ValueError("explicit_observations_document_required")
        descriptor = load_descriptor(
            args.descriptor,
            predecessor_path=args.predecessor,
            known_fingerprints=fingerprints,
        )
        report = build_offline_evidence_reader_v2(
            descriptor=descriptor,
            reader=args.reader,
            known_fingerprints=fingerprints,
            observations=observation_document["observations"],
        )
    except (ContractV2Error, OSError, ValueError, TypeError, KeyError) as exc:
        report = {
            "status": "blocked",
            "reason": str(exc) if isinstance(exc, ContractV2Error) else "offline_inputs_invalid:" + type(exc).__name__,
            "reader": args.reader,
            **SAFETY,
        }
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 2 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
