from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pandas as pd

from scripts.build_paper_autotrain_microbatch_sync_planner_v1 import main as cli_main
from smartcrypto.learning.paper_autotrain_incremental_watermark_fix import (
    build_paper_autotrain_incremental_watermark_fix_v1,
)
from smartcrypto.learning.paper_autotrain_microbatch_sync_planner import (
    build_paper_autotrain_microbatch_sync_planner_v1,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUARANTINE_DIR = Path("data/research/paper_autotrain_daily_quarantine")
REPORT_JSON = Path("data/reports/paper_autotrain_microbatch_sync_planner_v1.json")
REPORT_MD = Path("data/reports/paper_autotrain_microbatch_sync_planner_v1.md")
CSV_PATH = Path("data/trades/inbox/freqtrade_paper_closed_trades.csv")
FEEDBACK_PATH = Path("data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl")


def make_row(index: int, *, record_hash: str | None = None, close_day: str = "2026-06-01") -> dict[str, object]:
    close_time = pd.Timestamp(f"{close_day}T00:00:00Z") + pd.Timedelta(minutes=index)
    return {
        "record_hash": record_hash if record_hash is not None else f"hash-{index}",
        "order_id": f"order-{index}",
        "trade_id": f"trade-{index}",
        "symbol": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
        "side": "long" if index % 2 == 0 else "short",
        "open_time_utc": close_time - pd.Timedelta(minutes=10),
        "close_time_utc": close_time,
        "pnl_fechado": 1.0 if index % 3 == 0 else -1.0,
        "target_profitable": 1 if index % 3 == 0 else 0,
        "feature_a": float(index),
        "is_open": False,
    }


def rows(start: int, count: int, *, close_day: str = "2026-06-01") -> list[dict[str, object]]:
    return [make_row(index, close_day=close_day) for index in range(start, start + count)]


def write_microbatch(root: Path, run_id: str, data: list[dict[str, object]]) -> Path:
    path = root / QUARANTINE_DIR / run_id / "incremental_training_microbatch.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(data).to_parquet(path, index=False)
    return path


def bootstrap_watermark(root: Path, data: list[dict[str, object]] | None = None) -> None:
    write_microbatch(root, "run-bootstrap", data if data is not None else rows(0, 3))
    report = build_paper_autotrain_incremental_watermark_fix_v1(
        project_root=root,
        write_watermark_state_requested=True,
        generated_at_utc="2026-06-02T00:00:00+00:00",
    )
    assert report["watermark_status"] == "ok"


def write_sqlite(root: Path, data: list[dict[str, object]], *, relative_path: str = "paper.sqlite") -> Path:
    db_path = root / relative_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        pd.DataFrame(data).to_sql("trades", conn, index=False, if_exists="replace")
    return db_path


def write_closed_csv(root: Path, data: list[dict[str, object]]) -> Path:
    path = root / CSV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(data).to_csv(path, index=False)
    return path


def write_feedback(root: Path, data: list[dict[str, object]]) -> Path:
    path = root / FEEDBACK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, default=str) + "\n" for row in data), encoding="utf-8")
    return path


def test_planner_reports_missing_microbatch_records_without_writes(tmp_path: Path) -> None:
    existing = rows(0, 3)
    new_row = make_row(99)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, [*existing, new_row])
    write_closed_csv(tmp_path, [*existing, new_row])
    write_feedback(tmp_path, [*existing, new_row])

    report = build_paper_autotrain_microbatch_sync_planner_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "microbatch_sync_plan_ready_no_execution_authority"
    assert report["decision"] == "PLANEJAR_SYNC_MICROBATCHES_PAPER_RESEARCH_ONLY"
    assert report["sync_plan_status"] == "dry_run_plan_ready"
    assert report["sync_plan_candidate_count"] == 1
    assert report["microbatch_missing_counts_by_source"]["paper_db"] == 1
    assert report["microbatch_missing_counts_by_source"]["closed_trades_csv"] == 1
    assert report["microbatch_missing_counts_by_source"]["feedback_events"] == 1
    assert report["would_create_microbatch"] is False
    assert report["would_write_microbatch"] is False
    assert report["would_run_training"] is False
    assert report["writes_parquet"] is False
    assert report["writes_sqlite"] is False
    assert report["sends_orders"] is False
    assert not (tmp_path / REPORT_JSON).exists()


def test_planner_requires_explicit_paper_db_read_authorization(tmp_path: Path) -> None:
    existing = rows(0, 2)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, [*existing, make_row(99)])
    write_closed_csv(tmp_path, [*existing, make_row(99)])
    write_feedback(tmp_path, [*existing, make_row(99)])

    report = build_paper_autotrain_microbatch_sync_planner_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "paper_db_read_not_requested"
    assert report["decision"] == "PROVER_FONTE_AUTORITATIVA_PAPER_DB_READONLY"
    assert report["paper_db_read_requested"] is False
    assert report["paper_db_status"] == "not_requested"
    assert report["would_create_microbatch"] is False
    assert report["writes_runtime"] is False


