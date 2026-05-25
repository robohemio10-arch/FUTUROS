from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from smartcrypto.execution.paper_force_close import find_db_path, read_config, table_exists


def read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def inspect_db():
    cfg = read_config()
    db_path = find_db_path(cfg.db_candidates)
    if db_path is None:
        return {"status": "blocked", "reason": "freqtrade_db_not_found"}

    connection = sqlite3.connect(db_path)
    try:
        if not table_exists(connection, "trades"):
            return {"status": "blocked", "reason": "trades_table_not_found", "db_path": str(db_path)}

        frame = pd.read_sql_query(
            """
            select id, pair, is_open, is_short, open_rate, close_rate, open_date, close_date,
                   enter_tag, exit_reason, close_profit, close_profit_abs, realized_profit
            from trades
            order by id
            """,
            connection,
        )
    finally:
        connection.close()

    return {
        "status": "ok",
        "db_path": str(db_path),
        "rows": int(len(frame)),
        "open_rows": int(frame["is_open"].fillna(0).astype(int).sum()) if not frame.empty else 0,
        "closed_rows": int((frame["is_open"].fillna(0).astype(int) == 0).sum()) if not frame.empty else 0,
        "recent": frame.tail(10).to_dict(orient="records"),
    }


def main() -> None:
    raw_path = Path("data/trades/freqtrade_paper_trades_raw.parquet")
    closed_csv = Path("data/trades/inbox/freqtrade_paper_closed_trades.csv")
    report = {
        "force_close_report": read_json(Path("data/reports/phase16_force_close_report.json")),
        "freqtrade_db": inspect_db(),
        "raw_export": {
            "path": str(raw_path),
            "exists": raw_path.exists(),
            "rows": int(len(pd.read_parquet(raw_path))) if raw_path.exists() else None,
        },
        "closed_feedback_csv": {
            "path": str(closed_csv),
            "exists": closed_csv.exists(),
            "rows": int(len(pd.read_csv(closed_csv))) if closed_csv.exists() else None,
        },
    }
    db = report["freqtrade_db"]
    force_report = report["force_close_report"] or {}
    force_closed = int(force_report.get("closed_by_phase16") or 0)
    closed_rows = int(db.get("closed_rows", 0) or 0)
    open_rows = int(db.get("open_rows", 0) or 0)

    status = "ok"
    reason = None
    if force_report.get("status") not in {"ok", None}:
        status = "blocked"
        reason = force_report.get("reason")
    elif force_closed == 0 and open_rows > 0:
        status = "blocked"
        reason = "no_trades_force_closed"

    report["phase16_status"] = {
        "status": status,
        "reason": reason,
        "open_rows": open_rows,
        "closed_rows": closed_rows,
        "force_closed": force_closed,
    }

    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase16_output_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    if report["phase16_status"]["status"] != "ok":
        raise SystemExit(f"VALIDATION_BLOCKED: {report['phase16_status'].get('reason')}")
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
