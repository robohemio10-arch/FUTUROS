from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smartcrypto.ml.feature_contract import (
    DEFAULT_OUTPUT_PATH,
    FeatureContractError,
    build_ai_shadow_feature_contract_from_parquet,
    utc_timestamp,
)


DEFAULT_INPUT_PATH = Path("data/features/incremental_training_microbatch.parquet")


def safety_payload() -> dict[str, Any]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    try:
        contract = build_ai_shadow_feature_contract_from_parquet(
            args.input,
            output_path=args.output,
            feature_prefix=args.feature_prefix,
            source_model_id=args.model_id,
            source_model_version=args.model_version,
            strict=args.strict,
        )
    except (FeatureContractError, FileNotFoundError, ValueError) as exc:
        return {
            "status": "blocked",
            "reason": str(exc),
            "input_path": str(args.input),
            "output_path": str(args.output),
            "contract_id": None,
            "contract_version": None,
            "feature_count": 0,
            "feature_columns": [],
            "write_performed": False,
            "created_at_utc": utc_timestamp(),
            **safety_payload(),
        }

    return {
        "status": "ok",
        "reason": "ok",
        "input_path": str(args.input),
        "output_path": str(args.output),
        "contract_id": contract.contract_id,
        "contract_version": contract.contract_version,
        "feature_count": contract.feature_count,
        "feature_columns": list(contract.feature_columns),
        "feature_order_hash": contract.feature_order_hash,
        "schema_hash": contract.schema_hash,
        "write_performed": True,
        "created_at_utc": contract.created_at_utc,
        **contract.safety_flags(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build IA Shadow feature contract.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--feature-prefix", default="feature_")
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
