#!/usr/bin/env python3
"""Build Branch 16 Research Council economic uplift A/B evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.research_council.alpha_uplift_ab import (  # noqa: E402
    build_research_council_alpha_uplift_ab_v1,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--project-root", default=".")
    value.add_argument("--master", required=True)
    value.add_argument("--dataset", required=True)
    value.add_argument("--feature-contract", required=True)
    value.add_argument("--dataset-manifest", required=True)
    value.add_argument("--split-manifest", required=True)
    value.add_argument("--shadow-evidence", default=None)
    value.add_argument("--council-ab-evidence", default=None)
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = build_research_council_alpha_uplift_ab_v1(
        project_root=args.project_root,
        master_path=args.master,
        dataset_path=args.dataset,
        feature_contract_path=args.feature_contract,
        dataset_manifest_path=args.dataset_manifest,
        split_manifest_path=args.split_manifest,
        shadow_evidence_path=args.shadow_evidence,
        council_ab_evidence_path=args.council_ab_evidence,
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
