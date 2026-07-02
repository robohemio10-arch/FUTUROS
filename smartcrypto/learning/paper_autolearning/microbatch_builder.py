"""Daily microbatch builder for paper auto-learning foundation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from .outcome_schema import is_feature_column_allowed, utc_now_iso

TARGET_COLUMNS = ("label_win_loss", "label_sign", "net_pnl", "profit_ratio")
BASE_FEATURE_COLUMNS = (
    "entry_price",
    "quantity",
    "notional",
    "leverage",
    "paper_candidate_filter_called",
)


def build_daily_microbatch(
    events: Sequence[Mapping[str, Any]],
    *,
    output_dir: str | Path | None = None,
    write: bool = False,
    run_date: str | None = None,
) -> dict[str, Any]:
    lookahead = sorted({column for event in events for column in event if str(column).startswith("future_ret_")})
    if lookahead:
        return {
            "status": "blocked",
            "reason": "future_ret_columns_detected",
            "microbatch_rows": 0,
            "microbatch": [],
            "microbatch_output_path": None,
            "feature_columns": [],
            "label_columns": list(TARGET_COLUMNS),
            "lookahead_columns": lookahead,
            "validation_errors": [f"future_ret_columns:{lookahead}"],
        }
    rows = [build_microbatch_row(event) for event in events if event.get("is_closed") is True and event.get("validation_status") == "ok"]
    feature_columns = feature_columns_from_rows(rows)
    label_columns = [column for column in TARGET_COLUMNS if any(row.get(column) is not None for row in rows)]
    output_path: str | None = None
    if write:
        date = run_date or _date_from_rows(rows) or utc_now_iso()[:10]
        destination = Path(output_dir or "data/feedback/training_microbatches") / f"{date}.parquet"
        destination.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(destination, index=False)
        output_path = str(destination)
    return {
        "status": "ok",
        "reason": "microbatch_built" if rows else "empty_microbatch",
        "microbatch_rows": len(rows),
        "microbatch": rows,
        "microbatch_output_path": output_path,
        "feature_columns": feature_columns,
        "label_columns": label_columns,
        "lookahead_columns": [],
        "validation_errors": [],
    }


def build_microbatch_row(event: Mapping[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event_id": event.get("event_id"),
        "order_id": event.get("order_id"),
        "trade_id": event.get("trade_id"),
        "symbol_norm": event.get("symbol_norm"),
        "side": event.get("side"),
        "open_time_utc": event.get("open_time_utc"),
        "close_time_utc": event.get("close_time_utc"),
        "label_win_loss": event.get("label_win_loss"),
        "label_sign": event.get("label_sign"),
        "net_pnl": event.get("net_pnl"),
        "profit_ratio": event.get("profit_ratio"),
        "feature_side_long": 1 if event.get("side") == "long" else 0,
        "feature_side_short": 1 if event.get("side") == "short" else 0,
        "feature_symbol_btcusdt": 1 if event.get("symbol_norm") == "BTCUSDT" else 0,
        "feature_symbol_ethusdt": 1 if event.get("symbol_norm") == "ETHUSDT" else 0,
    }
    for column in BASE_FEATURE_COLUMNS:
        feature_name = f"feature_{column}"
        row[feature_name] = event.get(column)
    return row


def feature_columns_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    columns = sorted({column for row in rows for column in row if str(column).startswith("feature_")})
    return [column for column in columns if is_feature_column_allowed(column)]


def _date_from_rows(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        value = row.get("close_time_utc") or row.get("open_time_utc")
        if value:
            return str(value)[:10]
    return None
