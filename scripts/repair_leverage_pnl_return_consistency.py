from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback

from smartcrypto.ml.leverage_pnl_return_consistency import (
    read_parquet,
    repair_leverage_pnl_return_consistency,
    utc_now,
    write_json,
    write_outputs,
)


DEFAULT_INPUT = Path("data/features/trade_financial_inputs_repaired.parquet")
DEFAULT_OUTPUT = Path("data/features/trade_financial_consistency_repaired.parquet")
DEFAULT_REPORT = Path("data/reports/leverage_pnl_return_consistency_report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair leverage/PnL/return consistency for offline research only.")
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
    parser.add_argument("--leverage-column", default="leverage_repaired")
    parser.add_argument("--raw-return-column", default="raw_return_repaired")
    parser.add_argument("--pnl-column", default="pnl_repaired")
    parser.add_argument("--original-pnl-column", default="pnl_fechado")
    parser.add_argument("--original-return-column", default="taxa_lucros_perdas_fechados_pct")
    parser.add_argument("--max-leverage", type=float, default=125.0)
    parser.add_argument("--default-leverage-policy", default="block")
    parser.add_argument("--raw-return-discrepancy-threshold", type=float, default=5.0)
    parser.add_argument("--pnl-tolerance-pct", type=float, default=5.0)
    parser.add_argument("--sample-rows", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        frame = read_parquet(args.input)
        repaired, report = repair_leverage_pnl_return_consistency(
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
            raw_return_column=args.raw_return_column,
            pnl_column=args.pnl_column,
            original_pnl_column=args.original_pnl_column,
            original_return_column=args.original_return_column,
            max_leverage=args.max_leverage,
            default_leverage_policy=args.default_leverage_policy,
            raw_return_discrepancy_threshold=args.raw_return_discrepancy_threshold,
            pnl_tolerance_pct=args.pnl_tolerance_pct,
            sample_rows=args.sample_rows,
        )
        write_outputs(
            repaired=repaired,
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
