from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.paper_autolearning.master_consolidation import (
    build_paper_feedback_master_consolidation_report,
)


def trade(
    order_id: str,
    *,
    symbol: str = "BTCUSDT",
    side: str = "long",
    opened: str = "2026-07-01T10:00:00Z",
    closed: str = "2026-07-01T10:05:00Z",
    net_pnl: float | None = 1.25,
) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "internal_order_id": f"client-{order_id}" if order_id else "",
        "trade_id": f"trade-{order_id}" if order_id else "",
        "symbol": symbol,
        "side": side,
        "open_time_utc": opened,
        "close_time_utc": closed,
        "entry_price": 100.0,
        "exit_price": 101.0,
        "quantity": 0.5,
        "net_pnl": net_pnl,
    }


def write_source(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".csv":
        pd.DataFrame(rows).to_csv(path, index=False)
    else:
        pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def write_master(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)
    return path


def run_report(
    tmp_path: Path,
    source_rows: list[dict[str, Any]],
    master_rows: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    source = write_source(tmp_path / "data" / "feedback" / "paper_closed_trades_incremental.parquet", source_rows)
    master = tmp_path / "data" / "trades" / "trades_master.xlsx"
    if master_rows is not None or not master.exists():
        write_master(master, master_rows or [trade("m1")])
    return build_paper_feedback_master_consolidation_report(
        project_root=tmp_path,
        source_path=source,
        trades_master_xlsx_path=master,
        trades_master_parquet_path=tmp_path / "data" / "trades" / "missing_master.parquet",
        **kwargs,
    )


def test_default_is_preview_only_no_master_write(tmp_path: Path) -> None:
    master = tmp_path / "data" / "trades" / "trades_master.xlsx"
    report = run_report(tmp_path, [trade("n1")])

    assert report["status"] == "ok"
    assert report["preview_write_requested"] is False
    assert report["preview_write_performed"] is False
    assert report["master_write_requested"] is False
    assert report["master_write_performed"] is False
    assert len(pd.read_excel(master)) == 1


def test_write_preview_writes_only_reports(tmp_path: Path) -> None:
    master = tmp_path / "data" / "trades" / "trades_master.xlsx"
    json_path = tmp_path / "data" / "reports" / "preview.json"
    md_path = tmp_path / "data" / "reports" / "preview.md"
    report = run_report(
        tmp_path,
        [trade("n1")],
        preview_json_path=json_path,
        preview_markdown_path=md_path,
        write_preview=True,
    )

    assert report["preview_write_performed"] is True
    assert report["master_write_performed"] is False
    assert json_path.exists()
    assert md_path.exists()
    assert len(pd.read_excel(master)) == 1


def test_write_master_requires_backup(tmp_path: Path) -> None:
    bad_backup_root = tmp_path / "data" / "backups" / "not_a_dir"
    bad_backup_root.parent.mkdir(parents=True)
    bad_backup_root.write_text("blocked", encoding="utf-8")
    report = run_report(tmp_path, [trade("n1")], backup_root=bad_backup_root, write_master=True)

    assert report["status"] == "blocked"
    assert report["reason"].startswith("backup_failed")
    assert report["backup_created"] is False
    assert report["master_write_performed"] is False


def test_write_master_appends_only_new_rows(tmp_path: Path) -> None:
    master = tmp_path / "data" / "trades" / "trades_master.xlsx"
    report = run_report(tmp_path, [trade("n1"), trade("m1")], write_master=True)

    assert report["status"] == "ok"
    assert report["backup_created"] is True
    assert report["master_write_performed"] is True
    assert report["accepted_rows"] == 1
    assert len(pd.read_excel(master)) == 2


def test_write_master_second_run_is_idempotent(tmp_path: Path) -> None:
    master = tmp_path / "data" / "trades" / "trades_master.xlsx"
    run_report(tmp_path, [trade("n1")], write_master=True)
    second = run_report(tmp_path, [trade("n1")], write_master=True)

    assert second["status"] == "ok"
    assert second["reason"] == "no_new_rows_to_append"
    assert second["accepted_rows"] == 0
    assert second["master_write_performed"] is False
    assert len(pd.read_excel(master)) == 2


def test_duplicate_order_id_against_master_is_rejected_or_deduped(tmp_path: Path) -> None:
    report = run_report(tmp_path, [trade("m1")])

    assert report["accepted_rows"] == 0
    assert report["duplicate_rows"] == 1
    assert report["master_duplicate_order_id_rows"] == 1


def test_internal_duplicate_order_id_is_detected(tmp_path: Path) -> None:
    report = run_report(tmp_path, [trade("n1"), trade("n1")])

    assert report["accepted_rows"] == 1
    assert report["internal_duplicate_order_id_rows"] == 1
    assert report["duplicate_rows"] == 1


def test_fingerprint_duplicate_against_master_is_detected(tmp_path: Path) -> None:
    base = trade("")
    report = run_report(tmp_path, [base], master_rows=[base])

    assert report["accepted_rows"] == 0
    assert report["master_fingerprint_duplicate_rows"] >= 1
    assert report["duplicate_rows"] == 1


def test_open_trade_is_rejected(tmp_path: Path) -> None:
    row = trade("n1", closed="")
    report = run_report(tmp_path, [row])

    assert report["rejected_rows"] == 1
    assert "no_valid_staging_rows" in report["validation_errors"]


def test_missing_close_time_is_rejected(tmp_path: Path) -> None:
    row = trade("n1")
    row.pop("close_time_utc")
    report = run_report(tmp_path, [row])

    assert report["rejected_rows"] == 1
    assert "no_valid_staging_rows" in report["validation_errors"]


def test_missing_net_pnl_is_rejected(tmp_path: Path) -> None:
    report = run_report(tmp_path, [trade("n1", net_pnl=None)])

    assert report["rejected_rows"] == 1
    assert "no_valid_staging_rows" in report["validation_errors"]


def test_post_import_audit_blocks_final_duplicates(tmp_path: Path) -> None:
    report = run_report(tmp_path, [trade("n1")], master_rows=[trade("m1"), trade("m1")], write_master=True)

    assert report["status"] == "blocked"
    assert "duplicate_order_id_rows_after_gt_0" in report["validation_errors"]
    assert report["master_write_performed"] is False


def test_no_training_performed(tmp_path: Path) -> None:
    report = run_report(tmp_path, [trade("n1")])

    assert report["training_requested"] is False
    assert report["qlib_training_performed"] is False
    assert report["ai_shadow_training_performed"] is False


def test_no_registry_write_performed(tmp_path: Path) -> None:
    report = run_report(tmp_path, [trade("n1")])

    assert report["registry_write_performed"] is False


def test_no_model_promotion(tmp_path: Path) -> None:
    report = run_report(tmp_path, [trade("n1")])

    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False


def test_no_exchange_private_access(tmp_path: Path) -> None:
    report = run_report(tmp_path, [trade("n1")])

    assert report["exchange_private_access"] is False


def test_no_orders_sent(tmp_path: Path) -> None:
    report = run_report(tmp_path, [trade("n1")])

    assert report["sends_orders"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False


def test_safety_flags_preserved(tmp_path: Path) -> None:
    report = run_report(tmp_path, [trade("n1")])

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["safety_flags"]["paper_only"] is True


def test_cli_preview_json_executes(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source.parquet", [trade("n1")])
    master = write_master(tmp_path / "master.xlsx", [trade("m1")])
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_paper_feedback_master_consolidation_v1.py",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--trades-master-xlsx",
            str(master),
            "--trades-master-parquet",
            str(tmp_path / "missing.parquet"),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["master_write_performed"] is False
    assert payload["accepted_rows"] == 1


def test_cli_write_preview_json_executes(tmp_path: Path) -> None:
    source = write_source(tmp_path / "source.parquet", [trade("n1")])
    master = write_master(tmp_path / "master.xlsx", [trade("m1")])
    preview_json = tmp_path / "preview.json"
    preview_md = tmp_path / "preview.md"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_paper_feedback_master_consolidation_v1.py",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--trades-master-xlsx",
            str(master),
            "--trades-master-parquet",
            str(tmp_path / "missing.parquet"),
            "--preview-json",
            str(preview_json),
            "--preview-markdown",
            str(preview_md),
            "--write-preview",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["preview_write_performed"] is True
    assert payload["master_write_performed"] is False
    assert preview_json.exists()
    assert preview_md.exists()
