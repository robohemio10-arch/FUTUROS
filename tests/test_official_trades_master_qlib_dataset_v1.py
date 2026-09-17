from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from smartcrypto.research.trades_master_official.qlib_dataset import (
    FEATURE_FORMULAS,
    QlibDatasetValidationError,
    _build_feature_contract,
    _build_split_manifest,
    _build_target_store,
    build_pit_feature_dataset,
)


def market_frame(
    *,
    symbol: str,
    start: str,
    periods: int,
) -> pd.DataFrame:
    timestamp = pd.date_range(
        start,
        periods=periods,
        freq="1min",
        tz="UTC",
    )

    base = 100.0 + np.arange(periods, dtype=float) * 0.1

    frame = pd.DataFrame(
        {
            "timestamp": timestamp,
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base + 0.2,
            "volume": 10.0 + np.arange(periods, dtype=float),
            "symbol": symbol,
        }
    )

    return frame.set_index("timestamp", drop=False)


def master_frame(
    rows: list[dict[str, object]],
) -> pd.DataFrame:
    return pd.DataFrame(rows)


def freeze_payload() -> dict:
    """Return the minimal canonical WQ6 design-freeze shape for tests."""

    features = [{"name": name} for name in FEATURE_FORMULAS]

    return {
        "feature_contract": {
            "ordered_features": features,
        },
        "label_contract": {
            "ordered_labels": [
                {
                    "name": "label_economic_net_pnl",
                },
                {
                    "name": "label_is_profitable",
                },
            ],
        },
        "metadata_contract": {
            "columns": [
                "trade_sequence",
                "order_id_raw",
                "symbol",
                "side",
                "open_time_utc",
                "feature_cutoff_utc",
                "market_source",
            ],
            "metadata_is_model_input": False,
        },
    }


def canonical_row(
    *,
    sequence: int,
    opened: str,
    closed: str,
    order_id: str,
    symbol: str = "BTC_USDT",
    side: str = "long",
    pnl: float = 1.0,
) -> dict[str, object]:
    return {
        "trade_sequence": sequence,
        "order_id_raw": order_id,
        "symbol": symbol,
        "side": side,
        "horario_abertura": opened,
        "horario_fechamento": closed,
        "economic_net_pnl": pnl,
    }


def test_feature_cutoff_uses_only_closed_candles() -> None:
    market = market_frame(
        symbol="BTCUSDT",
        start="2026-01-01T00:00:00Z",
        periods=120,
    )

    master = master_frame(
        [
            canonical_row(
                sequence=1,
                opened="2026-01-01T01:00:42Z",
                closed="2026-01-01T01:10:00Z",
                order_id="a" * 24,
            )
        ]
    )

    dataset, evidence = build_pit_feature_dataset(
        master,
        market_by_symbol={
            "BTCUSDT": market,
        },
        expected_eligible_rows=1,
    )

    assert evidence["feature_complete_rows"] == 1

    row = dataset.iloc[0]

    assert row["feature_cutoff_utc"] == pd.Timestamp("2026-01-01T01:00:00Z")

    assert row["feature_cutoff_utc"] <= row["open_time_utc"]

    expected = (
        float(
            market.loc[
                pd.Timestamp("2026-01-01T00:59:00Z"),
                "close",
            ]
        )
        / float(
            market.loc[
                pd.Timestamp("2026-01-01T00:58:00Z"),
                "close",
            ]
        )
        - 1.0
    )

    assert row["feature_ret_close_1m"] == pytest.approx(expected)


def test_missing_required_market_minute_excludes_row() -> None:
    market = market_frame(
        symbol="BTCUSDT",
        start="2026-01-01T00:00:00Z",
        periods=120,
    ).drop(index=pd.Timestamp("2026-01-01T00:45:00Z"))

    master = master_frame(
        [
            canonical_row(
                sequence=1,
                opened="2026-01-01T01:00:42Z",
                closed="2026-01-01T01:10:00Z",
                order_id="b" * 24,
            )
        ]
    )

    dataset, evidence = build_pit_feature_dataset(
        master,
        market_by_symbol={
            "BTCUSDT": market,
        },
        expected_eligible_rows=1,
    )

    assert dataset.empty
    assert evidence["pit_eligible_excluded_rows"] == 1
    assert evidence["exclusion_counts"] == {"market_window_incomplete": 1}


def test_legacy_open_time_is_explicitly_excluded() -> None:
    market = market_frame(
        symbol="BTCUSDT",
        start="2026-01-01T00:00:00Z",
        periods=120,
    )

    master = master_frame(
        [
            canonical_row(
                sequence=1,
                opened="SOURCE_NOT_AVAILABLE_FROM_PRINT",
                closed="2026-01-01T01:10:00Z",
                order_id="c" * 24,
            ),
            canonical_row(
                sequence=2,
                opened="2026-01-01T01:00:42Z",
                closed="2026-01-01T01:10:00Z",
                order_id="d" * 24,
            ),
        ]
    )

    dataset, evidence = build_pit_feature_dataset(
        master,
        market_by_symbol={
            "BTCUSDT": market,
        },
        expected_eligible_rows=1,
    )

    assert len(dataset) == 1
    assert evidence["legacy_excluded_rows"] == 1
    assert evidence["pit_eligible_excluded_rows"] == 0


