from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "update_paper_feedback_incremental_store.py"
    spec = importlib.util.spec_from_file_location("update_paper_feedback_incremental_store", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def feedback_row(order_id: str, *, symbol: str = "BTCUSDT", close_time: str = "2026-01-01 00:05:00") -> dict:
    return {
        "order_id": order_id,
        "moeda": symbol,
        "fechar_side": "long",
        "horario_abertura": "2026-01-01 00:00:00",
        "horario_fechamento": close_time,
        "preco_abertura": "100.0",
        "preco_fechamento": "101.0",
        "pnl_fechado": "1.5",
        "taxa_lucros_perdas_fechados_pct": "0.015",
        "exit_reason": "roi",
    }


def write_input(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def run_store(tmp_path: Path, input_path: Path, *, strict: bool = False) -> dict:
    module = load_module()
    return module.update_incremental_store(
        input_path=input_path,
        output_path=tmp_path / "feedback" / "paper_closed_trades_incremental.parquet",
        report_path=tmp_path / "reports" / "paper_feedback_incremental_store_report.json",
        strict=strict,
    )


def read_store(tmp_path: Path) -> pd.DataFrame:
    return pd.read_parquet(tmp_path / "feedback" / "paper_closed_trades_incremental.parquet")


def test_creates_new_incremental_store(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "freqtrade_paper_closed_trades.csv"
    write_input(source, [feedback_row("paper-1"), feedback_row("paper-2", symbol="ETHUSDT")])

    report = run_store(tmp_path, source)
    store = read_store(tmp_path)

    assert report["status"] == "ok"
    assert report["input_rows"] == 2
    assert report["existing_rows"] == 0
    assert report["new_rows"] == 2
    assert report["duplicate_rows"] == 0
    assert report["final_rows"] == 2
    assert report["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert set(store["order_id"]) == {"paper-1", "paper-2"}
    assert store["record_hash"].str.len().eq(64).all()


def test_adds_only_new_rows_to_existing_store(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "freqtrade_paper_closed_trades.csv"
    write_input(source, [feedback_row("paper-1")])
    first = run_store(tmp_path, source)
    write_input(source, [feedback_row("paper-1"), feedback_row("paper-2", symbol="ETHUSDT")])

    second = run_store(tmp_path, source)
    store = read_store(tmp_path)

    assert first["new_rows"] == 1
    assert second["existing_rows"] == 1
    assert second["new_rows"] == 1
    assert second["duplicate_by_order_id_rows"] == 1
    assert second["final_rows"] == 2
    assert list(store["order_id"]) == ["paper-1", "paper-2"]


def test_deduplicates_by_order_id_with_excel_numeric_normalization(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "freqtrade_paper_closed_trades.csv"
    write_input(source, [feedback_row("123")])
    run_store(tmp_path, source)
    write_input(source, [feedback_row("123.0")])

    report = run_store(tmp_path, source)

    assert report["status"] == "ok"
    assert report["reason"] == "no_new_rows"
    assert report["new_rows"] == 0
    assert report["duplicate_rows"] == 1
    assert report["duplicate_by_order_id_rows"] == 1
    assert read_store(tmp_path)["order_id"].tolist() == ["123"]


def test_deduplicates_by_fingerprint_when_order_id_missing(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "freqtrade_paper_closed_trades.csv"
    write_input(source, [feedback_row("")])
    run_store(tmp_path, source)
    write_input(source, [feedback_row("")])

    report = run_store(tmp_path, source)

    assert report["status"] == "ok"
    assert report["new_rows"] == 0
    assert report["duplicate_by_order_id_rows"] == 0
    assert report["duplicate_by_fingerprint_rows"] == 1
    assert report["missing_order_id_rows"] == 1
    assert len(read_store(tmp_path)) == 1


def test_does_not_alter_trades_master(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "freqtrade_paper_closed_trades.csv"
    master = tmp_path / "trades" / "trades_master.xlsx"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(b"existing-master-content")
    before = master.read_bytes()
    write_input(source, [feedback_row("paper-1")])

    report = run_store(tmp_path, source)

    assert report["status"] == "ok"
    assert master.read_bytes() == before


def test_report_contains_required_metrics(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "freqtrade_paper_closed_trades.csv"
    report_path = tmp_path / "reports" / "paper_feedback_incremental_store_report.json"
    write_input(source, [feedback_row("paper-1")])

    report = run_store(tmp_path, source)
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    for key in [
        "status",
        "reason",
        "input_rows",
        "existing_rows",
        "new_rows",
        "duplicate_rows",
        "final_rows",
        "duplicate_by_order_id_rows",
        "duplicate_by_fingerprint_rows",
        "missing_order_id_rows",
        "min_close_ts",
        "max_close_ts",
        "symbols",
        "sides",
        "output_path",
        "paper_only",
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
    ]:
        assert key in report
        assert key in saved


def test_strict_blocks_invalid_schema(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "freqtrade_paper_closed_trades.csv"
    row = feedback_row("paper-1")
    row.pop("moeda")
    write_input(source, [row])

    report = run_store(tmp_path, source, strict=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "invalid_schema"
    assert "missing_required_columns:['moeda']" in report["blocking_errors"]
    assert not (tmp_path / "feedback" / "paper_closed_trades_incremental.parquet").exists()


def test_preserves_paper_shadow_only_safety(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "freqtrade_paper_closed_trades.csv"
    write_input(source, [feedback_row("paper-1")])

    report = run_store(tmp_path, source)
    text = (ROOT / "scripts" / "update_paper_feedback_incremental_store.py").read_text(encoding="utf-8")

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["runtime_mode"] == "paper"
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    for forbidden in ["create_order(", "fetch_balance(", "ccxt.", "Freqtrade API", "trades_master"]:
        assert forbidden not in text
