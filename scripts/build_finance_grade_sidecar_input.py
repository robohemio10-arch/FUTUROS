from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
import traceback

from smartcrypto.ml.finance_grade_filter import (
    build_finance_grade_sidecar_input,
    read_parquet,
    utc_now,
    write_json,
    write_outputs,
)


DEFAULT_INPUT = Path("data/features/trade_final_financial_quality_resolved.parquet")
DEFAULT_OUTPUT = Path("data/features/trade_finance_grade_sidecar_input.parquet")
DEFAULT_REJECTED_OUTPUT = Path("data/features/trade_finance_grade_rejected.parquet")
DEFAULT_REPORT = Path("data/reports/finance_grade_sidecar_input_report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build finance-grade normalized sidecar input for offline research.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--rejected-output", default=str(DEFAULT_REJECTED_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--id-column", default="trade_id")
    parser.add_argument("--symbol-column", default="symbol")
    parser.add_argument("--target-column", default="target_win")
    parser.add_argument("--time-column", default="open_1m_ts")
    parser.add_argument("--quality-status-column", default="final_quality_status")
    parser.add_argument("--allowed-status", default="OK")
    parser.add_argument("--sample-rows", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        frame = read_parquet(args.input)
        accepted, rejected, report = build_finance_grade_sidecar_input(
            frame,
            output_path=args.output,
            rejected_output_path=args.rejected_output,
            id_column=args.id_column,
            symbol_column=args.symbol_column,
            target_column=args.target_column,
            time_column=args.time_column,
            quality_status_column=args.quality_status_column,
            allowed_status=args.allowed_status,
            sample_rows=args.sample_rows,
        )
        write_outputs(
            accepted=accepted,
            rejected=rejected,
            report=report,
            output_path=args.output,
            rejected_output_path=args.rejected_output,
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
