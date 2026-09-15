from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd
import pytest

from smartcrypto.data.feature_builder import build_market_feature_frame
from smartcrypto.learning.paper_autolearning import (
    qlib_market_context_economic_challenger as market_context,
)
from smartcrypto.learning.qlib_v3_prospective import (
    economic_shadow_decision_producer as shadow,
)
from smartcrypto.learning.qlib_v3_prospective.contracts import EvidenceError
from smartcrypto.qlib_engine.market_features_refresh import (
    clear_canonical_rebuilt_feature_snapshot,
    get_canonical_rebuilt_feature_snapshot,
    refresh_qlib_market_features,
)


def _raw_frame(periods: int = 620) -> pd.DataFrame:
    base_time = datetime(2026, 9, 10, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for symbol, base_price in (
        ("BTCUSDT", 62000.0),
        ("ETHUSDT", 3200.0),
    ):
        price = base_price
        for index in range(periods):
            timestamp = base_time + timedelta(
                minutes=5 * index
            )
            wave = (
                8.0 * np.sin(index / 9.0)
                + 4.0 * np.sin(index / 17.0)
            )
            close = max(100.0, price + wave)
            rows.append(
                {
                    "symbol": symbol,
                    "pair": symbol.replace(
                        "USDT",
                        "/USDT:USDT",
                    ),
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


@pytest.fixture(autouse=True)
def _clear_snapshot() -> Iterator[None]:
    clear_canonical_rebuilt_feature_snapshot()
    yield
    clear_canonical_rebuilt_feature_snapshot()


def _run_refresh(tmp_path: Path) -> tuple[pd.DataFrame, Path, dict[str, object]]:
    raw = _raw_frame()
    source = tmp_path / "raw.parquet"
    output = tmp_path / "market_features_60d.parquet"
    report_path = tmp_path / "refresh_report.json"
    raw.to_parquet(source, index=False)
    now = pd.to_datetime(
        raw["ts"],
        utc=True,
    ).max().to_pydatetime()

    report = refresh_qlib_market_features(
        source_path=source,
        existing_features_path=(
            tmp_path / "missing_existing.parquet"
        ),
        output_path=output,
        report_path=report_path,
        public_download_enabled=False,
        max_source_age_minutes=1,
        now=now,
    )
    return raw, output, report


def test_refresh_publishes_exact_full_history_rebuild_in_process(
    tmp_path: Path,
) -> None:
    raw, output, report = _run_refresh(tmp_path)

    assert report["status"] == "ok"
    assert report["canonical_rebuilt_snapshot_cached"] is True

    snapshot = get_canonical_rebuilt_feature_snapshot(output)
    assert snapshot is not None

    expected = build_market_feature_frame(raw)
    columns = [
        "symbol",
        "tf",
        "ts",
        *market_context.MARKET_SOURCE_COLUMNS,
    ]
    pd.testing.assert_frame_equal(
        snapshot.loc[:, columns].reset_index(drop=True),
        expected.loc[:, columns].reset_index(drop=True),
        check_exact=True,
    )


def test_shadow_bridge_uses_refresh_rebuild_and_matches_canonical_rematerialization(
    tmp_path: Path,
) -> None:
    raw, output, report = _run_refresh(tmp_path)
    assert report["status"] == "ok"

    bridged, source = shadow._canonical_market_snapshot(
        output,
        required_symbols={"BTCUSDT", "ETHUSDT"},
    )
    canonical = market_context._rematerialize_market_features(
        raw.loc[
            :,
            [
                "symbol",
                "tf",
                "ts",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ],
        ]
    )

    assert source == "refresh_full_history_canonical_cache"

    columns = [
        "symbol",
        "ts",
        "available_at_utc",
        *market_context.MARKET_SOURCE_COLUMNS,
    ]
    left = bridged.loc[:, columns].reset_index(drop=True)
    right = canonical.loc[:, columns].reset_index(drop=True)
    pd.testing.assert_frame_equal(
        left,
        right,
        check_exact=True,
    )


def test_snapshot_invalidates_after_output_file_mutation(
    tmp_path: Path,
) -> None:
    _, output, report = _run_refresh(tmp_path)
    assert report["canonical_rebuilt_snapshot_cached"] is True
    assert get_canonical_rebuilt_feature_snapshot(output) is not None

    payload = output.read_bytes()
    output.write_bytes(payload + b"\n")

    assert get_canonical_rebuilt_feature_snapshot(output) is None
    with pytest.raises(
        EvidenceError,
        match="shadow_canonical_refresh_snapshot_unavailable",
    ):
        shadow._canonical_market_snapshot(
            output,
            required_symbols={"BTCUSDT"},
        )
