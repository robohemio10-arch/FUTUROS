from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback

from smartcrypto.ml.price_scale_ocr_repair import (
    read_parquet,
    repair_price_scale_ocr_anomalies,
    utc_now,
    write_json,
    write_outputs,
)


DEFAULT_INPUT = Path("data/features/trade_enriched.parquet")
DEFAULT_OUTPUT = Path("data/features/trade_price_scale_repaired.parquet")
DEFAULT_REPORT = Path("data/reports/trade_price_scale_ocr_repair_report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair price scale/OCR anomalies for offline research only.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--id-column", default="trade_id")
    parser.add_argument("--symbol-column", default="symbol")
    parser.add_argument("--time-column", default="open_1m_ts")
    parser.add_argument("--entry-price-column", default="entry_price")
    parser.add_argument("--exit-price-column", default="exit_price")
    parser.add_argument("--open-reference-column", default="open_1m_close")
    parser.add_argument("--close-reference-column", default="close_1m_close")
    parser.add_argument("--alt-open-reference-column", default="open_5m_close")
    parser.add_argument("--alt-close-reference-column", default="close_5m_close")
    parser.add_argument("--max-reference-distance-pct", type=float, default=5.0)
    parser.add_argument("--max-corrected-price-return-pct", type=float, default=20.0)
    parser.add_argument("--sample-rows", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        frame = read_parquet(args.input)
        repaired, report = repair_price_scale_ocr_anomalies(
            frame,
            output_path=args.output,
            id_column=args.id_column,
            symbol_column=args.symbol_column,
            time_column=args.time_column,
            entry_price_column=args.entry_price_column,
            exit_price_column=args.exit_price_column,
            open_reference_column=args.open_reference_column,
            close_reference_column=args.close_reference_column,
            alt_open_reference_column=args.alt_open_reference_column,
            alt_close_reference_column=args.alt_close_reference_column,
            max_reference_distance_pct=args.max_reference_distance_pct,
            max_corrected_price_return_pct=args.max_corrected_price_return_pct,
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
