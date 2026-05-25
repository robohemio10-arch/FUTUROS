from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def load_yaml(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    with target.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def read_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        with target.open("r", encoding="utf-8") as handle:
            return json.load(handle) or {}
    except Exception as exc:
        return {"_error": str(exc)}


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    temp.replace(target)


def count_parquet(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"exists": False, "rows": None, "columns": None}
    try:
        frame = pd.read_parquet(target)
        return {"exists": True, "rows": int(len(frame)), "columns": list(frame.columns)}
    except Exception as exc:
        return {"exists": True, "error": str(exc), "rows": None, "columns": None}


def resolve_first(paths: list[str]) -> Path | None:
    for item in paths:
        candidate = Path(item)
        if candidate.exists():
            return candidate
    return None


def inspect_freqtrade_db(candidates: list[str]) -> dict[str, Any]:
    path = resolve_first(candidates)
    if path is None:
        return {"exists": False, "path": None, "rows": None, "open_rows": None, "closed_rows": None}
    output: dict[str, Any] = {"exists": True, "path": str(path)}
    try:
        with sqlite3.connect(str(path)) as connection:
            rows = pd.read_sql_query("select * from trades order by id desc", connection)
        output["rows"] = int(len(rows))
        output["open_rows"] = int((rows["is_open"] == 1).sum()) if "is_open" in rows else None
        output["closed_rows"] = int((rows["is_open"] == 0).sum()) if "is_open" in rows else None
        closed = rows.loc[rows["is_open"] == 0].copy() if "is_open" in rows else pd.DataFrame()
        pnl_col = "close_profit_abs" if "close_profit_abs" in rows.columns else "realized_profit" if "realized_profit" in rows.columns else None
        if pnl_col and not closed.empty:
            pnl = closed[pnl_col].fillna(0).astype(float)
            output["closed_profit_abs"] = float(pnl.sum())
            output["win_rate"] = float((pnl > 0).mean())
            gains = pnl[pnl > 0].sum()
            losses = abs(pnl[pnl < 0].sum())
            output["profit_factor"] = float(gains / losses) if losses else None
        output["recent"] = rows.head(10).to_dict(orient="records")
    except Exception as exc:
        output["error"] = str(exc)
    return output


def signal_summary(path: str | Path) -> dict[str, Any]:
    payload = read_json(path)
    records = payload.get("signals", [])
    if not isinstance(records, list):
        records = []
    now = utc_now()
    active = []
    for record in records:
        if not isinstance(record, dict):
            continue
        valid_until = record.get("valid_until")
        is_active = True
        if valid_until:
            try:
                dt = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
                dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
                is_active = dt >= now
            except ValueError:
                is_active = True
        if is_active:
            active.append(record)
    return {
        "path": str(path),
        "exists": Path(path).exists(),
        "generated_at": payload.get("generated_at"),
        "source": payload.get("source"),
        "model_version": payload.get("model_version"),
        "signal_count": len(records),
        "active_signal_count": len(active),
        "pairs": sorted({str(item.get("pair")) for item in active if item.get("pair")}),
        "sides": sorted({str(item.get("side")) for item in active if item.get("side")}),
    }


def decision_log_summary(path: str | Path, tail: int = 200) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"exists": False, "rows_sampled": 0}
    records = []
    for line in target.read_text(encoding="utf-8", errors="ignore").splitlines()[-tail:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {
        "exists": True,
        "rows_sampled": len(records),
        "accepted_decisions": sum(1 for item in records if item.get("accepted") is True),
        "entry_events": sum(1 for item in records if item.get("event") == "populate_entry_trend"),
        "exit_events": sum(1 for item in records if item.get("event") == "populate_exit_trend"),
        "recent": records[-20:],
    }


def build_session_state(config_path: str | Path = "config/paper_session.yml") -> dict[str, Any]:
    config = load_yaml(config_path)
    paths = config.get("paths", {})
    state = {
        "created_at": iso_now(),
        "runtime_mode": os.getenv("SMARTCRYPTO_RUNTIME_MODE", "paper"),
        "signals": {
            "primary": signal_summary(paths.get("primary_signals", "data/freqtrade_signals.json")),
            "pinned": signal_summary(paths.get("pinned_signals", "data/runtime/active_freqtrade_signals.json")),
        },
        "decision_log": decision_log_summary(paths.get("decision_log", "data/runtime/freqtrade_signal_decisions.jsonl")),
        "freqtrade_db": inspect_freqtrade_db(paths.get("freqtrade_sqlite_candidates", [])),
        "datasets": {
            "trades_master": count_parquet(paths.get("trades_master", "data/trades/trades_master.parquet")),
            "training_dataset": count_parquet(paths.get("training_dataset", "data/features/training_dataset.parquet")),
            "qlib_predictions": count_parquet(paths.get("qlib_predictions", "data/predictions/latest_qlib_predictions.parquet")),
        },
        "controls": {
            "kill_switch": read_json(paths.get("kill_switch", "data/runtime/kill_switch.json")),
            "paper_exit_control": read_json(paths.get("paper_exit_control", "data/runtime/paper_exit_control.json")),
        },
    }
    reports_dir = Path(config.get("session", {}).get("reports_dir", "data/reports/paper_sessions"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    write_json(reports_dir / "latest_session_state.json", state)
    return state


def add_path(archive: zipfile.ZipFile, path: Path) -> None:
    if not path.exists():
        return
    if path.is_file():
        archive.write(path, arcname=str(path))
    else:
        for child in path.rglob("*"):
            if child.is_file():
                archive.write(child, arcname=str(child))


def collect_evidence(session_id: str | None = None, config_path: str | Path = "config/paper_session.yml") -> dict[str, Any]:
    config = load_yaml(config_path)
    session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    evidence_dir = Path(config.get("session", {}).get("evidence_dir", "data/evidence"))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    output = evidence_dir / f"paper_session_{session_id}.zip"
    state = build_session_state(config_path)
    reports_dir = Path(config.get("session", {}).get("reports_dir", "data/reports/paper_sessions"))
    write_json(reports_dir / f"session_state_{session_id}.json", state)

    include = [
        Path("data/reports"),
        Path("data/runtime"),
        Path("data/trades"),
        Path("data/predictions"),
        Path("data/freqtrade_signals.json"),
        Path("freqtrade/user_data/logs"),
        Path("logs"),
    ]
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in include:
            add_path(archive, item)
        archive.writestr("session_state.json", json.dumps(state, ensure_ascii=False, indent=2, default=str))
    return {"status": "ok", "evidence": str(output), "session_id": session_id, "created_at": iso_now()}


def main() -> None:
    print(json.dumps(build_session_state(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
