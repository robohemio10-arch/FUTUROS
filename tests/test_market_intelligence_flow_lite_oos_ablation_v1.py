from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from smartcrypto.research.aibot_parity import market_intelligence_flow_lite_oos_ablation as flow


class ScoreModel:
    def fit(self, x: np.ndarray, y: np.ndarray) -> ScoreModel:
        assert x.shape[1] == len(flow.FLOW_FEATURES) + 1
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        return x[:, 0] + x[:, 1] * 0.01


def _candle(symbol: str, opened: pd.Timestamp, ratio: float = 0.6) -> flow.FlowCandle:
    minute = int(opened.timestamp() * 1000)
    day = opened.strftime("%Y-%m-%d")
    relative = f"futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip"
    row = [
        str(minute),
        "10",
        "11",
        "9",
        "10",
        "10",
        str(minute + 59999),
        "100",
        "7",
        str(10 * ratio),
        str(100 * ratio),
        "0",
    ]
    return flow.parse_klines(
        [list(flow.KLINE_HEADER), row],
        symbol=symbol,
        relative=relative,
        archive_sha256="a" * 64,
        required_minutes={minute},
    )[minute]


def _window(symbol: str, decision: pd.Timestamp) -> list[flow.FlowCandle]:
    return [
        _candle(symbol, decision.floor("min") - pd.Timedelta(minutes=i)) for i in range(16, 1, -1)
    ]


def _fixture() -> tuple[pd.DataFrame, dict[tuple[str, int], flow.FlowCandle], list[dict[str, Any]]]:
    rows: list[dict[str, object]] = []
    candles: dict[tuple[str, int], flow.FlowCandle] = {}
    start = pd.Timestamp("2026-01-02T00:00:30Z")
    for i in range(60):
        decision = start + pd.Timedelta(hours=i * 2)
        symbol = "BTCUSDT" if i % 2 == 0 else "ETHUSDT"
        rows.append(
            {
                "trade_sequence": i,
                "order_id": f"fixture-{i}",
                "symbol": symbol,
                "side": "long" if i % 2 == 0 else "short",
                "open_time_utc": decision,
                "close_time_utc": decision + pd.Timedelta(minutes=5),
                "feature_cutoff_utc": decision - pd.Timedelta(seconds=1),
                "feature_side_long": float(i % 7),
                "label_economic_net_pnl": float(-3 if i % 3 == 0 else 2),
                "capital_proxy_usdt": 100.0,
                "capital_hours": 100.0 / 12,
            }
        )
        for candle in _window(symbol, decision):
            candles[(symbol, candle.open_time_ms)] = candle
    splits = [
        {
            "split_id": f"fold-{i}",
            "_train_indices": list(range(end - 10)),
            "_validation_indices": list(range(end - 10, end)),
            "_test_indices": list(range(end, end + 10)),
        }
        for i, end in enumerate((30, 40, 50), start=1)
    ]
    return pd.DataFrame(rows), candles, splits


def _evaluate(dataset: pd.DataFrame, splits: list[dict[str, Any]]) -> dict[str, Any]:
    return flow.evaluate_flow_lite(
        dataset, ("feature_side_long",), splits, model_factory=ScoreModel
    )


def test_real_raw_fields_and_closed_weighted_rolling_features() -> None:
    window = _window("BTCUSDT", pd.Timestamp("2026-01-02T00:00:30Z"))
    window = [
        replace(c, taker_buy_base_volume=2, taker_buy_quote_volume=20) for c in window[:10]
    ] + [replace(c, taker_buy_base_volume=8, taker_buy_quote_volume=80) for c in window[10:]]
    features = flow.candle_features(window)
    assert set(features) == set(flow.FLOW_FEATURES)
    assert features["taker_buy_ratio"] == pytest.approx(0.8)
    assert features["signed_taker_volume_ratio"] == pytest.approx(0.6)
    assert features["taker_buy_quote_ratio"] == pytest.approx(0.8)
    assert features["trade_count"] == 7
    assert features["rolling_signed_flow_5m"] == pytest.approx(0.6)
    assert features["rolling_signed_flow_15m"] == pytest.approx(-0.2)
    assert features["flow_acceleration_5m_vs_15m"] == pytest.approx(0.8)
    assert window[-1].available_at_utc == window[-1].event_time_utc + pd.Timedelta(seconds=60)
    assert pd.Timestamp(window[-1].close_time_ms, unit="ms", tz="UTC") < window[-1].event_time_utc


@pytest.mark.parametrize(
    "column,value", [(6, "0"), (5, "nan"), (9, "11"), (10, "101"), (8, "-1"), (8, "1.5")]
)
def test_partial_invalid_or_fabricated_kline_fields_fail_closed(column: int, value: str) -> None:
    minute = int(pd.Timestamp("2026-01-02T00:00:00Z").timestamp() * 1000)
    row = [
        str(minute),
        "10",
        "11",
        "9",
        "10",
        "10",
        str(minute + 59999),
        "100",
        "7",
        "6",
        "60",
        "0",
    ]
    row[column] = value
    with pytest.raises(ValueError):
        flow.parse_klines(
            [list(flow.KLINE_HEADER), row],
            symbol="BTCUSDT",
            relative="futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-01-02.zip",
            archive_sha256="a" * 64,
            required_minutes={minute},
        )


