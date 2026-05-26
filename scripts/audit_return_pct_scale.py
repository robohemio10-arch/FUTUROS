from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback

from smartcrypto.ml.return_scale_audit import (
    audit_return_scale,
    read_parquet,
    utc_now,
    write_json,
)


DEFAULT_INPUT = Path("data/features/training_dataset.parquet")
DEFAULT_SIDECAR = Path("data/features/training_outcome_sidecar.parquet")
DEFAULT_REPORT = Path("data/reports/return_pct_scale_audit_report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit return_pct scale, cost semantics, leverage and financial outliers.",
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--sidecar", default=str(DEFAULT_SIDECAR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--id-column", default="trade_id")
    parser.add_argument("--return-column", default="return_pct")
    parser.add_argument("--pnl-column", default="pnl")
    parser.add_argument("--entry-price-column", default="entry_price")
    parser.add_argument("--exit-price-column", default="exit_price")
    parser.add_argument("--volume-column", default="volume_posicao")
    parser.add_argument("--leverage-column", default="leverage")
    parser.add_argument("--target-column", default="target_win")
    parser.add_argument("--symbol-column", default="symbol")
    parser.add_argument("--max-abs-return-pct", type=float, default=100.0)
    parser.add_argument("--max-abs-pnl", type=float, default=1_000_000.0)
    parser.add_argument("--sample-outliers", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        input_frame = read_parquet(args.input)
        sidecar_frame = read_parquet(args.sidecar) if Path(args.sidecar).exists() else None
        report = audit_return_scale(
            input_frame,
            sidecar_frame,
            input_path=args.input,
            sidecar_path=args.sidecar if sidecar_frame is not None else None,
            id_column=args.id_column,
            return_column=args.return_column,
            pnl_column=args.pnl_column,
            entry_price_column=args.entry_price_column,
            exit_price_column=args.exit_price_column,
            volume_column=args.volume_column,
            leverage_column=args.leverage_column,
            target_column=args.target_column,
            symbol_column=args.symbol_column,
            max_abs_return_pct=args.max_abs_return_pct,
            max_abs_pnl=args.max_abs_pnl,
            sample_outliers=args.sample_outliers,
        )
        write_json(args.report, report.to_dict())
    except Exception as exc:
        failure = {
            "status": "FAILED",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "runtime_mode": "research",
            "live_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "created_at": utc_now(),
        }
        write_json(args.report, failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
