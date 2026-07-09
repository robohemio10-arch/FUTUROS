from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from smartcrypto.learning.paper_autotrain_incremental_watermark_fix import (
    build_paper_autotrain_incremental_watermark_fix_v1,
)
from smartcrypto.learning.paper_autotrain_source_key_reconciliation import (
    build_paper_autotrain_source_key_reconciliation_v1,
)

QUARANTINE_DIR = Path("data/research/paper_autotrain_daily_quarantine")
CSV_PATH = Path("data/trades/inbox/freqtrade_paper_closed_trades.csv")
FEEDBACK_PATH = Path("data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl")


def seed_row() -> dict[str, object]:
    return {
        "record_hash": "seed-hash-1",
        "order_id": "freqtrade-paper-1",
        "trade_id": "1",
        "symbol": "ETHUSDT",
        "side": "long",
        "open_time_utc": pd.Timestamp("2026-06-01T20:15:07.045313Z"),
        "close_time_utc": pd.Timestamp("2026-06-01T22:01:49.990000Z"),
        "pnl_fechado": -0.81114816,
        "target_profitable": 0,
        "is_open": False,
    }


def write_microbatch(root: Path) -> None:
    path = root / QUARANTINE_DIR / "run-bootstrap" / "incremental_training_microbatch.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([seed_row()]).to_parquet(path, index=False)


def bootstrap_watermark(root: Path) -> None:
    write_microbatch(root)
    report = build_paper_autotrain_incremental_watermark_fix_v1(
        project_root=root,
        write_watermark_state_requested=True,
        generated_at_utc="2026-06-02T00:00:00+00:00",
    )
    assert report["watermark_status"] == "ok"


def sqlite_row(
    trade_id: int,
    *,
    pair: str,
    is_short: int,
    close_date: str,
    pnl: float,
) -> dict[str, object]:
    return {
        "id": trade_id,
        "exchange": "binance",
        "pair": pair,
        "base_currency": pair.split("/")[0],
        "stake_currency": "USDT",
        "is_open": 0,
        "open_date": "2026-06-04 08:00:00.000000",
        "close_date": close_date,
        "close_profit_abs": pnl,
        "realized_profit": pnl,
        "is_short": is_short,
        "enter_tag": "smartcrypto_short" if is_short else "smartcrypto_long",
    }


def csv_row(
    trade_id: int,
    *,
    moeda: str,
    fechar_side: str,
    horario_fechamento: str,
    pnl: float,
) -> dict[str, object]:
    return {
        "moeda": moeda,
        "fechar_side": fechar_side,
        "leverage": 2.0,
        "order_id": f"freqtrade-paper-{trade_id}",
        "pnl_fechado": pnl,
        "taxa_lucros_perdas_fechados_pct": 0.0,
        "preco_abertura": 1.0,
        "preco_fechamento": 1.0,
        "volume_posicao": 1.0,
        "volume_fechado": 1.0,
        "horario_abertura": "2026-06-04 08:00:00.000000",
        "horario_fechamento": horario_fechamento,
        "taxa_1": 0.0,
        "preco_transacao": 1.0,
        "volume_transacao": 1.0,
        "direcao_liquidez": f"smartcrypto_{fechar_side}",
        "taxa_2": 0.0,
        "horario_transacao": horario_fechamento,
    }


def feedback_row(
    trade_id: int,
    *,
    symbol: str,
    side: str,
    close_time_utc: str,
    pnl: float,
) -> dict[str, object]:
    return {
        "order_id": f"freqtrade-paper-{trade_id}",
        "symbol": symbol,
        "side": side,
        "open_time_utc": "2026-06-04T08:00:00.000Z",
        "close_time_utc": close_time_utc,
        "net_pnl": pnl,
    }


def write_sqlite(root: Path, rows: list[dict[str, object]]) -> Path:
    db_path = root / "paper.sqlite"
    with sqlite3.connect(db_path) as conn:
        pd.DataFrame(rows).to_sql("trades", conn, index=False, if_exists="replace")
    return db_path


