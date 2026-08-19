#!/usr/bin/env python3
"""Build the read-only Shadow Opportunity Engine V1 research report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--paper-db", required=True)
    parser.add_argument("--candidate-source", default=None)
    parser.add_argument("--market-data-15s", default=None)
    parser.add_argument("--market-data-1m", default=None)
    parser.add_argument("--market-data-5m", default=None)
    parser.add_argument("--symbols", default=None, help="Comma-separated shadow symbols.")
    parser.add_argument("--shadow-capacity-limit", type=int, default=None)
    parser.add_argument("--evaluated-at-utc", default=None)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--append-ledger", action="store_true")
    parser.add_argument("--output-report", default="data/reports/shadow_opportunity_engine_v1.json")
    parser.add_argument("--output-ledger", default="data/reports/shadow_opportunity_ledger_v1.jsonl")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _domain() -> tuple[Any, Any, Any, Any]:
    from smartcrypto.research.shadow_opportunity_engine.engine import (
        SAFETY_FLAGS,
        build_shadow_opportunity_engine_v1,
    )
    from smartcrypto.research.shadow_opportunity_engine.persistence import (
        append_ledger_idempotent,
        resolve_ledger_path,
        resolve_report_path,
        write_report,
    )

    return (
        SAFETY_FLAGS,
        build_shadow_opportunity_engine_v1,
        (resolve_report_path, resolve_ledger_path),
        (write_report, append_ledger_idempotent),
    )


def _default_if_present(root: Path, explicit: str | None, relative: str) -> str | None:
    if explicit:
        return explicit
    candidate = root / relative
    return str(candidate) if candidate.exists() else None


def _symbols(value: str | None) -> list[str] | None:
    if value is None:
        return None
    parsed = [item.strip() for item in value.split(",") if item.strip()]
    return parsed or None


def _failure_report(reason: str, evaluated_at: str, safety: dict[str, bool]) -> dict[str, Any]:
    return {
        "schema_version": "shadow_opportunity_engine_v1",
        "generated_at_utc": evaluated_at,
        "status": "blocked",
        "reason": reason,
        "sources": {},
        "lineage": {},
        "market_context": {},
        "current_positions": [],
        "opportunity_book": {},
        "opportunity_cost": {},
        "alpha_decay": {},
        "replacement_research": {},
        "exit_efficiency": {},
        "event_engine": {"status": "blocked", "order_adapter_present": False},
        "multiasset": {},
        "gates": {"shadow_opportunity_engine_ready": "blocked"},
        "safety": dict(safety),
        **safety,
        "write_requested": False,
        "write_performed": False,
        "ledger_append_requested": False,
        "ledger_append_performed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    safety, build_report, resolvers, writers = _domain()
    resolve_report_path, resolve_ledger_path = resolvers
    write_report, append_ledger = writers
    root = Path(args.project_root).resolve()
    market_features_path = "data/features/market_features_60d.parquet"
    evaluated_at = args.evaluated_at_utc or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    writes_allowed = not args.no_write
    write_audit: dict[str, Any] = {
        "write_requested": bool(args.write_report),
        "write_performed": False,
        "ledger_append_requested": bool(args.append_ledger),
        "ledger_append_performed": False,
        "ledger_rows_appended": 0,
    }
    try:
        report = build_report(
            project_root=root,
            paper_db=args.paper_db,
            evaluated_at_utc=evaluated_at,
            candidate_source=args.candidate_source,
            market_data_15s=_default_if_present(
                root, args.market_data_15s, "data/raw/binance_futures_klines_15s"
            ),
            market_data_1m=_default_if_present(
                root, args.market_data_1m, market_features_path
            ),
            market_data_5m=_default_if_present(
                root, args.market_data_5m, market_features_path
            ),
            symbols=_symbols(args.symbols),
            shadow_capacity_limit=args.shadow_capacity_limit,
        )
        report_path = resolve_report_path(root, args.output_report)
        ledger_path = resolve_ledger_path(root, args.output_ledger)
        report.update(write_audit)
        report["output_report"] = str(report_path)
        report["output_ledger"] = str(ledger_path)
        if writes_allowed and args.append_ledger:
            appended = append_ledger(
                root,
                ledger_path,
                report.get("opportunity_cost", {}).get("ledger", []),
            )
            report["ledger_rows_appended"] = appended
            report["ledger_append_performed"] = appended > 0
            report["write_performed"] = appended > 0
            write_audit.update(
                {
                    "ledger_rows_appended": appended,
                    "ledger_append_performed": appended > 0,
                    "write_performed": appended > 0,
                }
            )
        if writes_allowed and args.write_report:
            write_report(root, report_path, report)
            report["write_performed"] = True
            write_audit["write_performed"] = True
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        report = _failure_report(
            f"{type(exc).__name__}:{exc}",
            evaluated_at,
            safety,
        )
        report.update(write_audit)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.json else 2,
            allow_nan=False,
        )
    )
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
