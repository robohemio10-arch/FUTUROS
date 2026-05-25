from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd


DB_CANDIDATES = [
    Path("freqtrade/user_data/tradesv3.paper.sqlite"),
    Path("/app/freqtrade_user_data/tradesv3.paper.sqlite"),
    Path("/freqtrade/user_data/tradesv3.paper.sqlite"),
]

DECISION_LOG_CANDIDATES = [
    Path("data/runtime/freqtrade_signal_decisions.jsonl"),
    Path("/app/data/runtime/freqtrade_signal_decisions.jsonl"),
    Path("/freqtrade/user_data/data/runtime/freqtrade_signal_decisions.jsonl"),
]


def find_existing(candidates: list[Path]) -> Path | None:
    for path in candidates:
        if path.exists():
            return path
    return None


def read_jsonl(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    for line in lines:
        try:
            rows.append(json.loads(line))
        except Exception:
            rows.append({"raw": line})
    return rows


def inspect_trades(db_path: Path | None) -> dict[str, Any]:
    if db_path is None:
        return {
            "exists": False,
            "path": None,
            "trades_table_exists": False,
            "rows": 0,
            "open_rows": 0,
            "closed_rows": 0,
            "recent": [],
        }

    try:
        with sqlite3.connect(db_path) as conn:
            tables = pd.read_sql_query(
                "select name from sqlite_master where type='table'",
                conn,
            )["name"].tolist()

            if "trades" not in tables:
                return {
                    "exists": True,
                    "path": str(db_path),
                    "trades_table_exists": False,
                    "tables": tables,
                    "rows": 0,
                    "open_rows": 0,
                    "closed_rows": 0,
                    "recent": [],
                }

            frame = pd.read_sql_query("select * from trades", conn)

        is_open = frame["is_open"].astype(bool) if "is_open" in frame.columns else pd.Series([], dtype=bool)
        recent_columns = [
            column
            for column in [
                "id",
                "pair",
                "is_open",
                "open_date",
                "close_date",
                "open_rate",
                "close_rate",
                "close_profit",
                "close_profit_abs",
                "is_short",
                "leverage",
                "enter_tag",
                "exit_reason",
                "strategy",
            ]
            if column in frame.columns
        ]

        recent = frame.tail(10)[recent_columns].to_dict(orient="records") if not frame.empty else []

        return {
            "exists": True,
            "path": str(db_path),
            "trades_table_exists": True,
            "rows": int(len(frame)),
            "open_rows": int(is_open.sum()) if len(frame) else 0,
            "closed_rows": int((~is_open).sum()) if len(frame) else 0,
            "columns": frame.columns.tolist(),
            "recent": recent,
        }
    except Exception as exc:
        return {
            "exists": True,
            "path": str(db_path),
            "error": str(exc),
            "rows": 0,
            "open_rows": 0,
            "closed_rows": 0,
            "recent": [],
        }


def inspect_signals() -> dict[str, Any]:
    path = Path("data/freqtrade_signals.json")
    if not path.exists():
        return {"exists": False, "signals": 0}

    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "exists": True,
        "path": str(path),
        "generated_at": payload.get("generated_at"),
        "runtime_mode": payload.get("runtime_mode"),
        "model_version": payload.get("model_version"),
        "source": payload.get("source"),
        "signals": payload.get("signals", []),
    }


def main() -> None:
    db_path = find_existing(DB_CANDIDATES)
    decision_log_path = find_existing(DECISION_LOG_CANDIDATES)

    decisions = read_jsonl(decision_log_path) if decision_log_path else []
    accepted = [row for row in decisions if row.get("accepted") is True]
    entries = [row for row in decisions if str(row.get("event", "")).startswith("enter_")]

    report = {
        "signals": inspect_signals(),
        "decision_log": {
            "exists": decision_log_path is not None,
            "path": str(decision_log_path) if decision_log_path else None,
            "rows_sampled": len(decisions),
            "accepted_decisions": len(accepted),
            "entry_events": len(entries),
            "recent": decisions[-30:],
        },
        "freqtrade_trades": inspect_trades(db_path),
        "phase9_status": {
            "status": "ok",
            "signal_file_exists": Path("data/freqtrade_signals.json").exists(),
            "strategy_has_written_decisions": bool(decisions),
            "has_entry_events": bool(entries),
        },
    }

    output = Path("data/reports/phase9_execution_status_report.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
