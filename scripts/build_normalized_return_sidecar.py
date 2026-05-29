from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback

from smartcrypto.ml.normalized_return import (
    build_normalized_return_sidecar,
    read_parquet,
    utc_now,
    write_json,
    write_outputs,
)


DEFAULT_INPUT = Path("data/features/training_dataset.parquet")
DEFAULT_OUTPUT = Path("data/features/training_normalized_return_sidecar.parquet")
DEFAULT_REPORT = Path("data/reports/normalized_return_sidecar_report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build normalized return sidecar for offline research.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--id-column", default="trade_id")
    parser.add_argument("--symbol-column", default="symbol")
    parser.add_argument("--target-column", default="target_win")
    parser.add_argument("--time-column", default="open_1m_ts")
    parser.add_argument("--entry-price-column", default="entry_price")
    parser.add_argument("--exit-price-column", default="exit_price")
    parser.add_argument("--side-column", default="fechar_side")
    parser.add_argument("--volume-column", default="volume_posicao")
    parser.add_argument("--leverage-column", default="leverage")
    parser.add_argument("--raw-return-column", default="return_pct")
    parser.add_argument("--pnl-column", default="pnl")
    parser.add_argument("--fee-bps", type=float, default=8.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--spread-bps", type=float, default=3.0)
    parser.add_argument("--max-abs-net-return-pct", type=float, default=100.0)
    parser.add_argument("--sample-outliers", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        frame = read_parquet(args.input)
        sidecar, report = build_normalized_return_sidecar(
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
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            spread_bps=args.spread_bps,
            max_abs_net_return_pct=args.max_abs_net_return_pct,
            sample_outliers=args.sample_outliers,
        )
        write_outputs(
            sidecar=sidecar,
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
