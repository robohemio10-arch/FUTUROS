"""No-write coverage auditor. Never starts or retroactively reconstructs capture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal, TypedDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.pretrade_causal_capture.archive import (  # noqa: E402
    CoverageReport,
    EvidenceArchive,
    coverage,
)
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import AtomicWriteError  # noqa: E402


class BlockedReport(TypedDict):
    status: Literal["blocked"]
    reason: str
    write_performed: Literal[False]
    operational_authority: Literal[False]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.archive_root is None:
        report: BlockedReport | CoverageReport = {
            "status": "blocked",
            "reason": "explicit_archive_not_supplied",
            "write_performed": False,
            "operational_authority": False,
        }
    else:
        try:
            archive = EvidenceArchive(args.archive_root, forbidden_roots=(args.project_root,))
            report = coverage(archive.read())
        except (OSError, ValueError, AtomicWriteError):
            report = {
                "status": "blocked",
                "reason": "archive_unreadable_or_invalid",
                "write_performed": False,
                "operational_authority": False,
            }
    print(json.dumps(report, sort_keys=True, allow_nan=False, indent=None if args.json else 2))
    return 0 if report["status"] == "structural_only" else 2


if __name__ == "__main__":
    raise SystemExit(main())
