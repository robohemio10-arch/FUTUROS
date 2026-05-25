from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Phase10Paths:
    data_dir: Path = Path("data")
    reports_dir: Path = Path("data/reports")
    signals_path: Path = Path("data/freqtrade_signals.json")
    decisions_path: Path = Path("data/runtime/freqtrade_signal_decisions.jsonl")
    freqtrade_db_paths: tuple[Path, ...] = (
        Path("/app/freqtrade_user_data/tradesv3.paper.sqlite"),
        Path("freqtrade/user_data/tradesv3.paper.sqlite"),
    )


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "invalid_json", "path": str(path), "error": str(exc)}


def read_jsonl_tail(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def summarize_signal_file(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if payload is None:
        return {"exists": False, "path": str(path), "signals": 0}
    signals = payload.get("signals", [])
    return {
        "exists": True,
        "path": str(path),
        "generated_at": payload.get("generated_at"),
        "source": payload.get("source"),
        "model_version": payload.get("model_version"),
        "signals": len(signals),
        "pairs": [signal.get("pair") for signal in signals],
        "sides": [signal.get("side") for signal in signals],
    }


def summarize_decision_log(path: Path) -> dict[str, Any]:
    rows = read_jsonl_tail(path, 200)
    accepted = [row for row in rows if row.get("accepted") is True]
    entries = [row for row in rows if str(row.get("event", "")).startswith("entry")]
    reasons: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("reason", "unknown"))
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "exists": path.exists(),
        "path": str(path),
        "rows_sampled": len(rows),
        "accepted_decisions": len(accepted),
        "entry_events": len(entries),
        "reason_counts": reasons,
        "recent": rows[-10:],
    }


def summarize_freqtrade_db(paths: tuple[Path, ...]) -> dict[str, Any]:
    db_path = next((path for path in paths if path.exists()), None)
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

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("select name from sqlite_master where type='table'").fetchall()
        }
        if "trades" not in tables:
            return {
                "exists": True,
                "path": str(db_path),
                "trades_table_exists": False,
                "rows": 0,
                "open_rows": 0,
                "closed_rows": 0,
                "recent": [],
            }

        columns = [row[1] for row in conn.execute("pragma table_info(trades)").fetchall()]
        rows = conn.execute("select count(*) from trades").fetchone()[0]
        open_rows = conn.execute("select count(*) from trades where is_open = 1").fetchone()[0]
        closed_rows = conn.execute("select count(*) from trades where is_open = 0").fetchone()[0]

        selected = [
            column
            for column in [
                "id",
                "pair",
                "is_open",
                "is_short",
                "open_rate",
                "close_rate",
                "open_date",
                "close_date",
                "enter_tag",
                "exit_reason",
                "realized_profit",
                "close_profit_abs",
            ]
            if column in columns
        ]
        recent = []
        if selected:
            query = f"select {', '.join(selected)} from trades order by id desc limit 10"
            for row in conn.execute(query).fetchall():
                recent.append(dict(zip(selected, row)))

    return {
        "exists": True,
        "path": str(db_path),
        "trades_table_exists": True,
        "rows": rows,
        "open_rows": open_rows,
        "closed_rows": closed_rows,
        "recent": recent,
    }


def build_phase10_summary(paths: Phase10Paths | None = None) -> dict[str, Any]:
    paths = paths or Phase10Paths()
    phase_reports = {}
    for name in [
        "phase8_qlib_signal_export_report",
        "phase9_execution_status_report",
        "phase7_paper_history_report",
        "phase5_import_report",
        "phase5_rebuild_report",
        "phase4_baseline_training_report",
    ]:
        phase_reports[name] = read_json(paths.reports_dir / f"{name}.json")

    signals = summarize_signal_file(paths.signals_path)
    decisions = summarize_decision_log(paths.decisions_path)
    trades = summarize_freqtrade_db(paths.freqtrade_db_paths)

    return {
        "signals": signals,
        "decision_log": decisions,
        "freqtrade_trades": trades,
        "phase_reports": phase_reports,
        "phase10_status": {
            "status": "ok",
            "signal_file_exists": signals["exists"],
            "signals_available": signals.get("signals", 0) > 0,
            "strategy_decisions_seen": decisions["exists"] and decisions["rows_sampled"] > 0,
            "paper_trades_seen": trades["rows"] > 0,
            "open_paper_trades": trades["open_rows"],
            "closed_paper_trades": trades["closed_rows"],
        },
    }
