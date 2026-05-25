from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PaperExitConfig:
    exit_control_path: Path
    report_path: Path
    db_candidates: tuple[Path, ...]
    expected_pairs: tuple[str, ...]
    validity_minutes: int


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_yaml_like(path: Path) -> dict[str, Any]:
    import yaml

    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_config(path: str | Path = "config/paper_exit_control.yml") -> PaperExitConfig:
    payload = load_yaml_like(Path(path))
    paths = payload.get("paths", {})
    policy = payload.get("policy", {})
    return PaperExitConfig(
        exit_control_path=Path(paths.get("exit_control", "data/runtime/paper_exit_control.json")),
        report_path=Path(paths.get("phase15_report", "data/reports/phase15_exit_signal_report.json")),
        db_candidates=tuple(Path(item) for item in paths.get("freqtrade_db_candidates", [])),
        expected_pairs=tuple(str(item) for item in payload.get("expected_pairs", [])),
        validity_minutes=int(policy.get("validity_minutes", 30)),
    )


def find_existing_db(candidates: tuple[Path, ...]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def read_open_trades(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            select id, pair, is_open, is_short, open_rate, close_rate, open_date, close_date, enter_tag, exit_reason
            from trades
            where is_open = 1
            order by id
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def build_exit_control(
    pairs: list[str],
    validity_minutes: int,
    reason: str,
) -> dict[str, Any]:
    created_at = now_utc()
    valid_until = created_at + timedelta(minutes=validity_minutes)
    return {
        "runtime_mode": "paper",
        "force_exit_enabled": True,
        "pairs": pairs,
        "exit_pairs": pairs,
        "side": "all",
        "reason": reason,
        "created_at": created_at.isoformat(),
        "generated_at": created_at.isoformat(),
        "valid_until": valid_until.isoformat(),
        "source": "phase15_controlled_paper_exit",
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def generate_exit_control(
    pair: str = "all",
    validity_minutes: int | None = None,
    reason: str = "phase15_controlled_paper_exit",
) -> dict[str, Any]:
    config = load_config()
    db_path = find_existing_db(config.db_candidates)
    open_trades = read_open_trades(db_path) if db_path else []
    selected_pairs = resolve_pairs(pair, open_trades, config.expected_pairs)
    effective_validity = validity_minutes or config.validity_minutes
    payload = build_exit_control(selected_pairs, effective_validity, reason)
    write_json_atomic(config.exit_control_path, payload)
    report = {
        "status": "ok" if selected_pairs else "blocked",
        "reason": None if selected_pairs else "no_pairs_to_exit",
        "db_path": str(db_path) if db_path else None,
        "open_rows": len(open_trades),
        "pairs": selected_pairs,
        "exit_control_path": str(config.exit_control_path),
        "validity_minutes": effective_validity,
        "created_at": now_utc().isoformat(),
    }
    config.report_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(config.report_path, report)
    return report


def resolve_pairs(pair: str, open_trades: list[dict[str, Any]], expected_pairs: tuple[str, ...]) -> list[str]:
    value = str(pair).strip()
    if value.lower() == "all":
        pairs = [str(row.get("pair")) for row in open_trades if row.get("pair")]
        if pairs:
            return sorted(set(pairs))
        return list(expected_pairs)
    return [value]
