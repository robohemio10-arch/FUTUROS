from __future__ import annotations

import ast
import json
import subprocess
import sqlite3
from pathlib import Path

import pandas as pd

from scripts.build_paper_autotrain_paper_runtime_source_diagnostics_v1 import main as cli_main
from smartcrypto.learning.paper_autotrain_incremental_watermark_fix import (
    build_paper_autotrain_incremental_watermark_fix_v1,
)
from smartcrypto.learning.paper_autotrain_paper_runtime_source_diagnostics import (
    build_paper_autotrain_paper_runtime_source_diagnostics_v1,
)
from smartcrypto.learning.paper_autotrain_paper_runtime_source_diagnostics.diagnostics import (
    readonly_sqlite_uri,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUARANTINE_DIR = Path("data/research/paper_autotrain_daily_quarantine")
REPORT_JSON = Path("data/reports/paper_autotrain_paper_runtime_source_diagnostics_v1.json")
REPORT_MD = Path("data/reports/paper_autotrain_paper_runtime_source_diagnostics_v1.md")
CSV_PATH = Path("data/trades/inbox/freqtrade_paper_closed_trades.csv")
FEEDBACK_PATH = Path("data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl")


def make_row(index: int, *, record_hash: str | None = None, symbol: str | None = None) -> dict[str, object]:
    close_time = pd.Timestamp("2026-06-01T00:00:00Z") + pd.Timedelta(minutes=index)
    return {
        "record_hash": record_hash if record_hash is not None else f"hash-{index}",
        "order_id": f"order-{index}",
        "trade_id": f"trade-{index}",
        "symbol": symbol or ("BTCUSDT" if index % 2 == 0 else "ETHUSDT"),
        "side": "long" if index % 2 == 0 else "short",
        "open_time_utc": close_time - pd.Timedelta(minutes=10),
        "close_time_utc": close_time,
        "pnl_fechado": 1.0 if index % 3 == 0 else -1.0,
        "target_profitable": 1 if index % 3 == 0 else 0,
        "feature_a": float(index),
    }


def rows(start: int, count: int) -> list[dict[str, object]]:
    return [make_row(index) for index in range(start, start + count)]


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


def write_sqlite(root: Path, data: list[dict[str, object]], *, table: str = "trades") -> Path:
    db_path = root / "paper.sqlite"
    with sqlite3.connect(db_path) as conn:
        pd.DataFrame(data).to_sql(table, conn, index=False, if_exists="replace")
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


def test_missing_paper_db_blocks_as_authoritative_source_missing(tmp_path: Path) -> None:
    bootstrap_watermark(tmp_path)

    report = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
        project_root=tmp_path,
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "paper_db_source_missing_or_unreadable"
    assert report["decision"] == "PROVER_FONTE_AUTORITATIVA_PAPER_DB_READONLY"
    assert report["paper_db_read_requested"] is True
    assert report["paper_db_status"] == "missing"
    assert report["paper_db_exists"] is False
    assert report["paper_db_error"] == "paper_db_not_found"
    assert report["write_performed"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False


def test_no_new_closed_paper_trades_after_watermark_is_fail_closed(tmp_path: Path) -> None:
    existing = rows(0, 3)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, existing)
    write_closed_csv(tmp_path, existing)
    write_feedback(tmp_path, existing)

    report = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "no_new_closed_paper_trades_after_watermark"
    assert report["decision"] == "AGUARDAR_NOVOS_TRADES_PAPER"
    assert report["source_diagnosis"] == "no_new_closed_paper_trades_after_watermark"
    assert report["paper_db_new_record_count"] == 0
    assert report["paper_db_new_after_watermark_count"] == 0
    assert report["closed_trades_csv_new_record_count"] == 0
    assert report["feedback_new_record_count"] == 0
    assert report["microbatch_new_record_count"] == 0
    assert report["would_create_microbatch"] is False
    assert report["would_run_training"] is False


def test_paper_db_new_trades_not_exported_is_detected(tmp_path: Path) -> None:
    existing = rows(0, 3)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, [*existing, make_row(99)])
    write_closed_csv(tmp_path, existing)
    write_feedback(tmp_path, existing)

    report = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "paper_db_new_trades_not_exported"
    assert report["decision"] == "SINCRONIZAR_EXPORTS_FEEDBACK_PAPER"
    assert report["paper_db_new_record_count"] == 1
    assert report["paper_db_new_after_watermark_count"] == 1
    assert report["closed_trades_csv_new_record_count"] == 0
    assert report["feedback_new_record_count"] == 0
    assert report["microbatch_new_record_count"] == 0


def test_exports_or_feedback_new_trades_not_microbatched_is_detected(tmp_path: Path) -> None:
    existing = rows(0, 3)
    new_row = make_row(99)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, existing)
    write_closed_csv(tmp_path, [*existing, new_row])
    write_feedback(tmp_path, [*existing, new_row])

    report = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "exports_feedback_new_trades_not_microbatched"
    assert report["decision"] == "SINCRONIZAR_MICROBATCHES_PAPER"
    assert report["closed_trades_csv_new_record_count"] == 1
    assert report["feedback_new_record_count"] == 1
    assert report["microbatch_new_record_count"] == 0


