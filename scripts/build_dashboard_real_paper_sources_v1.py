from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build SMART FUTUROS real paper read-only dashboard source snapshot."
    )
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default="data/reports/dashboard_real_paper_sources_snapshot.json")
    parser.add_argument("--write", type=parse_bool, default=True)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    from smartcrypto.ops.dashboard_real_paper_sources import build_real_paper_sources_snapshot

    args = build_parser().parse_args(argv)
    result = build_real_paper_sources_snapshot(
        project_root=args.project_root,
        output_path=args.output,
        write=args.write,
    )

    summary = {
        "status": result.snapshot.get("status"),
        "reason": result.snapshot.get("reason"),
        "schema_version": result.snapshot.get("schema_version"),
        "output_path": result.output_path.as_posix() if result.output_path else None,
        "write_performed": bool(args.write),
        "freqtrade": {
            "trades_total": result.snapshot.get("freqtrade", {}).get("trades_total"),
            "orders_total": result.snapshot.get("freqtrade", {}).get("orders_total"),
            "open_trades": result.snapshot.get("freqtrade", {}).get("open_trades"),
            "closed_trades": result.snapshot.get("freqtrade", {}).get("closed_trades"),
            "realized_pnl_abs": result.snapshot.get("freqtrade", {}).get("realized_pnl_abs"),
        },
        "alerts": {
            "events_total": result.snapshot.get("alerts_messaging", {}).get("events_total"),
            "channels_total": result.snapshot.get("alerts_messaging", {}).get("channels_total"),
            "pending_total": result.snapshot.get("alerts_messaging", {}).get("pending_total"),
        },
        "safety": {
            "dashboard_readonly": result.snapshot.get("dashboard_readonly"),
            "paper_only": result.snapshot.get("paper_only"),
            "shadow_only": result.snapshot.get("shadow_only"),
            "live_trading_enabled": result.snapshot.get("live_trading_enabled"),
            "order_submission_enabled": result.snapshot.get("order_submission_enabled"),
            "real_order_submission_enabled": result.snapshot.get("real_order_submission_enabled"),
            "exchange_private_access": result.snapshot.get("exchange_private_access"),
            "sends_orders": result.snapshot.get("sends_orders"),
            "sends_notifications": result.snapshot.get("sends_notifications"),
        },
    }

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return int(result.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
