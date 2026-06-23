#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from smartcrypto.research.ocr_v11_dataset import json_safe
from smartcrypto.research.tp_sl_simulator import (
    DEFAULT_ATR_MULTIPLIERS,
    DEFAULT_FEE_BPS,
    DEFAULT_MAX_RAM_GB,
    DEFAULT_SL_BPS,
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TP_BPS,
    DEFAULT_TRAILING_ATR_MULTIPLIERS,
    DEFAULT_WORKERS,
    SimulatorConfig,
    resolve_paths,
    run_simulation,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the paper-only OCR V1.1 TP/SL research simulator."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--research-dataset-path")
    parser.add_argument("--candles-path")
    parser.add_argument("--output-grid-path")
    parser.add_argument("--output-trade-path")
    parser.add_argument("--report-path")
    parser.add_argument("--executive-report-path")
    parser.add_argument("--summary-path")
    parser.add_argument("--tp-bps", type=float, nargs="+", default=list(DEFAULT_TP_BPS))
    parser.add_argument("--sl-bps", type=float, nargs="+", default=list(DEFAULT_SL_BPS))
    parser.add_argument(
        "--atr-multipliers",
        type=float,
        nargs="+",
        default=list(DEFAULT_ATR_MULTIPLIERS),
    )
    parser.add_argument(
        "--trailing-atr-multipliers",
        type=float,
        nargs="+",
        default=list(DEFAULT_TRAILING_ATR_MULTIPLIERS),
    )
    parser.add_argument("--fee-bps", type=float, default=DEFAULT_FEE_BPS)
    parser.add_argument("--slippage-bps", type=float, default=DEFAULT_SLIPPAGE_BPS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--max-ram-gb", type=float, default=DEFAULT_MAX_RAM_GB)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-write", action="store_true", help="Validate in memory (default).")
    mode.add_argument("--write", action="store_true", help="Write research-only outputs.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = resolve_paths(
        args.project_root,
        research_dataset_path=args.research_dataset_path,
        candles_path=args.candles_path,
        output_grid_path=args.output_grid_path,
        output_trade_path=args.output_trade_path,
        report_path=args.report_path,
        executive_report_path=args.executive_report_path,
        summary_path=args.summary_path,
    )
    config = SimulatorConfig(
        tp_bps=tuple(args.tp_bps),
        sl_bps=tuple(args.sl_bps),
        atr_multipliers=tuple(args.atr_multipliers),
        trailing_atr_multipliers=tuple(args.trailing_atr_multipliers),
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        workers=args.workers,
        max_ram_gb=args.max_ram_gb,
    )
    result = run_simulation(paths, config, write=bool(args.write))
    encoded = json.dumps(json_safe(result.report), ensure_ascii=False, sort_keys=True)
    print(encoded if args.json else json.dumps(json_safe(result.report), ensure_ascii=False, indent=2, sort_keys=True))
    return {"ok": 0, "blocked": 1, "failed": 2}.get(str(result.report["status"]), 2)


if __name__ == "__main__":
    raise SystemExit(main())
