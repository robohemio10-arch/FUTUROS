from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pandas as pd

from scripts.build_paper_autotrain_source_key_reconciliation_v1 import main as cli_main
from smartcrypto.learning.paper_autotrain_incremental_watermark_fix import (
    build_paper_autotrain_incremental_watermark_fix_v1,
)
from smartcrypto.learning.paper_autotrain_source_key_reconciliation import (
    build_paper_autotrain_source_key_reconciliation_v1,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUARANTINE_DIR = Path("data/research/paper_autotrain_daily_quarantine")
REPORT_JSON = Path("data/reports/paper_autotrain_source_key_reconciliation_v1.json")
REPORT_MD = Path("data/reports/paper_autotrain_source_key_reconciliation_v1.md")
CSV_PATH = Path("data/trades/inbox/freqtrade_paper_closed_trades.csv")
FEEDBACK_PATH = Path("data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl")


def make_row(
    index: int,
    *,
    record_hash: str | None = None,
    close_day: str = "2026-06-01",
    side: str | None = None,
    symbol: str | None = None,
    pnl: float | None = None,
    order_id: str | None = None,
    trade_id: str | None = None,
) -> dict[str, object]:
    close_time = pd.Timestamp(f"{close_day}T00:00:00Z") + pd.Timedelta(minutes=index)
    resolved_symbol = symbol if symbol is not None else ("BTCUSDT" if index % 2 == 0 else "ETHUSDT")
    resolved_side = side if side is not None else ("long" if index % 2 == 0 else "short")
    resolved_pnl = pnl if pnl is not None else (1.0 if index % 3 == 0 else -1.0)
    return {
        "record_hash": record_hash if record_hash is not None else f"hash-{index}",
        "order_id": order_id if order_id is not None else f"order-{index}",
        "trade_id": trade_id if trade_id is not None else f"trade-{index}",
        "symbol": resolved_symbol,
        "side": resolved_side,
        "open_time_utc": close_time - pd.Timedelta(minutes=10),
        "close_time_utc": close_time,
        "pnl_fechado": resolved_pnl,
        "target_profitable": 1 if resolved_pnl > 0 else 0,
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


def test_reconciles_trade_close_and_order_close_without_execution_authority(tmp_path: Path) -> None:
    existing = rows(0, 3)
    new_row = make_row(99)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, [*existing, new_row])
    write_closed_csv(tmp_path, [*existing, new_row])
    write_feedback(tmp_path, [*existing, new_row])

    report = build_paper_autotrain_source_key_reconciliation_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "sources_reconciled_no_sync_authority"
    assert report["decision"] == "FONTES_RECONCILIADAS_SEM_AUTORIZACAO_DE_SYNC"
    assert report["reconciled_group_count"] == 1
    assert report["classification_counts"]["reconciled"] == 1
    assert report["reconciliation_summary"]["unreconciled_group_count"] == 0
    assert report["ready_for_microbatch_sync"] is False
    assert report["would_create_microbatch"] is False
    assert report["would_write_microbatch"] is False
    assert report["would_run_training"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False
    assert report["sends_orders"] is False
    assert not (tmp_path / REPORT_JSON).exists()


def test_requires_explicit_paper_db_read_authorization(tmp_path: Path) -> None:
    existing = rows(0, 2)
    new_row = make_row(99)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, [*existing, new_row])
    write_closed_csv(tmp_path, [*existing, new_row])
    write_feedback(tmp_path, [*existing, new_row])

    report = build_paper_autotrain_source_key_reconciliation_v1(
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


def test_blocks_when_paper_db_is_missing(tmp_path: Path) -> None:
    existing = rows(0, 2)
    new_row = make_row(99)
    bootstrap_watermark(tmp_path, existing)
    write_closed_csv(tmp_path, [*existing, new_row])
    write_feedback(tmp_path, [*existing, new_row])

    report = build_paper_autotrain_source_key_reconciliation_v1(
        project_root=tmp_path,
        paper_db_path=tmp_path / "missing.sqlite",
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "paper_db_source_missing_or_unreadable"
    assert report["decision"] == "PROVER_FONTE_AUTORITATIVA_PAPER_DB_READONLY"
    assert report["paper_db_status"] == "missing"


def test_classifies_missing_sources(tmp_path: Path) -> None:
    existing = rows(0, 2)
    all_sources = make_row(99)
    db_and_csv_only = make_row(100)
    csv_only = make_row(101)
    db_only = make_row(102)
    bootstrap_watermark(tmp_path, existing)

    db_path = write_sqlite(tmp_path, [*existing, all_sources, db_and_csv_only, db_only])
    write_closed_csv(tmp_path, [*existing, all_sources, db_and_csv_only, csv_only])
    write_feedback(tmp_path, [*existing, all_sources])

    report = build_paper_autotrain_source_key_reconciliation_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "source_key_reconciliation_required"
    assert report["decision"] == "RECONCILIAR_CHAVES_FONTES_PAPER_RESEARCH_ONLY"
    assert report["classification_counts"]["reconciled"] == 1
    assert report["classification_counts"]["missing_in_feedback"] == 1
    assert report["classification_counts"]["missing_in_db"] == 1
    assert report["classification_counts"]["missing_in_csv"] == 1
    assert report["reconciliation_summary"]["unreconciled_group_count"] == 3


def test_classifies_conflicting_fields(tmp_path: Path) -> None:
    existing = rows(0, 2)
    base = make_row(99, side="long")
    conflicting = make_row(99, side="short")
    bootstrap_watermark(tmp_path, existing)

    db_path = write_sqlite(tmp_path, [*existing, base])
    write_closed_csv(tmp_path, [*existing, conflicting])
    write_feedback(tmp_path, [*existing, base])

    report = build_paper_autotrain_source_key_reconciliation_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["classification_counts"]["conflicting"] == 1
    assert report["conflicting_group_count"] == 1
    sample = report["group_samples_by_classification"]["conflicting"][0]
    assert sorted(sample["sources_present"]) == ["closed_trades_csv", "feedback_events", "paper_db"]


def test_classifies_ambiguous_duplicate_group(tmp_path: Path) -> None:
    existing = rows(0, 2)
    duplicate_a = make_row(99, record_hash="dup-a")
    duplicate_b = make_row(99, record_hash="dup-b")
    bootstrap_watermark(tmp_path, existing)

    db_path = write_sqlite(tmp_path, [*existing, duplicate_a, duplicate_b])
    write_closed_csv(tmp_path, [*existing, duplicate_a])
    write_feedback(tmp_path, [*existing, duplicate_a])

    report = build_paper_autotrain_source_key_reconciliation_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["classification_counts"]["ambiguous"] == 1
    assert report["ambiguous_group_count"] == 1
    sample = report["group_samples_by_classification"]["ambiguous"][0]
    assert len(sample["native_keys_by_source"]["paper_db"]) == 2


def test_snapshot_source_is_classified_without_runtime_writes(tmp_path: Path) -> None:
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

    report = build_paper_autotrain_source_key_reconciliation_v1(
        project_root=tmp_path,
        paper_db_path=snapshot_path,
        allow_paper_db_read=True,
    )

    assert report["paper_db_source_kind"] == "snapshot_db"
    assert report["paper_db_selected_source_kind"] == "snapshot_db"
    assert report["paper_db_authority_status"] in {
        "explicit_db_selected",
        "snapshot_db_fresh_requires_authority_review",
    }
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

    report = build_paper_autotrain_source_key_reconciliation_v1(
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

    report = build_paper_autotrain_source_key_reconciliation_v1(
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
    assert payload["schema_version"] == "paper_autotrain_source_key_reconciliation_v1"
    assert payload["reconciliation_mode"] == "read_only_research"
    assert payload["would_create_microbatch"] is False


def test_safety_flags_preserve_research_only_boundaries(tmp_path: Path) -> None:
    existing = rows(0, 2)
    bootstrap_watermark(tmp_path, existing)

    report = build_paper_autotrain_source_key_reconciliation_v1(project_root=tmp_path)

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
        "ready_for_microbatch_sync",
        "ready_for_sync_execution",
        "would_create_microbatch",
        "would_write_microbatch",
        "would_run_training",
        "would_promote_model",
    ):
        assert report[key] is False
        assert report["safety_flags"][key] is False


def test_static_boundary_does_not_import_forbidden_operational_modules() -> None:
    files = [
        PROJECT_ROOT / "smartcrypto/learning/paper_autotrain_source_key_reconciliation/reconciliation.py",
        PROJECT_ROOT / "scripts/build_paper_autotrain_source_key_reconciliation_v1.py",
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