def test_planner_blocks_when_paper_db_missing(tmp_path: Path) -> None:
    existing = rows(0, 2)
    bootstrap_watermark(tmp_path, existing)
    write_closed_csv(tmp_path, [*existing, make_row(99)])
    write_feedback(tmp_path, [*existing, make_row(99)])

    report = build_paper_autotrain_microbatch_sync_planner_v1(
        project_root=tmp_path,
        paper_db_path=tmp_path / "missing.sqlite",
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "paper_db_source_missing_or_unreadable"
    assert report["decision"] == "PROVER_FONTE_AUTORITATIVA_PAPER_DB_READONLY"
    assert report["paper_db_status"] == "missing"


def test_planner_detects_source_reconciliation_required(tmp_path: Path) -> None:
    existing = rows(0, 2)
    db_new = make_row(99)
    csv_extra = make_row(100)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, [*existing, db_new])
    write_closed_csv(tmp_path, [*existing, db_new, csv_extra])
    write_feedback(tmp_path, [*existing, db_new])

    report = build_paper_autotrain_microbatch_sync_planner_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "source_reconciliation_required_before_sync_execution"
    assert report["decision"] == "RECONCILIAR_FONTES_PAPER_ANTES_DE_SYNC"
    assert report["sync_plan_status"] == "blocked_requires_source_reconciliation"
    assert report["sync_plan_requires_source_reconciliation"] is True
    assert report["source_reconciliation"]["requires_reconciliation"] is True
    assert report["source_reconciliation"]["counts"] == {
        "paper_db": 1,
        "closed_trades_csv": 2,
        "feedback_events": 1,
    }


def test_planner_snapshot_candidate_is_classified_without_runtime_writes(tmp_path: Path) -> None:
    existing = rows(0, 2)
    new_row = make_row(99)
    bootstrap_watermark(tmp_path, existing)
    snapshot_path = write_sqlite(
        tmp_path,
        [*existing, new_row],
        relative_path="data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite",
    )
    write_closed_csv(tmp_path, [*existing, new_row])
    write_feedback(tmp_path, [*existing, new_row])

    report = build_paper_autotrain_microbatch_sync_planner_v1(
        project_root=tmp_path,
        paper_db_path=snapshot_path,
        allow_paper_db_read=True,
    )

    assert report["paper_db_source_kind"] == "snapshot_db"
    assert report["paper_db_selected_source_kind"] == "snapshot_db"
    assert report["sync_plan"]["candidate_authority_source"] == "paper_db_snapshot"
    assert report["writes_runtime"] is False
    assert report["updates_freqtrade"] is False


def test_write_report_only_writes_data_reports(tmp_path: Path) -> None:
    existing = rows(0, 2)
    new_row = make_row(99)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, [*existing, new_row])
    write_closed_csv(tmp_path, [*existing, new_row])
    write_feedback(tmp_path, [*existing, new_row])

    before_parquet = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.parquet"))
    before_sqlite = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.sqlite"))

    report = build_paper_autotrain_microbatch_sync_planner_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
        write_report=True,
    )

    assert report["write_report_performed"] is True
    assert report["write_performed"] is True
    assert (tmp_path / REPORT_JSON).is_file()
    assert (tmp_path / REPORT_MD).is_file()
    after_parquet = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.parquet"))
    after_sqlite = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.sqlite"))
    assert after_parquet == before_parquet
    assert after_sqlite == before_sqlite
    assert not list((tmp_path / "data/runtime").glob("**/*"))


def test_write_outside_data_reports_is_blocked(tmp_path: Path) -> None:
    existing = rows(0, 2)
    new_row = make_row(99)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, [*existing, new_row])
    write_closed_csv(tmp_path, [*existing, new_row])
    write_feedback(tmp_path, [*existing, new_row])

    report = build_paper_autotrain_microbatch_sync_planner_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
        write_report=True,
        output_json_path=tmp_path / "outside.json",
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "write_boundary_validation_failed"
    assert report["write_performed"] is False
    assert not (tmp_path / "outside.json").exists()


def test_cli_json_executes_without_subprocess(tmp_path: Path, capsys: object) -> None:
    existing = rows(0, 2)
    new_row = make_row(99)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, [*existing, new_row])
    write_closed_csv(tmp_path, [*existing, new_row])
    write_feedback(tmp_path, [*existing, new_row])

    exit_code = cli_main(
        [
            "--project-root",
            str(tmp_path),
            "--allow-paper-db-read",
            "--paper-db-path",
            str(db_path),
            "--json",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == "paper_autotrain_microbatch_sync_planner_v1"
    assert payload["planner_mode"] == "dry_run_read_only"
    assert payload["would_create_microbatch"] is False


def test_safety_flags_preserve_research_only_boundaries(tmp_path: Path) -> None:
    existing = rows(0, 2)
    bootstrap_watermark(tmp_path, existing)

    report = build_paper_autotrain_microbatch_sync_planner_v1(project_root=tmp_path)

    assert report["research_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    for key in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "runs_training",
        "trains_model",
        "training_allowed",
        "promotion_allowed",
        "runtime_allowed",
        "writes_runtime",
        "writes_sqlite",
        "writes_parquet",
        "writes_active_registry",
        "writes_signal_file",
        "updates_freqtrade",
        "updates_risk_manager",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "scheduler_registered",
        "would_create_microbatch",
        "would_write_microbatch",
        "would_run_training",
        "would_promote_model",
    ):
        assert report[key] is False
        assert report["safety_flags"][key] is False


def test_static_boundary_does_not_import_forbidden_operational_modules() -> None:
    files = [
        PROJECT_ROOT / "smartcrypto/learning/paper_autotrain_microbatch_sync_planner/planner.py",
        PROJECT_ROOT / "scripts/build_paper_autotrain_microbatch_sync_planner_v1.py",
    ]
    forbidden = {"ccxt", "freqtrade", "docker", "subprocess"}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden), f"{path} imports forbidden modules: {imported & forbidden}"
        source = path.read_text(encoding="utf-8")
        assert ".env" not in source
        assert "active_freqtrade_signals.json" not in source


def test_no_data_files_are_staged() -> None:
    completed = __import__("subprocess").run(
        ["git", "status", "--short", "--", "data"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == ""