def write_csv(root: Path, rows: list[dict[str, object]]) -> None:
    path = root / CSV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def write_feedback(root: Path, rows: list[dict[str, object]]) -> None:
    path = root / FEEDBACK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_real_schema_reconciles_portuguese_csv_feedback_and_freqtrade_pair(tmp_path: Path) -> None:
    bootstrap_watermark(tmp_path)

    db_path = write_sqlite(
        tmp_path,
        [
            sqlite_row(
                100,
                pair="ETH/USDT:USDT",
                is_short=0,
                close_date="2026-06-04 08:13:12.902000",
                pnl=-0.86771704,
            )
        ],
    )
    write_csv(
        tmp_path,
        [
            csv_row(
                100,
                moeda="ETHUSDT",
                fechar_side="long",
                horario_fechamento="2026-06-04 08:13:12.902000",
                pnl=-0.86771704,
            )
        ],
    )
    write_feedback(
        tmp_path,
        [
            feedback_row(
                100,
                symbol="ETHUSDT",
                side="long",
                close_time_utc="2026-06-04T08:13:12.902Z",
                pnl=-0.86771704,
            )
        ],
    )

    report = build_paper_autotrain_source_key_reconciliation_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["classification_counts"]["reconciled"] == 1
    assert report["classification_counts"]["conflicting"] == 0
    assert report["classification_counts"]["missing_in_csv"] == 0
    assert report["classification_counts"]["missing_in_db"] == 0
    assert report["classification_counts"]["missing_in_feedback"] == 0
    assert report["source_status"]["closed_trades_csv"]["normalized_record_count"] == 1
    assert report["source_status"]["paper_db"]["normalized_record_count"] == 1
    assert report["source_status"]["feedback_events"]["normalized_record_count"] == 1

    sample = report["group_samples_by_classification"]["reconciled"][0]
    assert sample["field_snapshot_by_source"]["paper_db"][0]["symbol"] == "ETHUSDT"
    assert sample["field_snapshot_by_source"]["closed_trades_csv"][0]["symbol"] == "ETHUSDT"
    assert sample["field_snapshot_by_source"]["feedback_events"][0]["symbol"] == "ETHUSDT"


def test_real_schema_classifies_csv_and_db_rows_missing_only_in_feedback(tmp_path: Path) -> None:
    bootstrap_watermark(tmp_path)

    db_path = write_sqlite(
        tmp_path,
        [
            sqlite_row(
                100,
                pair="ETH/USDT:USDT",
                is_short=0,
                close_date="2026-06-04 08:13:12.902000",
                pnl=-0.86771704,
            ),
            sqlite_row(
                511,
                pair="BTC/USDT:USDT",
                is_short=0,
                close_date="2026-07-03 12:01:58.668000",
                pnl=0.61582388,
            ),
        ],
    )
    write_csv(
        tmp_path,
        [
            csv_row(
                100,
                moeda="ETHUSDT",
                fechar_side="long",
                horario_fechamento="2026-06-04 08:13:12.902000",
                pnl=-0.86771704,
            ),
            csv_row(
                511,
                moeda="BTCUSDT",
                fechar_side="long",
                horario_fechamento="2026-07-03 12:01:58.668000",
                pnl=0.61582388,
            ),
        ],
    )
    write_feedback(
        tmp_path,
        [
            feedback_row(
                100,
                symbol="ETHUSDT",
                side="long",
                close_time_utc="2026-06-04T08:13:12.902Z",
                pnl=-0.86771704,
            )
        ],
    )

    report = build_paper_autotrain_source_key_reconciliation_v1(
        project_root=tmp_path,
        paper_db_path=db_path,
        allow_paper_db_read=True,
    )

    assert report["classification_counts"]["reconciled"] == 1
    assert report["classification_counts"]["missing_in_feedback"] == 1
    assert report["classification_counts"]["conflicting"] == 0
    assert report["pairwise_reconciled_counts"]["paper_db_vs_closed_trades_csv"] == 2
    assert report["pairwise_reconciled_counts"]["paper_db_vs_feedback_events"] == 1
    assert report["pairwise_reconciled_counts"]["closed_trades_csv_vs_feedback_events"] == 1
    assert report["would_create_microbatch"] is False
    assert report["writes_runtime"] is False
    assert report["sends_orders"] is False
