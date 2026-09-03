#!/usr/bin/env python3
"""Run the SMART FUTUROS point-in-time 5m research V2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.research.market_features_rematerialization_research_v2 import (
    build_market_features_rematerialization_research_v2,
    write_research_report,
)

DEFAULT_TRADES = Path("data/trades/trades_master.parquet")
DEFAULT_CANDLES = Path("data/features/market_features_60d.parquet")
DEFAULT_REPORT = Path("data/reports/market_features_rematerialization_research_v2.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rematerialize point-in-time 5m features for research only."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--trades-path", default=str(DEFAULT_TRADES))
    parser.add_argument("--candles-path", default=str(DEFAULT_CANDLES))
    parser.add_argument("--run-challenger", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"unsupported_input_format:{suffix}")


def blocked_report(reason: str, *, trades_path: Path, candles_path: Path) -> dict[str, Any]:
    return {
        "schema_version": "market_features_rematerialization_research_v2",
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "reason": reason,
        "decision": "MANTER_EM_RESEARCH",
        "trades_path": str(trades_path),
        "candles_path": str(candles_path),
        "research_only": True,
        "operational_authority": False,
        "qlib_security_gate_remains_blocked": True,
        "qlib_security_gate_bypassed": False,
        "p08_allowed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "writes_runtime": False,
        "sends_orders": False,
        "changes_risk": False,
        "exchange_private_access": False,
        "write_requested": False,
        "write_performed": False,
    }


def main() -> int:
    args = parse_args()
    root = Path(args.project_root).expanduser().resolve()
    trades_path = Path(args.trades_path)
    candles_path = Path(args.candles_path)
    trades_path = trades_path if trades_path.is_absolute() else root / trades_path
    candles_path = candles_path if candles_path.is_absolute() else root / candles_path

    if not trades_path.is_file():
        report = blocked_report(
            "missing_trades_source", trades_path=trades_path, candles_path=candles_path
        )
    elif not candles_path.is_file():
        report = blocked_report(
            "missing_5m_candle_source", trades_path=trades_path, candles_path=candles_path
        )
    else:
        try:
            trades = read_frame(trades_path)
            candles = read_frame(candles_path)
        except (OSError, ValueError, ImportError) as exc:
            report = blocked_report(
                f"source_read_failed:{type(exc).__name__}",
                trades_path=trades_path,
                candles_path=candles_path,
            )
        else:
            report = build_market_features_rematerialization_research_v2(
                trades,
                candles,
                run_challenger=bool(args.run_challenger),
            )
            report["trades_path"] = str(trades_path)
            report["candles_path"] = str(candles_path)

    if args.write_report:
        written = write_research_report(
            report,
            project_root=root,
            output_path=args.report_json,
        )
        report["write_requested"] = True
        report["write_performed"] = True
        report["written_report_path"] = str(written)

    if args.json:
        print(json.dumps(report, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(
            f"status={report.get('status')} reason={report.get('reason')} "
            f"decision={report.get('decision')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
