from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.research.aibot_parity import (
    SOURCE_INVESTMENT_ID,
    load_trader_master_readonly,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["a-1", "a-2", "a-2"],
            "moeda": ["BTC_USDT", "ETHUSDT", "ETHUSDT"],
            "fechar_side": ["Fechar Long", "Fechar Short", "Fechar Short"],
            "horario_abertura": [
                "2026-01-01 00:00:00+00:00",
                "invalid-time",
                "2026-01-03T00:00:00Z",
            ],
            "horario_fechamento": [
                "2026-01-01 00:10:00+00:00",
                "2026-01-02T00:10:00Z",
                "2026-01-03T00:20:00Z",
            ],
            "preco_abertura": [100.0, 200.0, 200.0],
            "preco_fechamento": [101.0, 199.0, 199.0],
            "pnl_fechado": ["+10 USDT", "-5 USDT", "-5 USDT"],
            "taxa_1": [0.1, 0.2, 0.2],
        }
    )


@pytest.mark.parametrize("suffix", [".parquet", ".xlsx"])
def test_loader_reads_supported_source_without_mutation(
    tmp_path: Path,
    suffix: str,
) -> None:
    source = tmp_path / "data" / "trades" / f"trades_master{suffix}"
    source.parent.mkdir(parents=True)
    frame = _fixture_frame()
    if suffix == ".parquet":
        frame.to_parquet(source, index=False)
    else:
        frame.to_excel(source, index=False)
    before = _sha256(source)

    result = load_trader_master_readonly(
        project_root=tmp_path,
        trader_master_path=source,
        source_investment_id=SOURCE_INVESTMENT_ID,
    )

    assert _sha256(source) == before
    assert result.source.source_row_count == 3
    assert result.frame["symbol"].tolist() == ["BTCUSDT", "ETHUSDT", "ETHUSDT"]
    assert result.frame["side"].tolist() == ["long", "short", "short"]
    assert result.frame["pnl_net"].tolist() == [10.0, -5.0, -5.0]
    assert result.frame["fees"].isna().all()
    assert result.frame["funding"].isna().all()
    assert result.audit["rows_removed"] == 0
    assert result.audit["fee_semantics_status"] == (
        "UNAVAILABLE_UNAPPROVED_SOURCE_SEMANTICS"
    )
    assert result.audit["adapter_report"]["trader_master_hash_preserved"] is True
    assert result.audit["safety_flags"]["writes_trader_master"] is False


def test_loader_reports_malformed_datetime_and_duplicate_ids(tmp_path: Path) -> None:
    source = tmp_path / "master.parquet"
    _fixture_frame().to_parquet(source, index=False)

    result = load_trader_master_readonly(
        project_root=tmp_path,
        trader_master_path=source,
        source_investment_id=SOURCE_INVESTMENT_ID,
    )

    counts = result.audit["quality_counts"]
    assert counts["malformed_open_time_count"] == 1
    assert counts["duplicate_order_id_row_count"] == 2
    assert counts["duplicate_order_id_value_count"] == 1
    assert result.audit["quality_status"] == "warning"


def test_empty_input_is_blocked_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "empty.parquet"
    _fixture_frame().iloc[0:0].to_parquet(source, index=False)
    before = _sha256(source)

    result = load_trader_master_readonly(
        project_root=tmp_path,
        trader_master_path=source,
        source_investment_id=SOURCE_INVESTMENT_ID,
    )

    assert result.audit["status"] == "blocked"
    assert result.audit["reason"] == "empty_trader_master"
    assert len(result.frame) == 0
    assert _sha256(source) == before
