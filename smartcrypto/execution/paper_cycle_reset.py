from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_text_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def signal_summary(path: Path) -> dict[str, Any]:
    payload = read_text_json(path)
    signals = payload.get("signals", [])
    active = []
    now = datetime.now(timezone.utc)
    for signal in signals if isinstance(signals, list) else []:
        valid_until = signal.get("valid_until")
        is_active = True
        if valid_until:
            try:
                parsed = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
                is_active = parsed >= now
            except Exception:
                is_active = False
        if is_active:
            active.append(signal)
    return {
        "path": str(path),
        "exists": path.exists(),
        "generated_at": payload.get("generated_at"),
        "source": payload.get("source"),
        "model_version": payload.get("model_version", "unknown"),
        "signal_count": len(signals) if isinstance(signals, list) else 0,
        "active_signal_count": len(active),
        "pairs": sorted({str(item.get("pair")) for item in active if item.get("pair")}),
        "sides": sorted({str(item.get("side")) for item in active if item.get("side")}),
    }


def find_freqtrade_db() -> Path | None:
    candidates = [
        Path("/app/freqtrade_user_data/tradesv3.paper.sqlite"),
        Path("/app/freqtrade/user_data/tradesv3.paper.sqlite"),
        Path("/freqtrade/user_data/tradesv3.paper.sqlite"),
        Path("freqtrade/user_data/tradesv3.paper.sqlite"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def inspect_freqtrade_db() -> dict[str, Any]:
    db_path = find_freqtrade_db()
    if db_path is None:
        return {"status": "missing", "db_path": None, "rows": 0, "open_rows": 0, "closed_rows": 0, "recent": []}

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("select count(*) as n from trades").fetchone()["n"]
        open_rows = connection.execute("select count(*) as n from trades where is_open = 1").fetchone()["n"]
        closed_rows = connection.execute("select count(*) as n from trades where is_open = 0").fetchone()["n"]
        recent = [
            dict(row)
            for row in connection.execute(
                """
                select id, pair, is_open, is_short, open_rate, close_rate, open_date,
                       close_date, enter_tag, exit_reason, close_profit, close_profit_abs, realized_profit
                from trades
                order by id desc
                limit 10
                """
            ).fetchall()
        ]
    return {
        "status": "ok",
        "db_path": str(db_path),
        "rows": rows,
        "open_rows": open_rows,
        "closed_rows": closed_rows,
        "recent": recent,
    }


def inspect_file_rows(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "rows": None, "columns": None}
    try:
        import pandas as pd
        if path.suffix.lower() == ".parquet":
            frame = pd.read_parquet(path)
        elif path.suffix.lower() in {".xlsx", ".xls"}:
            frame = pd.read_excel(path)
        elif path.suffix.lower() == ".csv":
            frame = pd.read_csv(path)
        else:
            return {"path": str(path), "exists": True, "rows": None, "columns": None}
        return {"path": str(path), "exists": True, "rows": int(len(frame)), "columns": list(frame.columns)}
    except Exception as exc:
        return {"path": str(path), "exists": True, "rows": None, "columns": None, "error": str(exc)}


def inspect_decision_log(path: Path, limit: int = 80) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "rows_sampled": 0, "entry_events": 0, "exit_events": 0, "accepted_decisions": 0}
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return {
        "path": str(path),
        "exists": True,
        "rows_sampled": len(records),
        "accepted_decisions": sum(1 for item in records if item.get("accepted") is True),
        "entry_events": sum(1 for item in records if item.get("event") == "populate_entry_trend"),
        "exit_events": sum(1 for item in records if item.get("event") == "populate_exit_trend"),
        "recent": records[-20:],
    }


def reset_runtime_controls(config_path: str | Path, clear_signals: bool = False) -> dict[str, Any]:
    config = read_config(config_path)
    paths = config.get("paths", {})
    exit_control = Path(paths.get("paper_exit_control", "data/runtime/paper_exit_control.json"))
    archive_dir = Path(paths.get("paper_exit_archive_dir", "data/runtime/archive"))
    primary = Path(paths.get("primary_signals", "data/freqtrade_signals.json"))
    pinned = Path(paths.get("pinned_signals", "data/runtime/active_freqtrade_signals.json"))
    report_path = Path(paths.get("reset_report", "data/reports/phase17_reset_report.json"))

    live_enabled = os.getenv("LIVE_ENABLED", "false").lower() == "true"
    order_enabled = os.getenv("ORDER_SUBMISSION_ENABLED", "false").lower() == "true"
    real_order_enabled = os.getenv("REAL_ORDER_SUBMISSION_ENABLED", "false").lower() == "true"
    if live_enabled or order_enabled or real_order_enabled:
        report = {
            "status": "blocked",
            "reason": "unsafe_runtime_flags",
            "live_enabled": live_enabled,
            "order_submission_enabled": order_enabled,
            "real_order_submission_enabled": real_order_enabled,
            "created_at": utc_now(),
        }
        write_json(report_path, report)
        return report

    archived = []
    disabled_marker = None
    if exit_control.exists():
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"paper_exit_control_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        shutil.copy2(exit_control, archive_path)
        archived.append(str(archive_path))
        disabled_payload = read_text_json(exit_control)
        disabled_payload.update(
            {
                "force_exit_enabled": False,
                "disabled_at": utc_now(),
                "disabled_by": "phase17_paper_cycle_reset",
                "previous_valid_until": disabled_payload.get("valid_until"),
            }
        )
        write_json(exit_control, disabled_payload)
        disabled_marker = str(exit_control)

    cleared_signals = []
    if clear_signals:
        empty_payload = {
            "generated_at": utc_now(),
            "source": "phase17_paper_cycle_reset",
            "model_version": "cleared",
            "signals": [],
        }
        write_json(primary, empty_payload)
        write_json(pinned, empty_payload)
        cleared_signals = [str(primary), str(pinned)]

    report = {
        "status": "ok",
        "exit_control_exists": exit_control.exists(),
        "archived_exit_controls": archived,
        "disabled_exit_control": disabled_marker,
        "clear_signals": clear_signals,
        "cleared_signals": cleared_signals,
        "created_at": utc_now(),
    }
    write_json(report_path, report)
    return report


def inspect_cycle_state(config_path: str | Path) -> dict[str, Any]:
    config = read_config(config_path)
    paths = config.get("paths", {})

    report_path = Path(paths.get("sqlite_report", "data/reports/phase17_cycle_state_report.json"))
    primary = Path(paths.get("primary_signals", "data/freqtrade_signals.json"))
    pinned = Path(paths.get("pinned_signals", "data/runtime/active_freqtrade_signals.json"))
    exit_control = Path(paths.get("paper_exit_control", "data/runtime/paper_exit_control.json"))
    decision_log = Path(paths.get("decision_log", "data/runtime/freqtrade_signal_decisions.jsonl"))

    db = inspect_freqtrade_db()
    training = inspect_file_rows(Path("data/features/training_dataset.parquet"))
    trade_enriched = inspect_file_rows(Path("data/features/trade_enriched.parquet"))

    exit_payload = read_text_json(exit_control)
    status = "ok"
    reason = None
    if exit_payload.get("force_exit_enabled") is True:
        status = "blocked"
        reason = "paper_exit_control_still_enabled"

    report = {
        "status": status,
        "reason": reason,
        "exit_control": {
            "path": str(exit_control),
            "exists": exit_control.exists(),
            "force_exit_enabled": exit_payload.get("force_exit_enabled"),
            "valid_until": exit_payload.get("valid_until"),
            "source": exit_payload.get("source"),
        },
        "primary_signal": signal_summary(primary),
        "pinned_signal": signal_summary(pinned),
        "decision_log": inspect_decision_log(decision_log),
        "freqtrade_db": db,
        "datasets": {
            "trade_enriched": trade_enriched,
            "training_dataset": training,
        },
        "phase17_status": {
            "ready_for_next_paper_cycle": status == "ok" and db.get("open_rows", 0) == 0 and training.get("rows", 0) and training.get("rows", 0) >= 2,
            "open_rows": db.get("open_rows", 0),
            "closed_rows": db.get("closed_rows", 0),
            "training_rows": training.get("rows"),
            "signals_available": signal_summary(primary).get("active_signal_count", 0) > 0 or signal_summary(pinned).get("active_signal_count", 0) > 0,
        },
        "created_at": utc_now(),
    }
    write_json(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["reset", "inspect"])
    parser.add_argument("--config", default="config/paper_cycle_reset.yml")
    parser.add_argument("--clear-signals", action="store_true")
    args = parser.parse_args()

    if args.action == "reset":
        report = reset_runtime_controls(args.config, clear_signals=args.clear_signals)
    else:
        report = inspect_cycle_state(args.config)

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
