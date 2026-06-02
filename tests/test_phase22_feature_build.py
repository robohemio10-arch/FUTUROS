from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest


MODULE_PATH = Path("scripts/build_phase22_market_features.py")


def load_module():
    spec = importlib.util.spec_from_file_location("build_phase22_market_features", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def duplicate_column_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            [1_700_000_000_000, "BTCUSDT", "BTCUSDT", "1m", 100, 101, 99, 100.5, 10],
            [1_700_000_060_000, "BTCUSDT", "BTCUSDT", "1m", 100.5, 102, 100, 101, 12],
            [1_700_000_120_000, "BTCUSDT", "BTCUSDT", "1m", 101, 103, 100.5, 102, 15],
        ],
        columns=[
            "open_time",
            "symbol",
            "symbol",
            "tf",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )


def market_frame(symbol: str, rows: int = 90) -> pd.DataFrame:
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    data = []
    base = 100 if symbol == "BTCUSDT" else 200
    for idx in range(rows):
        close = base + idx * 0.2
        data.append(
            {
                "timestamp": start + pd.Timedelta(minutes=idx),
                "symbol": symbol,
                "open": close - 0.1,
                "high": close + 0.4,
                "low": close - 0.5,
                "close": close,
                "volume": 10 + idx,
            }
        )
    return pd.DataFrame(data)


def write_raw_csv(raw_dir: Path, symbol: str, *, include_symbol: bool = True) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    frame = market_frame(symbol)
    if not include_symbol:
        frame = frame.drop(columns=["symbol"])
    path = raw_dir / f"{symbol}_1m_fixture.csv"
    frame.to_csv(path, index=False)
    return path


def write_raw_parquet(raw_dir: Path, symbol: str, *, include_symbol: bool = True) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    frame = market_frame(symbol)
    if not include_symbol:
        frame = frame.drop(columns=["symbol"])
    path = raw_dir / f"{symbol}_1m_fixture.parquet"
    frame.to_parquet(path, index=False)
    return path


def run_builder(tmp_path: Path, *, update_main: bool = False, backup: bool = False) -> dict:
    module = load_module()
    raw_dir = tmp_path / "raw"
    write_raw_parquet(raw_dir, "BTCUSDT", include_symbol=False)
    write_raw_csv(raw_dir, "ETHUSDT", include_symbol=False)
    return module.run_phase22_feature_build(
        raw_dir=raw_dir,
        symbols=["BTCUSDT", "ETHUSDT"],
        interval="1m",
        output_path=tmp_path / "features" / "market_features_1m_backfill.parquet",
        main_features_path=tmp_path / "features" / "market_features_60d.parquet",
        sqlite_path=tmp_path / "sqlite" / "trading_dataset.sqlite",
        sqlite_table="market_features",
        update_main_features=update_main,
        backup=backup,
        backups_dir=tmp_path / "backups",
        features_report_path=tmp_path / "reports" / "phase22_features_report.json",
        quality_report_path=tmp_path / "reports" / "phase22_data_quality_report.json",
    )


def test_normalize_raw_collapses_duplicate_columns_to_1d_series() -> None:
    module = load_module()

    normalized = module.normalize_raw(duplicate_column_frame(), interval="1m")

    assert module.duplicate_column_names(duplicate_column_frame()) == ["symbol"]
    assert list(normalized.columns) == module.BASE_COLS
    assert not normalized.columns.has_duplicates
    assert normalized["symbol"].tolist() == ["BTCUSDT", "BTCUSDT", "BTCUSDT"]
    assert normalized["tf"].tolist() == ["1m", "1m", "1m"]
    assert isinstance(normalized["ts"].dtype, pd.DatetimeTZDtype)
    assert pd.api.types.is_integer_dtype(normalized["ts_ms"])
    assert pd.api.types.is_numeric_dtype(normalized["close"])


def test_numeric_conversion_never_receives_2d_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    original_to_numeric = module.pd.to_numeric
    seen_types: list[type] = []

    def assert_series_only(value, *args, **kwargs):
        seen_types.append(type(value))
        assert isinstance(value, pd.Series)
        return original_to_numeric(value, *args, **kwargs)

    monkeypatch.setattr(module.pd, "to_numeric", assert_series_only)

    module.normalize_raw(duplicate_column_frame(), interval="1m")

    assert seen_types
    assert pd.DataFrame not in seen_types


def test_normalize_raw_accepts_timestamp_alias_and_symbol_concat() -> None:
    module = load_module()
    btc = market_frame("BTCUSDT", rows=2)
    eth = market_frame("ETHUSDT", rows=2)

    combined = pd.concat(
        [module.normalize_raw(btc), module.normalize_raw(eth)],
        ignore_index=True,
    )

    assert set(combined["symbol"]) == {"BTCUSDT", "ETHUSDT"}
    assert combined.groupby("symbol")["ts_ms"].nunique().to_dict() == {
        "BTCUSDT": 2,
        "ETHUSDT": 2,
    }
    assert str(combined["ts"].dt.tz) == "UTC"


