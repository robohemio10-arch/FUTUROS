#!/usr/bin/env python3
"""Legacy-safe wrapper for the V5 quality-gated research projection.

The former entrypoint wrote candidate and full-audit Parquets before completing
validation. That behavior is intentionally disabled. This wrapper delegates to
the projection-only V5 contract and can write only research reports under
``data/reports`` when explicitly requested.

``detect_ocr_rows`` remains available as a read-only compatibility API for
legacy Phase 5 source-alignment tests and callers. It delegates provenance
classification to the exact, versioned V5 contract; it performs no broad
substring matching, model loading, dataset construction, or persistence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.quality_gated_v5_contract import (  # noqa: E402
    build_quality_gated_v5_contract_report,
)
from smartcrypto.learning.quality_gated_v5_contract.provenance import (  # noqa: E402
    classify_provenance_frame,
)


def detect_ocr_rows(frame: pd.DataFrame) -> pd.Series:
    """Return rows matching an exact, versioned OCR provenance contract.

    Historical rows and blocked partial, ambiguous, or unknown provenance rows
    return ``False``. The returned boolean Series preserves the input index and
    the function never mutates ``frame``.
    """

    if frame.empty:
        return pd.Series(False, index=frame.index, dtype=bool)

    classified = classify_provenance_frame(frame)
    return (
        classified["provenance_status"].eq("ok")
        & classified["provenance_contract"].ne("historical")
    ).astype(bool)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_quality_gated_v5_contract_report(
        project_root=args.project_root,
        write_report=bool(args.write_report and not args.no_write),
    )
    report["legacy_entrypoint"] = True
    report["legacy_candidate_write_disabled"] = True
    report["writes_candidate_dataset"] = False
    report["writes_full_audit_dataset"] = False
    report["writes_official_dataset"] = False

    printable = {key: value for key, value in report.items() if key != "row_records"}
    print(
        json.dumps(
            printable,
            sort_keys=True,
            ensure_ascii=False,
            indent=None if args.json else 2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
