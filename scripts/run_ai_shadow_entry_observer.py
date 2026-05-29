from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback

from smartcrypto.ml.ai_shadow_entry_observer import (
    AIShadowEntryObserverError,
    read_parquet,
    run_ai_shadow_entry_observer,
    utc_now,
    write_json,
    write_jsonl,
)


DEFAULT_FEATURES = Path("data/features/training_dataset_open_decision_clean.parquet")
DEFAULT_MODEL_REPORT = Path("data/reports/model_vs_baseline_financial_evaluation_report.json")
DEFAULT_OUTPUT = Path("data/reports/ai_shadow_entry_observer_report.json")
DEFAULT_DECISIONS_OUTPUT = Path("data/reports/ai_shadow_entry_decisions.jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AI shadow entry observer offline without order submission.")
    parser.add_argument("--features", default=str(DEFAULT_FEATURES))
    parser.add_argument("--model-report", default=str(DEFAULT_MODEL_REPORT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--decisions-output", default=str(DEFAULT_DECISIONS_OUTPUT))
    parser.add_argument("--id-column", default="trade_id")
    parser.add_argument("--symbol-column", default="symbol")
    parser.add_argument("--time-column", default="open_1m_ts")
    parser.add_argument("--target-column", default="target_win")
    parser.add_argument("--probability-threshold", type=float, default=0.60)
    parser.add_argument("--max-rows", type=int, default=500)
    parser.add_argument("--dry-run", type=parse_bool, default=True)
    parser.add_argument("--shadow-only", type=parse_bool, default=True)
    parser.add_argument("--seed", type=int, default=42)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        features = read_parquet(args.features)
        result = run_ai_shadow_entry_observer(
            features,
            features_path=args.features,
            model_report=args.model_report,
            id_column=args.id_column,
            symbol_column=args.symbol_column,
            time_column=args.time_column,
            target_column=args.target_column,
            probability_threshold=args.probability_threshold,
            max_rows=args.max_rows,
            dry_run=args.dry_run,
            shadow_only=args.shadow_only,
            seed=args.seed,
            live_trading_enabled=False,
            order_submission_enabled=False,
            real_order_submission_enabled=False,
            exchange_private_access=False,
        )
        write_json(args.output, result["report"])
        write_jsonl(args.decisions_output, result["decisions"])
    except AIShadowEntryObserverError as exc:
        blocked = {
            "status": "BLOCKED",
            "error": str(exc),
            "runtime_mode": "shadow",
            "shadow_only": True,
            "dry_run": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "recommended_next_action": "block_ai_shadow_entry_observer_until_configuration_is_safe",
            "created_at": utc_now(),
        }
        write_json(args.output, blocked)
        write_jsonl(args.decisions_output, [])
        print(json.dumps(blocked, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        failure = {
            "status": "FAILED",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "runtime_mode": "shadow",
            "shadow_only": True,
            "dry_run": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "created_at": utc_now(),
        }
        write_json(args.output, failure)
        write_jsonl(args.decisions_output, [])
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result["report"], ensure_ascii=False, indent=2))
    return 0


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "sim"}:
        return True
    if normalized in {"0", "false", "no", "n", "nao", "não"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid_bool:{value}")


if __name__ == "__main__":
    raise SystemExit(main())
