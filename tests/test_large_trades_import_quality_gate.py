from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


MODULE_PATH = Path("scripts/large_trades_import_quality_gate.py")


def load_module():
    spec = importlib.util.spec_from_file_location("large_trades_import_quality_gate", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def trade_row(order_id: str, *, symbol: str = "BTCUSDT", close_time: str = "2026-01-01 00:05:00") -> dict:
    return {
        "moeda": symbol,
        "fechar_side": "LONG",
        "leverage": "2",
        "order_id": order_id,
        "pnl_fechado": "1.25",
        "taxa_lucros_perdas_fechados_pct": "0.10",
        "preco_abertura": "100.5",
        "preco_fechamento": "101.5",
        "volume_posicao": "1",
        "volume_fechado": "1",
        "horario_abertura": "2026-01-01 00:00:00",
        "horario_fechamento": close_time,
        "taxa_1": "0.01",
        "preco_transacao": "101.5",
        "volume_transacao": "1",
        "direcao_liquidez": "TAKER",
        "taxa_2": "0.01",
        "horario_transacao": close_time,
    }


def write_source(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def write_master(path: Path, rows: list[dict]) -> None:
    module = load_module()
    path.parent.mkdir(parents=True, exist_ok=True)
    master = module.clean_trade_frame(pd.DataFrame(rows), source_file="master.parquet")
    master["_dedup_key"] = master.apply(module.build_dedup_key, axis=1)
    master.to_parquet(path, index=False)


def run_gate(tmp_path: Path, source: Path, *, apply: bool = False, report: Path | None = None):
    module = load_module()
    return module.run_quality_gate(
        source_file=source,
        master_xlsx_path=tmp_path / "trades" / "trades_master.xlsx",
        master_parquet_path=tmp_path / "trades" / "trades_master.parquet",
        compatibility_xlsx_path=tmp_path / "trades" / "trades_excel.xlsx",
        report_path=report or (tmp_path / "reports" / "large_trades_import_preflight_report.json"),
        backup_dir=tmp_path / "backups",
        apply=apply,
        confirm_preflight_path=tmp_path / "reports" / "large_trades_import_preflight_report.json",
    )


def test_valid_large_trade_file_dry_run_reports_new_rows(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    master = tmp_path / "trades" / "trades_master.parquet"
    write_master(master, [trade_row("old-1")])
    write_source(source, [trade_row("new-1"), trade_row("new-2", symbol="ETHUSDT")])

    report = run_gate(tmp_path, source)

    assert report["status"] == "ok"
    assert report["dry_run"] is True
    assert report["read_rows"] == 2
    assert report["candidate_new_rows"] == 2
    assert report["duplicate_rows"] == 0
    assert report["duplicate_by_order_id_rows"] == 0
    assert report["duplicate_by_fingerprint_rows"] == 0
    assert report["missing_order_id_rows"] == 0
    assert report["dedup_policy"] == "order_id_first_then_fingerprint"
    assert report["final_expected_master_rows"] == 3
    assert report["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert report["sides"] == ["LONG"]
    assert len(pd.read_parquet(master)) == 1


def test_all_duplicate_file_is_ok_but_has_zero_new_rows(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    master = tmp_path / "trades" / "trades_master.parquet"
    write_master(master, [trade_row("same-1")])
    write_source(source, [trade_row("same-1")])

    report = run_gate(tmp_path, source)

    assert report["status"] == "ok"
    assert report["reason"] == "all_rows_duplicate"
    assert report["candidate_new_rows"] == 0
    assert report["duplicate_rows"] == 1
    assert report["duplicate_by_order_id_rows"] == 1
    assert report["duplicate_by_fingerprint_rows"] == 0
    assert report["missing_order_id_rows"] == 0
    assert report["dedup_policy"] == "order_id_first_then_fingerprint"
    assert report["final_expected_master_rows"] == 1


def test_order_id_dedup_matches_entire_existing_source(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    master = tmp_path / "trades" / "trades_master.parquet"
    write_master(master, [trade_row("same-1"), trade_row("same-2", symbol="ETHUSDT")])
    write_source(source, [trade_row("same-1"), trade_row("same-2", symbol="ETHUSDT")])

    report = run_gate(tmp_path, source)

    assert report["status"] == "ok"
    assert report["reason"] == "all_rows_duplicate"
    assert report["candidate_new_rows"] == 0
    assert report["duplicate_rows"] == report["read_rows"] == 2
    assert report["duplicate_by_order_id_rows"] == 2
    assert report["duplicate_by_fingerprint_rows"] == 0
    assert report["write_performed"] is False


def test_partial_order_id_overlap_reports_only_new_rows(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    master = tmp_path / "trades" / "trades_master.parquet"
    write_master(master, [trade_row("same-1")])
    write_source(source, [trade_row("same-1"), trade_row("new-1", symbol="ETHUSDT")])

    report = run_gate(tmp_path, source)

    assert report["status"] == "ok"
    assert report["candidate_new_rows"] == 1
    assert report["duplicate_rows"] == 1
    assert report["duplicate_by_order_id_rows"] == 1
    assert report["duplicate_by_fingerprint_rows"] == 0
    assert report["final_expected_master_rows"] == 2


def test_excel_numeric_order_id_dot_zero_matches_master_order_id(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    master = tmp_path / "trades" / "trades_master.parquet"
    write_master(master, [trade_row("123")])
    source_row = trade_row("123.0")
    write_source(source, [source_row])

    report = run_gate(tmp_path, source)

    assert report["status"] == "ok"
    assert report["reason"] == "all_rows_duplicate"
    assert report["candidate_new_rows"] == 0
    assert report["duplicate_rows"] == 1
    assert report["duplicate_by_order_id_rows"] == 1


def test_rows_without_order_id_use_fingerprint_dedup(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    master = tmp_path / "trades" / "trades_master.parquet"
    write_master(master, [trade_row("")])
    write_source(source, [trade_row("")])

    report = run_gate(tmp_path, source)

    assert report["status"] == "ok"
    assert report["reason"] == "all_rows_duplicate"
    assert report["candidate_new_rows"] == 0
    assert report["duplicate_rows"] == 1
    assert report["duplicate_by_order_id_rows"] == 0
    assert report["duplicate_by_fingerprint_rows"] == 1
    assert report["missing_order_id_rows"] == 1


def test_invalid_schema_blocks(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    row = trade_row("bad-schema")
    row.pop("moeda")
    write_source(source, [row])

    report = run_gate(tmp_path, source)

    assert report["status"] == "blocked"
    assert report["reason"] == "validation_failed"
    assert "missing_required_columns:['moeda']" in report["blocking_errors"]
    assert report["invalid_rows"] == 1


def test_invalid_dates_block(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    write_source(source, [trade_row("bad-date", close_time="2025-12-31 23:59:00")])

    report = run_gate(tmp_path, source)

    assert report["status"] == "blocked"
    assert "invalid_date_rows:1" in report["blocking_errors"]


def test_invalid_symbol_blocks(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    write_source(source, [trade_row("bad-symbol", symbol="DOGEUSDT")])

    report = run_gate(tmp_path, source)

    assert report["status"] == "blocked"
    assert "invalid_symbol_rows:1" in report["blocking_errors"]


def test_apply_blocks_without_successful_dry_run(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    master = tmp_path / "trades" / "trades_master.parquet"
    write_master(master, [trade_row("old-1")])
    write_source(source, [trade_row("new-1")])

    report = run_gate(tmp_path, source, apply=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "legacy_master_apply_forbidden"
    assert report["write_performed"] is False
    assert report["backup_created"] is False
    assert len(pd.read_parquet(master)) == 1


def test_apply_after_ok_dry_run_remains_permanently_blocked(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    master = tmp_path / "trades" / "trades_master.parquet"
    write_master(master, [trade_row("old-1")])
    write_source(source, [trade_row("new-1")])

    dry_run = run_gate(tmp_path, source)
    applied = run_gate(tmp_path, source, apply=True)

    assert dry_run["status"] == "ok"
    assert applied["status"] == "blocked"
    assert applied["reason"] == "legacy_master_apply_forbidden"
    assert applied["write_performed"] is False
    assert applied["backup_created"] is False
    assert applied["backup_paths"] == []
    assert len(pd.read_parquet(master)) == 1


def test_apply_blocks_when_preflight_has_zero_new_rows(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    master = tmp_path / "trades" / "trades_master.parquet"
    write_master(master, [trade_row("same-1")])
    write_source(source, [trade_row("same-1")])

    dry_run = run_gate(tmp_path, source)
    applied = run_gate(tmp_path, source, apply=True)

    assert dry_run["status"] == "ok"
    assert dry_run["candidate_new_rows"] == 0
    assert applied["status"] == "blocked"
    assert applied["reason"] == "legacy_master_apply_forbidden"
    assert applied["write_performed"] is False
    assert "legacy_master_apply_forbidden" in applied["blocking_errors"]
    assert len(pd.read_parquet(master)) == 1


def test_apply_blocks_when_preflight_failed(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    row = trade_row("bad-symbol", symbol="DOGEUSDT")
    write_source(source, [row])

    dry_run = run_gate(tmp_path, source)
    applied = run_gate(tmp_path, source, apply=True)

    assert dry_run["status"] == "blocked"
    assert applied["status"] == "blocked"
    assert applied["reason"] == "legacy_master_apply_forbidden"
    assert applied["write_performed"] is False
    assert not (tmp_path / "trades" / "trades_master.parquet").exists()


def test_quality_gate_preserves_paper_shadow_only_safety(tmp_path: Path) -> None:
    source = tmp_path / "incoming.parquet"
    write_source(source, [trade_row("new-1")])

    report = run_gate(tmp_path, source)
    text = MODULE_PATH.read_text(encoding="utf-8")

    assert report["runtime_mode"] == "paper"
    assert report["shadow_only"] is True
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    for forbidden in ["create_order(", "fetch_balance(", "ccxt.", "Freqtrade API"]:
        assert forbidden not in text
