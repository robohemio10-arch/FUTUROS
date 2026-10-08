#!/usr/bin/env python3
"""Run the BR10 chronological capacity replay without writing reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.aibot_parity.br10_capacity_constrained_oos_redeployment import (  # noqa: E402
    build_br10_capacity_constrained_oos_redeployment_v1,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--project-root", default=".")
    value.add_argument("--config", default="config/research/portfolio_allocator.yaml")
    value.add_argument("--capital-per-candidate-usdt", type=float, default=None)
    value.add_argument("--dataset", default=None)
    value.add_argument("--feature-contract", default=None)
    value.add_argument("--dataset-manifest", default=None)
    value.add_argument("--split-manifest", default=None)
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = build_br10_capacity_constrained_oos_redeployment_v1(
        project_root=args.project_root,
        config_path=args.config,
        capital_per_candidate_usdt=args.capital_per_candidate_usdt,
        dataset_path=args.dataset,
        feature_contract_path=args.feature_contract,
        dataset_manifest_path=args.dataset_manifest,
        split_manifest_path=args.split_manifest,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":") if args.json else None,
            indent=None if args.json else 2,
        )
    )
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
