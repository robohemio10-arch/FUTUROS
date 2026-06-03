from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smartcrypto.ml.drift_monitor import DEFAULT_BASELINE_PATH, build_ai_shadow_drift_baseline


DEFAULT_INPUT_PATH = Path("data/features/incremental_training_microbatch.parquet")
DEFAULT_CONTRACT_PATH = Path("data/models/shadow/ai_shadow_feature_contract.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build IA Shadow drift baseline.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_BASELINE_PATH)
    parser.add_argument("--feature-prefix", default="feature_")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    contract_path = args.contract if args.contract.exists() else None
    report = build_ai_shadow_drift_baseline(
        input_path=args.input,
        contract_path=contract_path,
        output_path=args.output,
        feature_prefix=args.feature_prefix,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