def test_missing_real_fields_and_wrong_market_block() -> None:
    with pytest.raises(flow.FlowLiteError, match="real_kline_fields_missing"):
        flow.parse_klines(
            [list(flow.KLINE_HEADER)[:-1]],
            symbol="BTCUSDT",
            relative="futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-01-02.zip",
            archive_sha256="a" * 64,
            required_minutes=set(),
        )
    with pytest.raises(flow.FlowLiteError, match="usd_m_kline_source_required"):
        flow.parse_klines(
            [],
            symbol="BTCUSDT",
            relative="spot/daily/klines/BTCUSDT/1m/x.zip",
            archive_sha256="a" * 64,
            required_minutes=set(),
        )


def test_future_and_partial_candle_cannot_enter_decision_features() -> None:
    dataset, candles, _ = _fixture()
    one = dataset.iloc[:1]
    initial, _ = flow.align_flow_lite(one, candles)
    future = _candle(
        "BTCUSDT",
        pd.Timestamp(one.iloc[0].open_time_utc).floor("min") - pd.Timedelta(minutes=1),
        0.99,
    )
    candles[(future.symbol, future.open_time_ms)] = future
    later, coverage = flow.align_flow_lite(one, candles)
    pd.testing.assert_frame_equal(initial, later)
    assert coverage["future_join_count"] == 0
    partial = replace(future, close_time_ms=future.open_time_ms + 20000)
    candles[(partial.symbol, partial.open_time_ms)] = partial
    with pytest.raises(flow.FlowLiteError, match="partial_or_non_1m_candle"):
        flow.align_flow_lite(one, candles)


def test_exact_availability_boundary_and_next_minute_staleness() -> None:
    dataset, candles, _ = _fixture()
    one = dataset.iloc[:1].copy()
    one["open_time_utc"] = one["open_time_utc"].dt.floor("min")
    one["feature_cutoff_utc"] = one["open_time_utc"] - pd.Timedelta(seconds=1)
    aligned, coverage = flow.align_flow_lite(one, candles)
    assert coverage["available_count"] == 1
    assert aligned.iloc[0].flow_available_at_utc == one.iloc[0].open_time_utc
    one["open_time_utc"] += pd.Timedelta(minutes=1)
    _, coverage = flow.align_flow_lite(one, candles)
    assert coverage["statuses"]["STALE"] == 1


def test_gap_zero_volume_and_missing_are_not_imputed() -> None:
    dataset, candles, _ = _fixture()
    one = dataset.iloc[:1]
    window = _window("BTCUSDT", pd.Timestamp(one.iloc[0].open_time_utc))
    missing_middle = dict(candles)
    del missing_middle[("BTCUSDT", window[7].open_time_ms)]
    aligned, coverage = flow.align_flow_lite(one, missing_middle)
    assert coverage["statuses"]["GAP"] == 1
    assert aligned[list(flow.FLOW_FEATURES)].isna().all().all()
    last = window[-1]
    candles[(last.symbol, last.open_time_ms)] = replace(
        last, volume=0, quote_volume=0, taker_buy_base_volume=0, taker_buy_quote_volume=0
    )
    _, coverage = flow.align_flow_lite(one, candles)
    assert coverage["statuses"]["ZERO_VOLUME"] == 1
    _, coverage = flow.align_flow_lite(one, {})
    assert coverage["statuses"]["MISSING"] == 1


def test_source_plan_includes_previous_day_and_never_outcomes() -> None:
    dataset, _, _ = _fixture()
    one = dataset.iloc[:1][["symbol", "open_time_utc"]]
    plan = flow._minute_plan(one)
    assert ("BTCUSDT", "2026-01-01") in plan
    assert sum(map(len, plan.values())) == 15


def test_missing_cache_does_not_download_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset, _, _ = _fixture()
    from smartcrypto.research.aibot_parity import (
        market_intelligence_pit_source_foundation as source,
    )

    def forbidden(*_: object) -> bytes:
        raise AssertionError("no_network_by_default")

    monkeypatch.setattr(source, "download", forbidden)
    with pytest.raises(flow.PITSourceError, match="SOURCE_CACHE_MISSING"):
        flow.load_flow_candles(dataset.iloc[:1], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_only_missing_public_archives_enable_cache_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset, _, _ = _fixture()
    calls: list[bool] = []
    one = dataset.iloc[1:2]
    plan = flow._minute_plan(one)
    symbol, day = next(iter(plan))
    relative = f"futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing fixture")
    target.with_suffix(".zip.CHECKSUM").write_text("fixture", encoding="ascii")

    def archived(
        relative: str, cache: Path, *, allow_download: bool
    ) -> tuple[list[list[str]], str]:
        calls.append(allow_download)
        return [list(flow.KLINE_HEADER)], "a" * 64

    monkeypatch.setattr(flow, "archive_rows", archived)
    _, audit = flow.load_flow_candles(one, tmp_path, allow_download=True)
    assert calls == [False]
    assert audit["cache_write_performed"] is False


