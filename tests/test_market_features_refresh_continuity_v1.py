from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from smartcrypto.data.feature_builder import build_market_feature_frame
from smartcrypto.qlib_engine.market_features_refresh import (
    CONTINUITY_GUARD_FEATURE_COLUMNS,
    _merge_features_preserving_non_null,
    _select_affected_group_full_history_raw,
    inspect_feature_continuity,
    inspect_group_freshness,
)


def _raw_frame(periods: int = 620) -> pd.DataFrame:
    base_time = datetime(2026, 6, 1, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for symbol, base_price in (("BTCUSDT", 62000.0), ("ETHUSDT", 3200.0)):
        price = base_price
        for index in range(periods):
            timestamp = base_time + timedelta(minutes=5 * index)
            wave = 8.0 * np.sin(index / 9.0) + 4.0 * np.sin(index / 17.0)
            close = max(100.0, price + wave)
            rows.append(
                {
                    "symbol": symbol,
                    "pair": symbol.replace("USDT", "/USDT:USDT"),
                    "tf": "5m",
                    "ts": timestamp,
                    "open": price,
                    "high": max(price, close) + 3.0,
                    "low": min(price, close) - 3.0,
                    "close": close,
                    "volume": 1000.0 + (index % 23) * 13.0,
                }
            )
            price = close
    return pd.DataFrame(rows)


def test_in_memory_feature_builder_preserves_operational_no_lookahead_contract() -> None:
    features = build_market_feature_frame(_raw_frame())

    assert not [column for column in features.columns if column.startswith("future_ret_")]
    assert {"ret_30", "ema_200", "dist_ema200", "vol_120", "trend_score"}.issubset(
        features.columns
    )


def test_continuity_audit_detects_mid_history_warmup_scar() -> None:
    features = build_market_feature_frame(_raw_frame())
    scarred = features.copy()
    symbol_rows = scarred.index[scarred["symbol"].eq("BTCUSDT")]
    scar_indices = symbol_rows[360:390]
    for column in CONTINUITY_GUARD_FEATURE_COLUMNS:
        scarred.loc[scar_indices, column] = np.nan

    report = inspect_feature_continuity(scarred)

    assert report["status"] == "blocked"
    assert report["interior_null_regression_count"] > 0


def test_rebuilt_non_null_features_repair_scar_without_null_regression() -> None:
    raw = _raw_frame()
    original = build_market_feature_frame(raw)
    scarred = original.copy()
    symbol_rows = scarred.index[scarred["symbol"].eq("BTCUSDT")]
    scar_indices = symbol_rows[360:390]
    for column in CONTINUITY_GUARD_FEATURE_COLUMNS:
        scarred.loc[scar_indices, column] = np.nan

    rebuilt = build_market_feature_frame(raw)
    merged = _merge_features_preserving_non_null(existing=scarred, rebuilt=rebuilt)
    report = inspect_feature_continuity(merged)

    assert report["status"] == "ok"
    assert report["interior_null_regression_count"] == 0


def test_overlap_merge_never_replaces_existing_value_with_warmup_null() -> None:
    raw = _raw_frame(periods=620)
    existing = build_market_feature_frame(raw)

    recent_raw = raw.groupby(["symbol", "tf"], sort=False).tail(260).reset_index(drop=True)
    recent_rebuilt = build_market_feature_frame(recent_raw)
    overlap = recent_rebuilt.loc[recent_rebuilt["ema_200"].isna()].copy()
    assert not overlap.empty

    merged = _merge_features_preserving_non_null(existing=existing, rebuilt=recent_rebuilt)
    keys = overlap.loc[:, ["symbol", "tf", "ts"]]
    checked = merged.merge(keys, on=["symbol", "tf", "ts"], how="inner")

    # If the complete historical artifact had a valid EMA200, an incremental warm-up
    # row is not allowed to erase it.
    expected = existing.merge(keys, on=["symbol", "tf", "ts"], how="inner")
    valid_keys = expected.loc[expected["ema_200"].notna(), ["symbol", "tf", "ts"]]
    checked = checked.merge(valid_keys, on=["symbol", "tf", "ts"], how="inner")
    assert not checked.empty
    assert checked["ema_200"].notna().all()



def test_affected_group_rebuild_uses_complete_history_without_ema_restart_drift() -> None:
    raw = _raw_frame(periods=620)
    recent = raw.groupby(["symbol", "tf"], sort=False).tail(260).reset_index(drop=True)

    selected_raw = _select_affected_group_full_history_raw(
        raw_basis=raw,
        recent_raw=recent,
    )
    rebuilt = build_market_feature_frame(selected_raw)
    expected = build_market_feature_frame(raw)

    assert len(selected_raw) == len(raw)
    columns = [
        "symbol",
        "tf",
        "ts",
        "ema_20",
        "ema_50",
        "ema_200",
        "dist_ema200",
        "macd_hist",
        "atr_pct_14",
        "vol_120",
        "trend_score",
    ]
    pd.testing.assert_frame_equal(
        rebuilt.loc[:, columns].reset_index(drop=True),
        expected.loc[:, columns].reset_index(drop=True),
        check_exact=True,
    )


def test_group_freshness_blocks_one_stale_group_even_when_global_max_is_fresh() -> None:
    now = datetime(2026, 9, 8, 15, 0, tzinfo=UTC)
    frame = pd.DataFrame(
        [
            {"symbol": "BTCUSDT", "tf": "5m", "ts": now - timedelta(minutes=5)},
            {"symbol": "ETHUSDT", "tf": "5m", "ts": now - timedelta(minutes=45)},
        ]
    )

    report = inspect_group_freshness(
        frame,
        expected_groups=[("BTCUSDT", "5m"), ("ETHUSDT", "5m")],
        max_source_age_minutes=15,
        now=now,
    )

    assert report["status"] == "blocked"
    assert report["fresh_group_count"] == 1
    assert report["stale_group_count"] == 1
    assert report["missing_group_count"] == 0
    statuses = {(row["symbol"], row["tf"]): row["status"] for row in report["groups"]}
    assert statuses[("BTCUSDT", "5m")] == "ok"
    assert statuses[("ETHUSDT", "5m")] == "stale"

def test_refresh_full_repair_rebuilds_scarred_operational_history(tmp_path) -> None:
    import pytest

    pytest.importorskip("pyarrow")
    from smartcrypto.qlib_engine.market_features_refresh import refresh_qlib_market_features

    raw = _raw_frame(periods=620)
    full = build_market_feature_frame(raw)
    scarred = full.copy()
    symbol_rows = scarred.index[scarred["symbol"].eq("BTCUSDT")]
    scar_indices = symbol_rows[360:390]
    for column in CONTINUITY_GUARD_FEATURE_COLUMNS:
        scarred.loc[scar_indices, column] = np.nan

    existing_path = tmp_path / "market_features_60d.parquet"
    source_path = tmp_path / "recent_raw.parquet"
    report_path = tmp_path / "refresh_report.json"
    scarred.to_parquet(existing_path, index=False)
    raw.groupby(["symbol", "tf"], sort=False).tail(260).to_parquet(source_path, index=False)
    now = pd.to_datetime(raw["ts"], utc=True).max().to_pydatetime()

    report = refresh_qlib_market_features(
        source_path=source_path,
        existing_features_path=existing_path,
        output_path=existing_path,
        report_path=report_path,
        public_download_enabled=False,
        max_source_age_minutes=1,
        now=now,
    )
    written = pd.read_parquet(existing_path)

    assert report["status"] == "ok"
    assert report["rebuild_mode"] == "full_continuity_repair"
    assert report["continuity_repair_triggered"] is True
    assert report["existing_feature_continuity"]["interior_null_regression_count"] > 0
    assert report["final_feature_continuity"]["interior_null_regression_count"] == 0
    assert inspect_feature_continuity(written)["status"] == "ok"


def test_refresh_clean_artifact_rebuilds_affected_groups_from_full_history(tmp_path) -> None:
    import pytest

    pytest.importorskip("pyarrow")
    from smartcrypto.qlib_engine.market_features_refresh import refresh_qlib_market_features

    raw = _raw_frame(periods=620)
    full = build_market_feature_frame(raw)
    existing_path = tmp_path / "market_features_60d.parquet"
    source_path = tmp_path / "recent_raw.parquet"
    report_path = tmp_path / "refresh_report.json"
    full.to_parquet(existing_path, index=False)
    raw.groupby(["symbol", "tf"], sort=False).tail(260).to_parquet(source_path, index=False)
    now = pd.to_datetime(raw["ts"], utc=True).max().to_pydatetime()

    report = refresh_qlib_market_features(
        source_path=source_path,
        existing_features_path=existing_path,
        output_path=existing_path,
        report_path=report_path,
        public_download_enabled=False,
        max_source_age_minutes=1,
        now=now,
    )

    assert report["status"] == "ok"
    assert report["rebuild_mode"] == "affected_group_full_history"
    assert report["affected_group_rebuild_policy"] == "full_history"
    assert report["group_freshness"]["status"] == "ok"
    assert report["continuity_repair_triggered"] is False
    assert report["final_feature_continuity"]["interior_null_regression_count"] == 0