def test_normalize_raw_raises_clear_error_for_missing_required_columns() -> None:
    module = load_module()
    broken = pd.DataFrame({"timestamp": ["2026-01-01T00:00:00Z"], "symbol": ["BTCUSDT"]})

    with pytest.raises(module.Phase22FeatureBuildError, match="missing_required_columns"):
        module.normalize_raw(broken)


def test_build_group_features_has_no_lookahead_columns() -> None:
    module = load_module()
    normalized = module.normalize_raw(market_frame("BTCUSDT", rows=240))

    features = module.build_group_features(normalized)

    for column in ["ret_1", "ema_20", "rsi_14", "atr_pct_14"]:
        assert column in features.columns
    assert not [column for column in features.columns if column.startswith("future_")]
    assert len(features) == len(normalized)


def test_builder_outputs_btc_eth_1m_5m_sorted_schema_and_reports(tmp_path: Path) -> None:
    report = run_builder(tmp_path)
    output = tmp_path / "features" / "market_features_1m_backfill.parquet"
    features = pd.read_parquet(output)
    quality_report = json.loads(
        (tmp_path / "reports" / "phase22_data_quality_report.json").read_text(encoding="utf-8")
    )

    assert report["status"] == "ok"
    assert report["reason"] == "ok"
    assert report["rows"] == len(features)
    assert report["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert report["raw_files_ok"] == 2
    assert report["raw_files_skipped"] == 0
    assert report["raw_files_blocked"] == 0
    assert set(features["symbol"]) == {"BTCUSDT", "ETHUSDT"}
    assert set(features["tf"]) == {"1m", "5m"}
    assert not features.columns.has_duplicates
    assert not [column for column in features.columns if column.startswith("future_")]
    assert report["min_ts"] is not None
    assert report["max_ts"] is not None
    assert quality_report["status"] == "ok"
    assert {"status", "reason", "rows", "min_ts", "max_ts"}.issubset(quality_report)
    assert quality_report["live_trading_enabled"] is False
    assert quality_report["order_submission_enabled"] is False
    assert quality_report["real_order_submission_enabled"] is False
    assert quality_report["exchange_private_access"] is False
    assert {
        item["path"]: (item["symbol_source"], item["symbol_inferred"], item["inferred_symbol"])
        for item in report["raw_file_reports"]
    } == {
        str(tmp_path / "raw" / "BTCUSDT_1m_fixture.parquet"): ("filename", True, "BTCUSDT"),
        str(tmp_path / "raw" / "ETHUSDT_1m_fixture.csv"): ("filename", True, "ETHUSDT"),
    }

    for _, group in features.groupby(["symbol", "tf"]):
        assert group["ts_ms"].is_monotonic_increasing


def test_builder_blocks_main_overwrite_without_backup(tmp_path: Path) -> None:
    module = load_module()
    existing = module.normalize_raw(market_frame("BTCUSDT", rows=5))
    main_path = tmp_path / "features" / "market_features_60d.parquet"
    main_path.parent.mkdir(parents=True, exist_ok=True)
    existing.to_parquet(main_path, index=False)

    report = run_builder(tmp_path, update_main=True, backup=False)

    assert report["status"] == "blocked"
    assert report["reason"] == module.MAIN_OVERWRITE_BLOCK_REASON


def test_main_features_update_removes_existing_lookahead_columns(tmp_path: Path) -> None:
    module = load_module()
    existing = module.normalize_raw(market_frame("BTCUSDT", rows=5))
    existing["future_ret_1"] = 0.01
    main_path = tmp_path / "features" / "market_features_60d.parquet"
    main_path.parent.mkdir(parents=True, exist_ok=True)
    existing.to_parquet(main_path, index=False)

    report = run_builder(tmp_path, update_main=True, backup=True)
    updated = pd.read_parquet(main_path)

    assert report["status"] == "ok"
    assert not [column for column in updated.columns if column.startswith("future_")]
    assert not [
        column
        for column in report["main_features"]["columns"]
        if column.startswith("future_")
    ]


def test_missing_raw_files_returns_controlled_blocked_report(tmp_path: Path) -> None:
    module = load_module()

    report = module.run_phase22_feature_build(
        raw_dir=tmp_path / "missing",
        symbols=["BTCUSDT", "ETHUSDT"],
        interval="1m",
        output_path=tmp_path / "features.parquet",
        main_features_path=tmp_path / "main.parquet",
        sqlite_path=tmp_path / "db.sqlite",
        sqlite_table="market_features",
        update_main_features=False,
        backup=False,
        backups_dir=tmp_path / "backups",
        features_report_path=tmp_path / "features_report.json",
        quality_report_path=tmp_path / "quality_report.json",
    )

    assert report["status"] == "blocked"
    assert report["reason"].startswith("missing_raw_files:")
    assert json.loads((tmp_path / "features_report.json").read_text(encoding="utf-8"))[
        "status"
    ] == "blocked"


def test_symbol_inference_from_parquet_filename_when_symbol_column_missing(tmp_path: Path) -> None:
    module = load_module()
    path = write_raw_parquet(tmp_path, "BTCUSDT", include_symbol=False)
    table = module.read_table(path)

    prepared, metadata = module.prepare_raw_for_normalization(
        table,
        path,
        allowed_symbols={"BTCUSDT", "ETHUSDT"},
        interval="1m",
    )
    normalized = module.normalize_raw(prepared)

    assert metadata == {
        "symbol_inferred": True,
        "inferred_symbol": "BTCUSDT",
        "symbol_source": "filename",
    }
    assert set(normalized["symbol"]) == {"BTCUSDT"}


def test_symbol_inference_from_csv_filename_when_symbol_column_missing(tmp_path: Path) -> None:
    module = load_module()
    path = write_raw_csv(tmp_path, "ETHUSDT", include_symbol=False)
    table = module.read_table(path)

    prepared, metadata = module.prepare_raw_for_normalization(
        table,
        path,
        allowed_symbols={"BTCUSDT", "ETHUSDT"},
        interval="1m",
    )
    normalized = module.normalize_raw(prepared)

    assert metadata["symbol_inferred"] is True
    assert metadata["inferred_symbol"] == "ETHUSDT"
    assert metadata["symbol_source"] == "filename"
    assert set(normalized["symbol"]) == {"ETHUSDT"}


def test_invalid_symbol_filename_is_blocked_without_zeroing_valid_files(tmp_path: Path) -> None:
    module = load_module()
    raw_dir = tmp_path / "raw"
    write_raw_parquet(raw_dir, "BTCUSDT", include_symbol=False)
    invalid = market_frame("BTCUSDT").drop(columns=["symbol"])
    invalid.to_parquet(raw_dir / "DOGEUSDT_1m_fixture.parquet", index=False)

    report = module.run_phase22_feature_build(
        raw_dir=raw_dir,
        symbols=["BTCUSDT", "ETHUSDT"],
        interval="1m",
        output_path=tmp_path / "features.parquet",
        main_features_path=tmp_path / "main.parquet",
        sqlite_path=tmp_path / "db.sqlite",
        sqlite_table="market_features",
        update_main_features=False,
        backup=False,
        backups_dir=tmp_path / "backups",
        features_report_path=tmp_path / "features_report.json",
        quality_report_path=tmp_path / "quality_report.json",
    )

    assert report["status"] == "ok"
    assert report["raw_files_ok"] == 1
    assert report["raw_files_blocked"] == 1
    assert str(raw_dir / "DOGEUSDT_1m_fixture.parquet") in report["blocked_paths"]
    assert report["rows"] > 0
    assert report["symbols"] == ["BTCUSDT"]
    blocked = [item for item in report["raw_file_reports"] if item["status"] == "blocked"][0]
    assert blocked["symbol_source"] == "missing"
    assert blocked["reason"].startswith("missing_symbol_and_uninferable_filename:")


def test_duplicate_csv_and_parquet_prefers_parquet_and_deduplicates_candles(tmp_path: Path) -> None:
    module = load_module()
    raw_dir = tmp_path / "raw"
    write_raw_parquet(raw_dir, "BTCUSDT", include_symbol=False)
    write_raw_csv(raw_dir, "BTCUSDT", include_symbol=False)

    report = module.run_phase22_feature_build(
        raw_dir=raw_dir,
        symbols=["BTCUSDT", "ETHUSDT"],
        interval="1m",
        output_path=tmp_path / "features.parquet",
        main_features_path=tmp_path / "main.parquet",
        sqlite_path=tmp_path / "db.sqlite",
        sqlite_table="market_features",
        update_main_features=False,
        backup=False,
        backups_dir=tmp_path / "backups",
        features_report_path=tmp_path / "features_report.json",
        quality_report_path=tmp_path / "quality_report.json",
    )
    features = pd.read_parquet(tmp_path / "features.parquet")

    assert report["status"] == "ok"
    assert report["raw_files_ok"] == 1
    assert report["raw_files_skipped"] == 1
    assert str(raw_dir / "BTCUSDT_1m_fixture.csv") in report["skipped_paths"]
    one_minute = features[features["tf"] == "1m"]
    assert len(one_minute) == len(market_frame("BTCUSDT"))
    assert not one_minute.duplicated(subset=["symbol", "tf", "ts_ms"]).any()


def test_phase22_builder_preserves_paper_shadow_only_safety(tmp_path: Path) -> None:
    report = run_builder(tmp_path)
    source = MODULE_PATH.read_text(encoding="utf-8").lower()

    assert report["runtime_mode"] == "paper"
    assert report["shadow_only"] is True
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    for forbidden in ["create_order", "fetch_balance", "freqtradeapi", "order_submission_enabled=true"]:
        assert forbidden not in source
    assert not (tmp_path / "active_freqtrade_signals.json").exists()
