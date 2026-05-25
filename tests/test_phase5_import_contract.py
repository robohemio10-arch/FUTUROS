from pathlib import Path

import pandas as pd

from smartcrypto.data.trades_importer import build_dedup_key, clean_trade_frame


def test_clean_trade_frame_preserves_canonical_columns():
    frame = pd.DataFrame(
        [
            {
                "moeda": "BTCUSDT",
                "fechar_side": "SELL",
                "order_id": "abc",
                "pnl_fechado": "1.2",
                "preco_abertura": "100",
                "preco_fechamento": "101",
                "horario_abertura": "2026-01-01 00:00:00",
                "horario_fechamento": "2026-01-01 00:05:00",
            }
        ]
    )

    cleaned = clean_trade_frame(frame, source_file="sample.xlsx")

    assert "moeda" in cleaned.columns
    assert "order_id" in cleaned.columns
    assert "source_file" in cleaned.columns
    assert len(cleaned) == 1


def test_dedup_key_prefers_order_id():
    row = pd.Series({"order_id": "123", "moeda": "BTCUSDT"})
    assert build_dedup_key(row) == "order_id::123"
