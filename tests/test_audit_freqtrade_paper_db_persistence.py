from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path


def load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit_freqtrade_paper_db_persistence.py"
    spec = importlib.util.spec_from_file_location("audit_freqtrade_paper_db_persistence", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_db(path: Path, ids: list[int], *, with_table: bool = True) -> None:
    con = sqlite3.connect(path)
    cur = con.cursor()
    if with_table:
        cur.execute("create table trades (id integer primary key, is_open integer)")
        for trade_id in ids:
            cur.execute("insert into trades (id, is_open) values (?, ?)", (trade_id, 0))
    con.commit()
    con.close()


def test_detects_log_sqlite_divergence(tmp_path: Path) -> None:
    module = load_module()
    db = tmp_path / "trades.sqlite"
    log = tmp_path / "freqtrade.log"
    report = tmp_path / "report.json"

    make_db(db, [1, 2, 22])
    log.write_text(
        "2026-06-01 14:50:08,888 - freqtrade.rpc.rpc_manager - INFO - Sending rpc message: {'trade_id': 28, 'type': entry}\n",
        encoding="utf-8",
    )

    payload = module.audit_persistence(db_path=db, log_paths=(log,), report_path=report)

    assert payload["status"] == module.LOG_SQLITE_DIVERGENCE
    assert payload["logs"]["latest_trade_id_from_log"] == 28
    assert payload["db"]["max_id"] == 22
    assert report.exists()


def test_ok_when_log_and_sqlite_are_aligned(tmp_path: Path) -> None:
    module = load_module()
    db = tmp_path / "trades.sqlite"
    log = tmp_path / "freqtrade.log"

    make_db(db, [1, 2, 28])
    log.write_text(
        "2026-06-01 14:50:08,888 - freqtrade.rpc.rpc_manager - INFO - Sending rpc message: {'trade_id': 28, 'type': entry}\n",
        encoding="utf-8",
    )

    payload = module.audit_persistence(db_path=db, log_paths=(log,), report_path=tmp_path / "report.json")

    assert payload["status"] == module.OK
    assert payload["reason"] is None


def test_missing_db_returns_missing_source(tmp_path: Path) -> None:
    module = load_module()
    log = tmp_path / "freqtrade.log"
    log.write_text("2026-06-01 14:50:08,888 - INFO - {'trade_id': 28}\n", encoding="utf-8")

    payload = module.audit_persistence(
        db_path=tmp_path / "missing.sqlite",
        log_paths=(log,),
        report_path=tmp_path / "report.json",
    )

    assert payload["status"] == module.MISSING_SOURCE


def test_invalid_schema_when_trades_table_is_missing(tmp_path: Path) -> None:
    module = load_module()
    db = tmp_path / "trades.sqlite"

    make_db(db, [], with_table=False)

    payload = module.audit_persistence(db_path=db, log_paths=(), report_path=tmp_path / "report.json")

    assert payload["status"] == module.INVALID_SCHEMA


def test_detects_db_stale_when_ids_align_but_mtime_is_old(tmp_path: Path) -> None:
    module = load_module()
    db = tmp_path / "trades.sqlite"
    log = tmp_path / "freqtrade.log"

    make_db(db, [22])
    old = 1700000000
    os.utime(db, (old, old))

    log.write_text(
        "2026-06-01 14:50:08,888 - INFO - Trade(id=22, pair=BTC/USDT:USDT)\n",
        encoding="utf-8",
    )

    payload = module.audit_persistence(db_path=db, log_paths=(log,), report_path=tmp_path / "report.json")

    assert payload["status"] == module.DB_STALE


def test_log_parser_supports_trade_model_pattern(tmp_path: Path) -> None:
    module = load_module()
    log = tmp_path / "freqtrade.log"

    log.write_text(
        "2026-06-01 14:45:16,219 - freqtrade.persistence.trade_model - INFO - Updating trade (id=25) ...\n",
        encoding="utf-8",
    )

    parsed = module.parse_freqtrade_logs((log,))

    assert parsed.latest_trade_id == 25
    assert parsed.observed_trade_ids == [25]