def test_frozen_walkforward_uses_primary_unfiltered_control() -> None:
    dataset, candles, splits = _fixture()
    aligned, coverage = flow.align_flow_lite(dataset, candles)
    report = _evaluate(aligned, splits)
    assert coverage["feature_coverage"] == 1
    assert report["oos_universe_count"] == 30
    assert set(report["variants"]) == {"NO_BR10_CONTROL", "FLOW_LITE_ONLY"}
    baseline, arm = report["variants"].values()
    assert baseline["trade_count"] == 30
    assert baseline["cohort_identity_sha256"] == arm["cohort_identity_sha256"]
    assert arm["delta_net_pnl_vs_no_br10_control"] == pytest.approx(
        arm["net_pnl_usdt"] - baseline["net_pnl_usdt"]
    )
    assert report["anti_leakage_status"] == "PASS"
    assert report["aggtrades_used"] is False
    assert report["funding_recalibrated"] is False


def test_heldout_outcomes_do_not_change_thresholds_or_selection() -> None:
    dataset, candles, splits = _fixture()
    aligned, _ = flow.align_flow_lite(dataset, candles)
    initial = _evaluate(aligned, splits)["variants"]["FLOW_LITE_ONLY"]
    changed = aligned.copy()
    changed.loc[50:59, "label_economic_net_pnl"] *= -1000
    changed.loc[50:59, "close_time_utc"] += pd.Timedelta(days=30)
    changed.loc[50:59, "capital_hours"] *= 1000
    later = _evaluate(changed, splits)["variants"]["FLOW_LITE_ONLY"]
    assert initial["selection_identity_sha256"] == later["selection_identity_sha256"]
    assert [f["threshold"] for f in initial["folds"]] == [f["threshold"] for f in later["folds"]]
    assert all(f["threshold_source"] == "past_validation_only" for f in later["folds"])


@pytest.mark.parametrize(
    "feature", ["label_economic_net_pnl", "close_time_utc", "capital_hours", "MFE", "MAE"]
)
def test_outcome_fields_cannot_be_model_features(feature: str) -> None:
    dataset, candles, splits = _fixture()
    aligned, _ = flow.align_flow_lite(dataset, candles)
    with pytest.raises(flow.FlowLiteError, match="feature_outside_frozen_pretrade_contract"):
        flow.evaluate_flow_lite(aligned, (feature,), splits, model_factory=ScoreModel)


def test_uncovered_cohort_future_alignment_and_overlapping_folds_block() -> None:
    dataset, candles, splits = _fixture()
    aligned, _ = flow.align_flow_lite(dataset, candles)
    missing = aligned.copy()
    missing.loc[59, "taker_buy_ratio"] = np.nan
    with pytest.raises(flow.FlowLiteError, match="complete_frozen_cohort_required"):
        _evaluate(missing, splits)
    future = aligned.copy()
    future.loc[59, "flow_event_time_utc"] = future.loc[59, "open_time_utc"]
    future.loc[59, "flow_available_at_utc"] = future.loc[59, "open_time_utc"] + pd.Timedelta(
        seconds=60
    )
    with pytest.raises(flow.FlowLiteError, match="future_join_blocked"):
        _evaluate(future, splits)
    splits[-1]["_test_indices"] = splits[0]["_test_indices"]
    with pytest.raises(flow.FlowLiteError, match="oos_fold_overlap"):
        _evaluate(aligned, splits)


def test_no_write_builder_and_missing_source_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset, candles, splits = _fixture()
    monkeypatch.setattr(
        flow, "_load_bundle", lambda **_: (dataset, ("feature_side_long",), splits, {})
    )
    monkeypatch.setattr(
        flow,
        "load_flow_candles",
        lambda *_args, **_kwargs: (candles, {"real_fields_available": list(flow.RAW_FIELDS)}),
    )
    report = flow.build_flow_lite_oos_ablation(
        project_root=tmp_path / "project", cache_root=tmp_path / "cache", model_factory=ScoreModel
    )
    assert report["status"] == "ok"
    assert report["write_performed"] is False
    assert report["availability_classification"] == "PIT_MODELLED_CONSERVATIVE"
    assert report["coverage"]["oos_available_count"] == 30
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(flow, "load_flow_candles", lambda *_args, **_kwargs: ({}, {}))
    blocked = flow.build_flow_lite_oos_ablation(
        project_root=tmp_path / "project", cache_root=tmp_path / "cache", model_factory=ScoreModel
    )
    assert blocked["status"] == "blocked"
    assert blocked["anti_leakage_status"] == "BLOCKED"
    assert blocked["coverage"]["available_count"] == 0
    json.dumps(blocked, allow_nan=False)
