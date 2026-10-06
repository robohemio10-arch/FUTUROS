#!/usr/bin/env python3
"""Audit or create the Paper B causal Epoch 2 foundation after proven runtime drift."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.canonical_treatment.causal_epoch2_foundation import (  # noqa: E402
    DEFAULT_REGISTRATION_RELATIVE_PATH,
    DEFAULT_REPORT_RELATIVE_PATH,
    run_foundation,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--project-root", default=str(PROJECT_ROOT))
    value.add_argument("--runtime-root", required=True)
    value.add_argument("--epoch1-project-root", required=True)
    value.add_argument("--epoch1-baseline", required=True)
    value.add_argument("--evidence-root", required=True)
    value.add_argument("--registration", default=str(DEFAULT_REGISTRATION_RELATIVE_PATH))
    value.add_argument("--report", default=str(DEFAULT_REPORT_RELATIVE_PATH))
    value.add_argument("--register-epoch2", action="store_true")
    value.add_argument("--write-report", action="store_true")
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = run_foundation(
        project_root=args.project_root,
        runtime_root=args.runtime_root,
        epoch1_project_root=args.epoch1_project_root,
        epoch1_baseline_path=args.epoch1_baseline,
        evidence_root=args.evidence_root,
        registration_path=args.registration,
        report_path=args.report,
        register_epoch2=bool(args.register_epoch2),
        write_report=bool(args.write_report),
    )
    print(
        json.dumps(
            report,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            indent=None if args.json else 2,
        )
    )
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
