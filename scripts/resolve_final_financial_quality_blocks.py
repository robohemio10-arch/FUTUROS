from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback

from smartcrypto.ml.final_financial_quality_resolution import (
    read_parquet,
    resolve_final_financial_quality_blocks,
    utc_now,
    write_json,
    write_outputs,
)


DEFAULT_INPUT = Path("data/features/trade_financial_consistency_repaired.parquet")
DEFAULT_OUTPUT = Path("data/features/trade_final_financial_quality_resolved.parquet")
DEFAULT_REPORT = Path("data/reports/final_financial_quality_resolution_report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve final financial quality blocks for offline research only.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--id-column", default="trade_id")
    parser.add_argument("--symbol-column", default="symbol")
    parser.add_argument("--target-column", default="target_win")
    parser.add_argument("--time-column", default="open_1m_ts")
    parser.add_argument("--entry-price-column", default="entry_price_repaired")
    parser.add_argument("--exit-price-column", default="exit_price_repaired")
    parser.add_argument("--side-column", default="side_repaired")
    parser.add_argument("--volume-column", default="volume_repaired")
    parser.add_argument("--leverage-column", default="leverage_consistent")
    parser.add_argument("--leverage-original-column", default="leverage_original")
    parser.add_argument("--raw-return-column", default="raw_return_consistent")
    parser.add_argument("--pnl-column", default="pnl_consistent")
    parser.add_argument("--max-leverage", type=float, default=125.0)
    parser.add_argument("--max-abs-price-return-pct", type=float, default=20.0)
    parser.add_argument("--max-abs-net-return-pct", type=float, default=100.0)
    parser.add_argument("--raw-return-warning-threshold", type=float, default=5.0)
    parser.add_argument("--sample-rows", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        frame = read_parquet(args.input)
        resolved, report = resolve_final_financial_quality_blocks(
            frame,
            output_path=args.output,
            id_column=args.id_column,
            symbol_column=args.symbol_column,
            target_column=args.target_column,
            time_column=args.time_column,
            entry_price_column=args.entry_price_column,
            exit_price_column=args.exit_price_column,
            side_column=args.side_column,
            volume_column=args.volume_column,
            leverage_column=args.leverage_column,
            leverage_original_column=args.leverage_original_column,
            raw_return_column=args.raw_return_column,
            pnl_column=args.pnl_column,
            max_leverage=args.max_leverage,
            max_abs_price_return_pct=args.max_abs_price_return_pct,
            max_abs_net_return_pct=args.max_abs_net_return_pct,
            raw_return_warning_threshold=args.raw_return_warning_threshold,
            sample_rows=args.sample_rows,
        )
        write_outputs(
            resolved=resolved,
            report=report,
            output_path=args.output,
            report_path=args.report,
        )
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