def test_source_divergence_between_new_sources_is_detected(tmp_path: Path) -> None:
    existing = rows(0, 3)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, [*existing, make_row(90)])
    write_closed_csv(tmp_path, [*existing, make_row(91)])
    write_feedback(tmp_path, [*existing, make_row(91)])
    write_microbatch(tmp_path, "run-new", [*existing, make_row(91)])

    report = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "paper_source_divergence_detected"
    assert report["decision"] == "INVESTIGAR_DIVERGENCIA_FONTES_PAPER"
    assert report["divergence_summary"]["divergence_detected"] is True


def test_invalid_sqlite_schema_blocks_controlled(tmp_path: Path) -> None:
    bootstrap_watermark(tmp_path)
    db_path = write_sqlite(tmp_path, [{"foo": "bar"}], table="metadata")

    report = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "paper_db_source_missing_or_unreadable"
    assert report["paper_db_status"] == "invalid_schema"
    assert "paper_db_invalid_schema" in report["warnings"]


def test_default_no_write_and_write_report_only_under_data_reports(tmp_path: Path) -> None:
    existing = rows(0, 2)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, existing)

    no_write = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert no_write["write_performed"] is False
    assert not (tmp_path / REPORT_JSON).exists()
    assert not (tmp_path / REPORT_MD).exists()

    written = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
        write_report=True,
    )

    assert written["write_performed"] is True
    assert (tmp_path / REPORT_JSON).exists()
    assert (tmp_path / REPORT_MD).exists()
    assert not list((tmp_path / "data/runtime").glob("**/*"))
    assert not list((tmp_path / "data/registries/active").glob("**/*"))


def test_write_outside_data_reports_is_blocked(tmp_path: Path) -> None:
    existing = rows(0, 2)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, existing)

    report = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
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


def test_readonly_sqlite_uri_uses_mode_ro(tmp_path: Path) -> None:
    db_path = write_sqlite(tmp_path, rows(0, 1))

    uri = readonly_sqlite_uri(db_path)

    assert uri.startswith("file:")
    assert uri.endswith("?mode=ro")


def test_without_allow_paper_db_read_does_not_resolve_or_open_db(tmp_path: Path) -> None:
    existing = rows(0, 2)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, existing)

    report = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
    )

    assert report["paper_db_read_requested"] is False
    assert report["paper_db_status"] == "not_requested"
    assert report["paper_db_path"] is None
    assert report["paper_db_exists"] is False
    assert report["paper_db_error"] is None
    assert report["paper_db_new_after_watermark_count"] == 0


def test_allow_paper_db_read_with_invalid_path_is_controlled_missing(tmp_path: Path) -> None:
    bootstrap_watermark(tmp_path)
    missing_path = tmp_path / "missing.sqlite"

    report = build_paper_autotrain_paper_runtime_source_diagnostics_v1(
        project_root=tmp_path,
        paper_db_path=missing_path,
        allow_paper_db_read=True,
    )

    assert report["status"] == "blocked"
    assert report["paper_db_read_requested"] is True
    assert report["paper_db_status"] == "missing"
    assert report["paper_db_path"] == str(missing_path)
    assert report["paper_db_exists"] is False
    assert report["paper_db_error"] == "paper_db_not_found"


def test_safety_flags_preserve_research_only_boundaries(tmp_path: Path) -> None:
    bootstrap_watermark(tmp_path)

    report = build_paper_autotrain_paper_runtime_source_diagnostics_v1(project_root=tmp_path)

    assert report["research_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False
    assert report["writes_active_registry"] is False
    assert report["scheduler_registered"] is False


def test_cli_json_executes_without_project_runtime_writes(tmp_path: Path, capsys) -> None:
    bootstrap_watermark(tmp_path)

    exit_code = cli_main(["--project-root", str(tmp_path), "--json"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["schema_version"] == "paper_autotrain_paper_runtime_source_diagnostics_v1"
    assert payload["status"] == "blocked"
    assert payload["paper_db_read_requested"] is False
    assert payload["paper_db_status"] == "not_requested"
    assert payload["write_performed"] is False
    assert not (tmp_path / REPORT_JSON).exists()


def test_cli_accepts_allow_paper_db_read(tmp_path: Path, capsys) -> None:
    bootstrap_watermark(tmp_path)

    exit_code = cli_main(["--project-root", str(tmp_path), "--allow-paper-db-read", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["paper_db_read_requested"] is True
    assert payload["paper_db_status"] == "missing"
    assert payload["paper_db_error"] == "paper_db_not_found"


def test_cli_with_fake_sqlite_readonly_source_works(tmp_path: Path, capsys) -> None:
    existing = rows(0, 2)
    bootstrap_watermark(tmp_path, existing)
    db_path = write_sqlite(tmp_path, existing)

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
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["paper_db_read_requested"] is True
    assert payload["paper_db_status"] == "ok"
    assert payload["paper_db_exists"] is True
    assert payload["paper_db_new_after_watermark_count"] == 0
    assert payload["writes_sqlite"] is False


def test_no_data_files_are_staged() -> None:
    completed = subprocess.run(
        ["git", "status", "--short", "--", "data"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == ""


def test_static_boundary_does_not_import_forbidden_operational_modules() -> None:
    files = [
        PROJECT_ROOT
        / "smartcrypto/learning/paper_autotrain_paper_runtime_source_diagnostics/diagnostics.py",
        PROJECT_ROOT / "scripts/build_paper_autotrain_paper_runtime_source_diagnostics_v1.py",
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
