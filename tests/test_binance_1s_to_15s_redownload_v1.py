from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from smartcrypto.research.binance_futures_15s_redownload import (
    DownloadConfig,
    archive_aggtrades_to_dataframe,
    build_archive_url,
    parse_iso_date,
    resample_aggtrades_to_15s,
    run_download,
)


def _make_archive_bytes(symbol: str, day: str) -> bytes:
    csv_payload = "\n".join(
        [
            "aggregate_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker",
            "1,100.0,1.0,10,10,1767571200000,false",
            "2,101.0,2.0,11,11,1767571205000,true",
            "3,99.0,3.0,12,12,1767571215000,false",
            "4,102.0,4.0,13,13,1767571229000,false",
        ]
    )
    handle = io.BytesIO()
    with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{symbol}-aggTrades-{day}.csv", csv_payload)
    return handle.getvalue()


def test_build_archive_url_targets_futures_um_aggtrades() -> None:
    url = build_archive_url("BTCUSDT", date(2026, 1, 5))
    assert url == "https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2026-01-05.zip"


def test_parse_iso_date_rejects_invalid_dates() -> None:
    assert parse_iso_date("2026-01-05") == date(2026, 1, 5)
    try:
        parse_iso_date("2026-99-99")
    except ValueError as exc:
        assert "invalid_date" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected invalid date")


def test_archive_aggtrades_to_dataframe_parses_headered_archive_payload() -> None:
    payload = _make_archive_bytes("BTCUSDT", "2026-01-05")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        with archive.open("BTCUSDT-aggTrades-2026-01-05.csv") as handle:
            frame = pd.read_csv(handle)
    parsed = archive_aggtrades_to_dataframe(frame, "BTCUSDT")
    assert len(parsed) == 4
    assert parsed["symbol"].unique().tolist() == ["BTCUSDT"]
    assert parsed["price"].tolist() == [100.0, 101.0, 99.0, 102.0]
    assert parsed["buyer_is_maker"].tolist() == [False, True, False, False]


def test_resample_aggtrades_to_15s_builds_ohlcv_and_taker_buy_volume() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["BTCUSDT", "BTCUSDT", "BTCUSDT", "BTCUSDT"],
            "aggregate_trade_id": [1, 2, 3, 4],
            "price": [100.0, 101.0, 99.0, 102.0],
            "quantity": [1.0, 2.0, 3.0, 4.0],
            "first_trade_id": [10, 11, 12, 13],
            "last_trade_id": [10, 11, 12, 13],
            "buyer_is_maker": [False, True, False, False],
            "timestamp": pd.to_datetime(
                [
                    "2026-01-05T00:00:00Z",
                    "2026-01-05T00:00:05Z",
                    "2026-01-05T00:00:15Z",
                    "2026-01-05T00:00:29Z",
                ],
                utc=True,
            ),
        }
    )
    result = resample_aggtrades_to_15s(frame)
    assert len(result) == 2
    first = result.iloc[0]
    second = result.iloc[1]
    assert first["open"] == 100.0
    assert first["high"] == 101.0
    assert first["low"] == 100.0
    assert first["close"] == 101.0
    assert first["volume"] == 3.0
    assert first["taker_buy_base_asset_volume"] == 1.0
    assert second["open"] == 99.0
    assert second["high"] == 102.0
    assert second["low"] == 99.0
    assert second["close"] == 102.0
    assert second["taker_buy_base_asset_volume"] == 7.0
    assert result["source"].unique().tolist() == ["binance_usdm_futures_public_aggtrades_resampled_15s"]


def test_run_download_no_write_is_deterministic(tmp_path: Path) -> None:
    config = DownloadConfig(
        project_root=tmp_path,
        symbols=("BTCUSDT", "ETHUSDT"),
        from_date=date(2026, 1, 5),
        to_date=date(2026, 1, 5),
        output_dir=tmp_path / "out",
        no_write=True,
    )
    result = run_download(config)
    assert result["schema_version"] == "binance_futures_aggtrades_to_15s_redownload_v4"
    assert result["status"] == "ok"
    assert result["reason"] == "no_write_preflight_ok"
    assert len(result["days"]) == 2
    assert result["written_files"] == []
    assert result["paper_only"] is True
    assert result["shadow_only"] is True
    assert result["exchange_private_access"] is False
    assert all("aggTrades" in day["archive_url"] for day in result["days"])
