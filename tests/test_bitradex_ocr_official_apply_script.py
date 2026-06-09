from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("apply_bitradex_ocr_v5", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def row(order_id: str, source_file: str = "source.png") -> dict[str, str]:
    return {
        "moeda": "BTCUSDT",
        "fechar_side": "long",
        "leverage": "10",
        "order_id": order_id,
        "pnl_fechado": "1.23",
        "taxa_lucros_perdas_fechados_pct": "0.5",
        "preco_abertura": "100000",
        "preco_fechamento": "100100",
        "volume_posicao": "0.01",
        "volume_fechado": "0.01",
        "horario_abertura": "2026-06-01 10:00:00",
        "horario_fechamento": "2026-06-01 10:05:00",
        "taxa_1": "0.01",
        "preco_transacao": "100100",
        "volume_transacao": "0.01",
        "direcao_liquidez": "maker",
        "taxa_2": "0.01",
        "horario_transacao": "2026-06-01 10:05:00",
        "source_file": source_file,
        "imported_at": "2026-06-08T12:00:00Z",
        "_dedup_key": f"dedup-{order_id}",
        "_relaxed_dedup_key": f"relaxed-{order_id}",
        "exchange_source": "bitradex",
        "market_data_source": "binance_futures_1m",
        "ocr_source": "bitradex_ocr_v5",
    }


def write_xlsx(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False)


def staging_ok() -> dict[str, Any]:
    return {
        "status": "ok",
        "duplicate_internal_order_id_rows": 0,
        "duplicate_against_trades_master_rows": 0,
        "non_hex24_order_id_rows": 0,
        "validation_errors": [],
        "writes_trades_master": False,
    }


def preview_ok() -> dict[str, Any]:
    return {
        "status": "ok",
        "preview_only": True,
        "writes_trades_master": False,
        "problem_rows": 0,
        "duplicate_internal_order_id_rows": 0,
        "duplicate_against_trades_master_rows": 0,
        "validation_errors": [],
    }


def fixture_project(tmp_path: Path, incoming_rows: list[dict[str, str]] | None = None) -> tuple[Path, Path]:
    project = tmp_path / "project"
    package = project / "data" / "staging" / "bitradex_ocr"
    master = project / "data" / "trades" / "trades_master.xlsx"
    write_xlsx(master, [row("aaaaaaaaaaaaaaaaaaaaaaaa", "master.png") | {"legacy_extra": "keep"}])
    write_xlsx(package / "BITRADEX_OCR_PHASE5_IMPORT_READY.xlsx", incoming_rows or [row("bbbbbbbbbbbbbbbbbbbbbbbb")])
    write_json(package / "PROJECT_STAGING_AUDIT_SUMMARY.json", staging_ok())
    write_json(package / "BITRADEX_OCR_IMPORT_PREVIEW_SUMMARY.json", preview_ok())
    return project, package


def apply(project: Path, package: Path, *, no_write: bool = True) -> dict[str, Any]:
    module = load_module()
    paths = module.ApplyPaths(
        project_root=project,
        package_dir=package,
        master_xlsx=project / "data" / "trades" / "trades_master.xlsx",
        master_parquet=project / "data" / "trades" / "trades_master.parquet",
        trades_excel_xlsx=project / "data" / "trades" / "trades_excel.xlsx",
        backups_root=project / "data" / "backups",
    )
    return module.apply_bitradex_ocr_orderid_synthetic_v5(paths, no_write=no_write)


def test_blocks_without_project_staging_audit_summary(tmp_path: Path) -> None:
    project, package = fixture_project(tmp_path)
    (package / "PROJECT_STAGING_AUDIT_SUMMARY.json").unlink()

    summary = apply(project, package)

    assert summary["status"] == "blocked"
    assert any("missing_staging_audit" in error for error in summary["validation_errors"])


def test_blocks_when_staging_audit_not_ok(tmp_path: Path) -> None:
    project, package = fixture_project(tmp_path)
    write_json(package / "PROJECT_STAGING_AUDIT_SUMMARY.json", staging_ok() | {"status": "blocked"})

    summary = apply(project, package)

    assert summary["status"] == "blocked"
    assert "staging_audit_status_not_ok:blocked" in summary["validation_errors"]


def test_blocks_without_preview_only(tmp_path: Path) -> None:
    project, package = fixture_project(tmp_path)
    write_json(package / "BITRADEX_OCR_IMPORT_PREVIEW_SUMMARY.json", preview_ok() | {"preview_only": False})

    summary = apply(project, package)

    assert summary["status"] == "blocked"
    assert "preview_only_not_true" in summary["validation_errors"]


def test_blocks_when_preview_has_duplicate_against_master(tmp_path: Path) -> None:
    project, package = fixture_project(tmp_path)
    write_json(
        package / "BITRADEX_OCR_IMPORT_PREVIEW_SUMMARY.json",
        preview_ok() | {"duplicate_against_trades_master_rows": 1},
    )

    summary = apply(project, package)

    assert summary["status"] == "blocked"
    assert "preview_duplicate_against_trades_master_rows_gt_0" in summary["validation_errors"]


def test_blocks_excel_lock_file(tmp_path: Path) -> None:
    project, package = fixture_project(tmp_path)
    (package / "~$BITRADEX_OCR_PHASE5_IMPORT_READY.xlsx").write_text("lock", encoding="utf-8")

    summary = apply(project, package)

    assert summary["status"] == "blocked"
    assert any(error.startswith("excel_lock_files_present:") for error in summary["validation_errors"])


def test_no_write_does_not_alter_master(tmp_path: Path) -> None:
    project, package = fixture_project(tmp_path)
    master = project / "data" / "trades" / "trades_master.xlsx"
    before = master.read_bytes()

    summary = apply(project, package, no_write=True)

    assert summary["status"] == "ok"
    assert summary["writes_official_trades_master"] is False
    assert summary["backup_created"] is False
    assert master.read_bytes() == before


def test_write_creates_backup_before_writing(tmp_path: Path) -> None:
    project, package = fixture_project(tmp_path)
    master_parquet = project / "data" / "trades" / "trades_master.parquet"
    trades_excel = project / "data" / "trades" / "trades_excel.xlsx"
    pd.DataFrame([row("aaaaaaaaaaaaaaaaaaaaaaaa")]).to_parquet(master_parquet, index=False)
    write_xlsx(trades_excel, [row("aaaaaaaaaaaaaaaaaaaaaaaa")])

    summary = apply(project, package, no_write=False)

    assert summary["status"] == "ok"
    assert summary["backup_created"] is True
    backup_dir = Path(summary["backup_dir"])
    assert (backup_dir / "trades_master.xlsx").exists()
    assert (backup_dir / "trades_master.parquet").exists()
    assert (backup_dir / "trades_excel.xlsx").exists()


def test_applies_new_rows_preserving_schema(tmp_path: Path) -> None:
    project, package = fixture_project(tmp_path)

    summary = apply(project, package, no_write=False)
    frame = pd.read_excel(project / "data" / "trades" / "trades_master.xlsx", dtype=str, keep_default_na=False)

    assert summary["status"] == "ok"
    assert summary["rows_before"] == 1
    assert summary["incoming_rows"] == 1
    assert summary["rows_after"] == 2
    assert summary["imported_rows"] == 1
    assert list(frame.columns)[: len(load_module().OFFICIAL_COLUMNS)] == load_module().OFFICIAL_COLUMNS
    assert "legacy_extra" in frame.columns
    assert frame.iloc[-1]["order_id"] == "bbbbbbbbbbbbbbbbbbbbbbbb"


def test_blocks_internal_duplicate_order_id(tmp_path: Path) -> None:
    duplicate = "bbbbbbbbbbbbbbbbbbbbbbbb"
    project, package = fixture_project(tmp_path, [row(duplicate), row(duplicate, "two.png")])

    summary = apply(project, package)

    assert summary["status"] == "blocked"
    assert "duplicate_internal_order_id_rows:2" in summary["validation_errors"]


def test_blocks_partial_duplicate_against_master(tmp_path: Path) -> None:
    project, package = fixture_project(
        tmp_path,
        [row("aaaaaaaaaaaaaaaaaaaaaaaa", "dup.png"), row("bbbbbbbbbbbbbbbbbbbbbbbb", "new.png")],
    )

    summary = apply(project, package)

    assert summary["status"] == "blocked"
    assert "duplicate_against_trades_master_rows:1" in summary["validation_errors"]


def test_second_execution_is_idempotent_noop_without_duplicate(tmp_path: Path) -> None:
    project, package = fixture_project(tmp_path)

    first = apply(project, package, no_write=False)
    second = apply(project, package, no_write=False)
    frame = pd.read_excel(project / "data" / "trades" / "trades_master.xlsx", dtype=str, keep_default_na=False)

    assert first["status"] == "ok"
    assert second["status"] == "idempotent_noop"
    assert second["imported_rows"] == 0
    assert len(frame) == 2


def test_summaries_include_safety_flags(tmp_path: Path) -> None:
    project, package = fixture_project(tmp_path)

    summary = apply(project, package, no_write=False)
    apply_summary = json.loads((package / "APPLY_BITRADEX_OCR_ORDERID_SYNTHETIC_V5_SUMMARY.json").read_text())
    post_audit = json.loads((package / "POST_IMPORT_TRADES_MASTER_AUDIT_ORDERID_SYNTHETIC_V5.json").read_text())

    assert summary["sends_orders"] is False
    assert summary["changes_risk"] is False
    assert summary["exchange_private_access"] is False
    assert apply_summary["changes_training_dataset"] is False
    assert post_audit["sends_orders"] is False
    assert post_audit["changes_risk"] is False


def test_cli_json_no_write_is_controlled(tmp_path: Path) -> None:
    project, package = fixture_project(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--project-root",
            str(project),
            "--package-dir",
            str(package),
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["status"] == "ok"
    assert payload["writes_official_trades_master"] is False
    assert payload["changes_training_dataset"] is False
    assert payload["sends_orders"] is False
    assert payload["changes_risk"] is False
    assert payload["exchange_private_access"] is False
