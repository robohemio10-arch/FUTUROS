from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_DB_CANDIDATES = [
    Path("/app/freqtrade_user_data/tradesv3.paper.sqlite"),
    Path("/freqtrade/user_data/tradesv3.paper.sqlite"),
    Path("freqtrade/user_data/tradesv3.paper.sqlite"),
    Path("data/freqtrade_user_data/tradesv3.paper.sqlite"),
]


def find_freqtrade_db(extra_paths: list[str] | None = None) -> Path | None:
    candidates = [Path(p) for p in (extra_paths or [])] + DEFAULT_DB_CANDIDATES
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def inspect_freqtrade_db(extra_paths: list[str] | None = None) -> dict[str, Any]:
    path = find_freqtrade_db(extra_paths)
    if path is None:
        return {
            "status": "blocked",
            "reason": "freqtrade_db_not_found",
            "db_path": None,
            "tables": [],
            "rows": 0,
            "open_rows": 0,
            "closed_rows": 0,
            "recent": [],
        }

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        tables = [
            row["name"]
            for row in conn.execute("select name from sqlite_master where type='table' order by name").fetchall()
        ]
        if "trades" not in tables:
            return {
                "status": "blocked",
                "reason": "trades_table_not_found",
                "db_path": str(path),
                "tables": tables,
                "rows": 0,
                "open_rows": 0,
                "closed_rows": 0,
                "recent": [],
            }

        rows = conn.execute("select count(*) as c from trades").fetchone()["c"]
        open_rows = conn.execute("select count(*) as c from trades where is_open = 1").fetchone()["c"]
        closed_rows = conn.execute("select count(*) as c from trades where is_open = 0").fetchone()["c"]
        recent = [
            dict(row)
            for row in conn.execute(
                """
                select id, pair, is_open, is_short, open_rate, close_rate, open_date, close_date, enter_tag, exit_reason
                from trades
                order by id desc
                limit 10
                """
            ).fetchall()
        ]

    return {
        "status": "ok",
        "reason": None,
        "db_path": str(path),
        "tables": tables,
        "rows": rows,
        "open_rows": open_rows,
        "closed_rows": closed_rows,
        "recent": recent,
    }
