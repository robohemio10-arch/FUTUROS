from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import yaml

from smartcrypto.dashboard.freqtrade_snapshot_reader import load_freqtrade_trades_snapshot, perf_metrics
from smartcrypto.data.paper_trade_lifecycle import PaperFeedbackConfig


ROOT = Path(__file__).resolve().parents[1]


def load_sync_module():
    path = ROOT / "scripts" / "run_phase14_runtime_feedback_sync.py"
    spec = importlib.util.spec_from_file_location("run_phase14_runtime_feedback_sync", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.paper.yml").read_text(encoding="utf-8"))


def create_trade_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            create table trades (
                id integer primary key,
                pair text,
                is_open integer,
                is_short integer,
                open_rate real,
                close_rate real,
                open_date text,
                close_date text,
                enter_tag text,
                exit_reason text,
                realized_profit real,
                close_profit real,
                close_profit_abs real,
                amount real,
                leverage real,
                fee_open_cost real,
                fee_close_cost real
            )
            """
        )
        conn.executemany(
            """
            insert into trades (
                id, pair, is_open, is_short, open_rate, close_rate, open_date, close_date,
                enter_tag, exit_reason, realized_profit, close_profit, close_profit_abs,
                amount, leverage, fee_open_cost, fee_close_cost
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "BTC/USDT:USDT", 1, 0, 100.0, None, "2026-01-01", None, "paper", None, 0.0, 0.0, 0.0, 0.1, 2.0, 0.0, 0.0),
                (2, "ETH/USDT:USDT", 0, 1, 200.0, 190.0, "2026-01-02", "2026-01-03", "paper", "roi", 1.2, 0.03, 1.2, 0.2, 3.0, 0.1, 0.1),
            ],
        )
    return path


def feedback_config(tmp_path: Path, snapshot: Path) -> PaperFeedbackConfig:
    return PaperFeedbackConfig(
        db_candidates=(snapshot,),
        raw_export=tmp_path / "trades" / "freqtrade_paper_trades_raw.parquet",
        closed_export_parquet=tmp_path / "trades" / "freqtrade_paper_closed_smartcrypto.parquet",
        closed_export_csv=tmp_path / "trades" / "freqtrade_paper_closed_smartcrypto.csv",
        inbox_export_csv=tmp_path / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv",
        open_positions_report=tmp_path / "reports" / "phase14_open_positions_report.json",
        closed_feedback_report=tmp_path / "reports" / "phase14_closed_feedback_report.json",
        output_summary=tmp_path / "reports" / "phase14_output_summary.json",
        summary=tmp_path / "reports" / "phase14_summary.json",
        expected_pairs=("BTC/USDT:USDT", "ETH/USDT:USDT"),
        max_open_trades=2,
    )


def test_compose_contains_phase14_feedback_sync_service() -> None:
    service = compose()["services"]["phase14-feedback-sync-paper"]
    command = [str(item) for item in service["command"]]

    assert "scripts/docker_runtime_permissions_bootstrap.py" in command
    assert "scripts/run_phase14_runtime_feedback_sync.py" in command
    assert command[command.index("--interval-seconds") + 1] == "120"
    assert "freqtrade_paper_db:/paper-db:ro" in service["volumes"]
    assert "./data:/app/data" in service["volumes"]
    assert "./config:/app/config:ro" in service["volumes"]
    assert "./scripts:/app/scripts:ro" in service["volumes"]
    assert "./smartcrypto:/app/smartcrypto:ro" in service["volumes"]


def test_phase14_feedback_sync_service_preserves_safety_flags() -> None:
    service = compose()["services"]["phase14-feedback-sync-paper"]
    env = service["environment"]

    assert env["SMARTCRYPTO_RUNTIME_MODE"] == "paper"
    assert env["LIVE_ENABLED"] == "false"
    assert env["ORDER_SUBMISSION_ENABLED"] == "false"
    assert env["REAL_ORDER_SUBMISSION_ENABLED"] == "false"


def test_phase14_feedback_sync_service_command_does_not_send_orders() -> None:
    service = compose()["services"]["phase14-feedback-sync-paper"]
    command = service["command"]
    command_text = "\n".join(str(item) for item in command) if isinstance(command, list) else str(command)
    text = "\n".join(
        [
            command_text,
            "\n".join(service["volumes"]),
            (ROOT / "scripts" / "run_phase14_runtime_feedback_sync.py").read_text(encoding="utf-8"),
        ]
    )

    forbidden = ["create_order", "fetch_balance", "ccxt", "freqtrade rpc", "forcebuy", "forcesell"]
    assert all(token not in text.lower() for token in forbidden)
    assert "/paper-db:ro" in text


def test_runtime_sync_exports_snapshot_and_generates_phase14_reports(tmp_path: Path) -> None:
    module = load_sync_module()
    source = create_trade_db(tmp_path / "operational" / "tradesv3.paper.sqlite")
    snapshot = tmp_path / "snapshots" / "freqtrade-paper" / "tradesv3.paper.snapshot.sqlite"
    cfg = feedback_config(tmp_path, snapshot)
    source_before = source.read_bytes()

    report = module.run_feedback_sync_once(
        source_db=source,
        snapshot_output=snapshot,
        snapshot_report_path=tmp_path / "reports" / "freqtrade_paper_db_snapshot_export.json",
        report_path=tmp_path / "reports" / "phase14_runtime_feedback_sync_report.json",
        config=cfg,
    )

    assert report["status"] == "ok"
    assert report["source_db_read_only"] is True
    assert source.read_bytes() == source_before
    assert snapshot.exists()
    assert cfg.open_positions_report.exists()
    assert cfg.closed_feedback_report.exists()
    assert cfg.output_summary.exists()
    assert cfg.summary.exists()
    assert cfg.closed_export_csv.exists()
    assert json.loads(cfg.open_positions_report.read_text(encoding="utf-8"))["open_rows"] == 1
    assert json.loads(cfg.closed_feedback_report.read_text(encoding="utf-8"))["closed_rows"] == 1


def test_dashboard_can_consume_refreshed_snapshot_open_and_closed(tmp_path: Path) -> None:
    module = load_sync_module()
    source = create_trade_db(tmp_path / "operational" / "tradesv3.paper.sqlite")
    snapshot = tmp_path / "snapshots" / "freqtrade-paper" / "tradesv3.paper.snapshot.sqlite"
    cfg = feedback_config(tmp_path, snapshot)

    module.run_feedback_sync_once(
        source_db=source,
        snapshot_output=snapshot,
        snapshot_report_path=tmp_path / "reports" / "freqtrade_paper_db_snapshot_export.json",
        report_path=tmp_path / "reports" / "phase14_runtime_feedback_sync_report.json",
        config=cfg,
    )

    state = load_freqtrade_trades_snapshot([str(snapshot)])
    metrics = perf_metrics(state["trades"])

    assert state["status"] == "ok"
    assert state["db_snapshot_used"] is True
    assert metrics["trades"] == 2
    assert metrics["closed"] == 1
    assert metrics["open"] == 1


def test_runtime_sync_default_cli_is_once() -> None:
    module = load_sync_module()
    args = module.build_parser().parse_args([])

    assert args.interval_seconds == 0
    assert args.once is False
