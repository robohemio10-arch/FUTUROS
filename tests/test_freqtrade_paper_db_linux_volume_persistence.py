from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_export_module():
    path = ROOT / "scripts" / "export_freqtrade_paper_db_snapshot.py"
    spec = importlib.util.spec_from_file_location("export_freqtrade_paper_db_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_audit_module():
    path = ROOT / "scripts" / "audit_freqtrade_paper_db_persistence.py"
    spec = importlib.util.spec_from_file_location("audit_freqtrade_paper_db_persistence_for_volume_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.paper.yml").read_text(encoding="utf-8"))


def test_compose_uses_linux_named_volume_for_freqtrade_paper_db() -> None:
    payload = compose()
    service = payload["services"]["freqtrade-paper"]
    volumes = service["volumes"]

    assert payload["volumes"]["freqtrade_paper_db"]["name"] == "futuros_freqtrade_paper_db"
    assert "freqtrade_paper_db:/freqtrade/user_data/db" in volumes
    assert all(item != "./freqtrade/user_data:/freqtrade/user_data" for item in volumes)
    assert "--db-url sqlite:////freqtrade/user_data/db/tradesv3.paper.sqlite" in service["command"]


def test_compose_preserves_config_strategy_and_isolates_internal_data() -> None:
    volumes = compose()["services"]["freqtrade-paper"]["volumes"]

    assert "./freqtrade/user_data/config.paper.json:/freqtrade/user_data/config.paper.json:ro" in volumes
    assert "./freqtrade/user_data/strategies:/freqtrade/user_data/strategies:ro" in volumes
    assert "./freqtrade/user_data/logs:/freqtrade/user_data/logs" in volumes
    assert "./data:/freqtrade/user_data/data" not in volumes
    assert "freqtrade_paper_data:/freqtrade/user_data/data" in volumes
    assert "./data/runtime:/freqtrade/user_data/data/runtime:ro" in volumes


def test_live_and_real_order_flags_remain_blocked() -> None:
    payload = compose()
    for service_name in ("smartcrypto-bot-paper", "freqtrade-paper"):
        env = payload["services"][service_name]["environment"]
        assert env["SMARTCRYPTO_RUNTIME_MODE"] == "paper"
        assert env["LIVE_ENABLED"] == "false"
        assert env["ORDER_SUBMISSION_ENABLED"] == "false"
        assert env["REAL_ORDER_SUBMISSION_ENABLED"] == "false"

    paper_config = json.loads((ROOT / "freqtrade/user_data/config.paper.json").read_text(encoding="utf-8"))
    assert paper_config["dry_run"] is True
    assert paper_config["exchange"]["key"] == ""
    assert paper_config["exchange"]["secret"] == ""


def test_phase14_and_dashboard_read_only_snapshot_path() -> None:
    feedback = yaml.safe_load((ROOT / "config/paper_feedback.yml").read_text(encoding="utf-8"))
    dashboard = yaml.safe_load((ROOT / "config/paper_dashboard.yml").read_text(encoding="utf-8"))
    expected = "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite"

    assert expected in feedback["paths"]["freqtrade_db_candidates"]
    assert expected in dashboard["paths"]["freqtrade_sqlite_candidates"]
    assert all("freqtrade/user_data/tradesv3.paper.sqlite" not in item for item in feedback["paths"]["freqtrade_db_candidates"])
    assert all("freqtrade/user_data/tradesv3.paper.sqlite" not in item for item in dashboard["paths"]["freqtrade_sqlite_candidates"])


def test_snapshot_export_does_not_mutate_operational_db(tmp_path: Path) -> None:
    module = load_export_module()
    source = tmp_path / "operational.sqlite"
    snapshot = tmp_path / "snapshots" / "tradesv3.paper.snapshot.sqlite"

    with sqlite3.connect(source) as conn:
        conn.execute("create table trades (id integer primary key, is_open integer)")
        conn.execute("insert into trades (id, is_open) values (28, 0)")
    before = source.read_bytes()

    payload = module.export_local_sqlite_snapshot(source, snapshot)

    assert payload["status"] == "ok"
    assert source.read_bytes() == before
    with sqlite3.connect(snapshot) as conn:
        assert conn.execute("select max(id) from trades").fetchone()[0] == 28


def test_auditor_detects_log_sqlite_divergence_for_snapshot(tmp_path: Path) -> None:
    module = load_audit_module()
    db = tmp_path / "tradesv3.paper.snapshot.sqlite"
    log = tmp_path / "freqtrade-paper.log"

    with sqlite3.connect(db) as conn:
        conn.execute("create table trades (id integer primary key, is_open integer)")
        conn.execute("insert into trades (id, is_open) values (22, 0)")
    log.write_text(
        "2026-06-01 14:50:08,888 - INFO - Sending rpc message: {'trade_id': 28, 'type': entry}\n",
        encoding="utf-8",
    )

    payload = module.audit_persistence(db_path=db, log_paths=(log,), report_path=tmp_path / "audit.json")

    assert payload["status"] == module.LOG_SQLITE_DIVERGENCE
    assert payload["logs"]["latest_trade_id_from_log"] == 28
    assert payload["db"]["max_id"] == 22
