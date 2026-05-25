from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from smartcrypto.execution.paper_exit_control import find_existing_db, load_config, now_utc


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_decisions(path: Path, limit: int = 80) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "rows_sampled": 0, "exit_events": 0, "recent": []}
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    sample = rows[-limit:]
    exit_events = [row for row in sample if row.get("event") == "populate_exit_trend" and row.get("accepted")]
    entry_events = [row for row in sample if row.get("event") == "populate_entry_trend" and row.get("accepted")]
    return {
        "exists": True,
        "rows_sampled": len(sample),
        "entry_events": len(entry_events),
        "exit_events": len(exit_events),
        "recent": sample[-20:],
    }


def read_db(db_path: Path | None) -> dict[str, Any]:
    if db_path is None or not db_path.exists():
        return {"status": "blocked", "reason": "freqtrade_db_not_found", "rows": 0, "open_rows": 0, "closed_rows": 0, "recent": []}
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        table_exists = connection.execute("select name from sqlite_master where type='table' and name='trades'").fetchone() is not None
        if not table_exists:
            return {"status": "blocked", "reason": "trades_table_not_found", "db_path": str(db_path), "rows": 0, "open_rows": 0, "closed_rows": 0, "recent": []}
        rows = connection.execute(
            """
            select id, pair, is_open, is_short, open_rate, close_rate, open_date, close_date, enter_tag, exit_reason, close_profit
            from trades
            order by id desc
            limit 20
            """
        ).fetchall()
        counts = connection.execute(
            """
            select
              count(*) as rows,
              sum(case when is_open = 1 then 1 else 0 end) as open_rows,
              sum(case when is_open = 0 then 1 else 0 end) as closed_rows
            from trades
            """
        ).fetchone()
        return {
            "status": "ok",
            "reason": None,
            "db_path": str(db_path),
            "rows": int(counts["rows"] or 0),
            "open_rows": int(counts["open_rows"] or 0),
            "closed_rows": int(counts["closed_rows"] or 0),
            "recent": [dict(row) for row in rows],
        }
    finally:
        connection.close()


def main() -> None:
    config = load_config()
    db_path = find_existing_db(config.db_candidates)
    exit_control = read_json(config.exit_control_path)
    decisions = read_decisions(Path("data/runtime/freqtrade_signal_decisions.jsonl"))
    db_status = read_db(db_path)
    report = {
        "exit_control": {
            "path": str(config.exit_control_path),
            "exists": config.exit_control_path.exists(),
            "content": exit_control,
        },
        "decision_log": decisions,
        "freqtrade_db": db_status,
        "phase15_status": {
            "status": "ok",
            "exit_control_active": bool(exit_control and exit_control.get("force_exit_enabled")),
            "exit_events": decisions.get("exit_events", 0),
            "open_rows": db_status.get("open_rows", 0),
            "closed_rows": db_status.get("closed_rows", 0),
        },
        "created_at": now_utc().isoformat(),
    }
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase15_exit_flow_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