def test_feature_contract_preserves_frozen_order() -> None:
    market = market_frame(
        symbol="BTCUSDT",
        start="2026-01-01T00:00:00Z",
        periods=120,
    )

    master = master_frame(
        [
            canonical_row(
                sequence=1,
                opened="2026-01-01T01:00:42Z",
                closed="2026-01-01T01:10:00Z",
                order_id="e" * 24,
            )
        ]
    )

    dataset, _ = build_pit_feature_dataset(
        master,
        market_by_symbol={
            "BTCUSDT": market,
        },
        expected_eligible_rows=1,
    )

    contract = _build_feature_contract(
        dataset,
        design_freeze=freeze_payload(),
        source_datasets=["synthetic"],
    )

    assert contract["validation_status"] == "ok"
    assert contract["feature_columns"] == list(FEATURE_FORMULAS)
    assert contract["label_columns"] == [
        "label_economic_net_pnl",
        "label_is_profitable",
    ]
    assert not any(column.startswith("label_") for column in contract["feature_columns"])
    assert not any(column.startswith("future_ret_") for column in contract["feature_columns"])


def test_target_and_walkforward_lineage_is_consistent() -> None:
    market = market_frame(
        symbol="BTCUSDT",
        start="2026-01-01T00:00:00Z",
        periods=16_000,
    )

    rows = []

    for index in range(12):
        opened = pd.Timestamp("2026-01-01T01:00:42Z") + pd.Timedelta(hours=index * 12)
        closed = opened + pd.Timedelta(minutes=5)

        rows.append(
            canonical_row(
                sequence=index + 1,
                opened=opened.isoformat(),
                closed=closed.isoformat(),
                order_id=f"{index + 1:024x}",
                pnl=1.0 if index % 2 == 0 else -0.5,
            )
        )

    dataset, evidence = build_pit_feature_dataset(
        master_frame(rows),
        market_by_symbol={
            "BTCUSDT": market,
        },
        expected_eligible_rows=12,
    )

    assert evidence["pit_eligible_excluded_rows"] == 0

    contract = _build_feature_contract(
        dataset,
        design_freeze=freeze_payload(),
        source_datasets=["synthetic"],
    )

    from smartcrypto.learning.feature_contracts.dataset_manifest import (
        frame_hash,
    )

    dataset_hash = frame_hash(dataset)

    target_store = _build_target_store(
        dataset,
        feature_contract_hash=contract["contract_hash"],
        dataset_hash=dataset_hash,
    )

    split_manifest = _build_split_manifest(
        dataset,
        feature_contract=contract,
        dataset_hash=dataset_hash,
        target_store_hash=target_store["target_store_hash"],
    )

    assert split_manifest["validation_status"] == "ok"
    assert split_manifest["leakage_status"] == "ok"
    assert split_manifest["true_label_interval_overlap_count"] == 0
    assert split_manifest["feature_contract_hash"] == contract["contract_hash"]
    assert split_manifest["dataset_hash"] == dataset_hash
    assert split_manifest["target_store_hash"] == target_store["target_store_hash"]


def test_rsi_and_volatility_are_finite() -> None:
    market = market_frame(
        symbol="BTCUSDT",
        start="2026-01-01T00:00:00Z",
        periods=120,
    )

    master = master_frame(
        [
            canonical_row(
                sequence=1,
                opened="2026-01-01T01:00:42Z",
                closed="2026-01-01T01:10:00Z",
                order_id="f" * 24,
            )
        ]
    )

    dataset, _ = build_pit_feature_dataset(
        master,
        market_by_symbol={
            "BTCUSDT": market,
        },
        expected_eligible_rows=1,
    )

    row = dataset.iloc[0]

    assert math.isfinite(float(row["feature_rsi_14"]))
    assert 0.0 <= float(row["feature_rsi_14"]) <= 100.0

    assert math.isfinite(float(row["feature_pre_entry_volatility_20"]))


def test_unsupported_symbol_fails_closed_by_exclusion() -> None:
    market = market_frame(
        symbol="BTCUSDT",
        start="2026-01-01T00:00:00Z",
        periods=120,
    )

    master = master_frame(
        [
            canonical_row(
                sequence=1,
                opened="2026-01-01T01:00:42Z",
                closed="2026-01-01T01:10:00Z",
                order_id="1" * 24,
                symbol="SOL_USDT",
            )
        ]
    )

    dataset, evidence = build_pit_feature_dataset(
        master,
        market_by_symbol={
            "BTCUSDT": market,
        },
        expected_eligible_rows=1,
    )

    assert dataset.empty
    assert evidence["pit_eligible_excluded_rows"] == 1
    assert evidence["exclusion_counts"] == {"unsupported_symbol:SOL_USDT": 1}


def test_duplicate_feature_name_freeze_is_rejected() -> None:
    payload = freeze_payload()
    payload["feature_contract"]["ordered_features"].append(
        {"name": payload["feature_contract"]["ordered_features"][0]["name"]}
    )

    market = market_frame(
        symbol="BTCUSDT",
        start="2026-01-01T00:00:00Z",
        periods=120,
    )

    master = master_frame(
        [
            canonical_row(
                sequence=1,
                opened="2026-01-01T01:00:42Z",
                closed="2026-01-01T01:10:00Z",
                order_id="2" * 24,
            )
        ]
    )

    dataset, _ = build_pit_feature_dataset(
        master,
        market_by_symbol={
            "BTCUSDT": market,
        },
        expected_eligible_rows=1,
    )

    with pytest.raises(
        QlibDatasetValidationError,
        match="feature_contract_whitelist_drift",
    ):
        _build_feature_contract(
            dataset,
            design_freeze=payload,
            source_datasets=["synthetic"],
        )
