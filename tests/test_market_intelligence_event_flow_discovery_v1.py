from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from smartcrypto.research.aibot_parity import market_intelligence_event_flow_discovery as discovery
from smartcrypto.research.aibot_parity import market_intelligence_event_flow_source as source


def _archive(cache: Path, symbol: str, day: str, rows: list[list[object]]) -> Path:
    path = cache / f"futures/um/daily/aggTrades/{symbol}/{symbol}-aggTrades-{day}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        ",".join(source.RAW_COLUMNS)
        + "\n"
        + "\n".join(",".join(map(str, row)) for row in rows)
        + "\n"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(path.stem + ".csv", raw)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(".zip.CHECKSUM").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return path


def _event(identifier: int, time: str, quantity: float, maker: bool) -> list[object]:
    return [
        identifier,
        100.0,
        quantity,
        identifier * 2,
        identifier * 2 + 1,
        int(pd.Timestamp(time).timestamp() * 1000),
        str(maker).lower(),
    ]


def _candidate(time: str = "2026-01-02T00:10:00Z") -> pd.DataFrame:
    return pd.DataFrame([{"symbol": "BTCUSDT", "open_time_utc": pd.Timestamp(time)}])


def _previous() -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    rows = []
    for i in range(12):
        opened = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=i)
        rows.append(
            {
                "trade_sequence": i,
                "order_id": f"previous-{i}",
                "symbol": "BTCUSDT" if i % 2 else "ETHUSDT",
                "side": "long" if i % 2 else "short",
                "open_time_utc": opened,
                "close_time_utc": opened + pd.Timedelta(minutes=5),
                "label_economic_net_pnl": float(2 if i % 2 else -3),
                "capital_proxy_usdt": 100.0,
                "capital_hours": 100.0 / 12,
            }
        )
    splits = [
        {"split_id": f"fold-{n}", "_test_indices": list(range(n * 4, (n + 1) * 4))}
        for n in range(3)
    ]
    return pd.DataFrame(rows), splits


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for feature in source.FEATURES:
        result[feature] = 1.0
    result["event_flow_covered"] = True
    return result


def test_real_schema_maker_direction_windows_and_conservative_delay(tmp_path: Path) -> None:
    _archive(tmp_path, "BTCUSDT", "2026-01-01", [_event(1, "2026-01-01T23:56:00Z", 2, False)])
    _archive(
        tmp_path,
        "BTCUSDT",
        "2026-01-02",
        [
            _event(2, "2026-01-02T00:06:00Z", 3, True),
            _event(3, "2026-01-02T00:09:30Z", 5, False),
            _event(4, "2026-01-02T00:09:59Z", 1, True),
            _event(5, "2026-01-02T00:09:59.500Z", 999, True),
            _event(6, "2026-01-02T00:10:01Z", 999, True),
        ],
    )
    aligned, coverage = source.align_event_flow(_candidate(), tmp_path)
    assert coverage["feature_coverage"] == 1
    assert coverage["future_join_count"] == 0
    row = aligned.iloc[0]
    assert row.aggressive_buy_volume_1m == 5
    assert row.aggressive_sell_volume_1m == 1
    assert row.signed_volume_1m == 4
    assert row.taker_imbalance_1m == pytest.approx(4 / 6)
    assert row.trade_intensity_1m == pytest.approx(2 / 60)
    assert row.mean_trade_size_1m == 3
    assert row.buy_trade_count_1m == row.sell_trade_count_1m == 1
    assert row.signed_trade_count_imbalance_1m == 0
    assert row.aggressive_sell_volume_5m == 4
    assert row.aggressive_buy_volume_15m == 7
    assert len(source.FEATURES) == 27


def test_exact_lower_exclusive_upper_inclusive_and_midnight_boundary(tmp_path: Path) -> None:
    _archive(
        tmp_path,
        "BTCUSDT",
        "2026-01-01",
        [
            _event(1, "2026-01-01T23:45:00Z", 999, True),
            _event(2, "2026-01-01T23:59:59Z", 1, False),
            _event(3, "2026-01-01T23:59:59.001Z", 999, True),
        ],
    )
    aligned, coverage = source.align_event_flow(_candidate("2026-01-02T00:00:00Z"), tmp_path)
    assert coverage["feature_coverage"] == 1
    assert aligned.aggressive_buy_volume_15m.iloc[0] == 1
    assert aligned.aggressive_sell_volume_15m.iloc[0] == 0


def test_explicit_offset_uses_utc_archive_day(tmp_path: Path) -> None:
    _archive(tmp_path, "BTCUSDT", "2026-01-01", [_event(1, "2026-01-01T23:59:59Z", 1, False)])
    candidates = _candidate("2026-01-01T21:00:00-03:00")
    assert source.required_archives(candidates) == [
        ("BTCUSDT", "2026-01-01"),
        ("BTCUSDT", "2026-01-02"),
    ]
    aligned, coverage = source.align_event_flow(candidates, tmp_path)
    assert coverage["feature_coverage"] == 1
    assert aligned.aggressive_buy_volume_1m.iloc[0] == 1


def test_missing_decision_timezone_fails_closed(tmp_path: Path) -> None:
    candidates = _candidate("2026-01-02T00:10:00")
    with pytest.raises(source.EventFlowError, match="decision_timestamp_invalid"):
        source.required_archives(candidates)
    with pytest.raises(source.EventFlowError, match="decision_timestamp_invalid"):
        source.align_event_flow(candidates, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "column,value,reason",
    [
        (1, 0, "price_or_quantity_invalid"),
        (2, "nan", "price_or_quantity_invalid"),
        (4, -1, "trade_id_range_invalid"),
        (6, "unknown", "buyer_maker_not_boolean"),
    ],
)
def test_invalid_public_event_schema_fails_closed(
    tmp_path: Path, column: int, value: object, reason: str
) -> None:
    row = _event(1, "2026-01-02T00:00:00Z", 1, False)
    row[column] = value
    path = _archive(tmp_path, "BTCUSDT", "2026-01-02", [row])
    with pytest.raises(source.EventFlowError, match=reason):
        list(source.archive_chunks(path))


def test_duplicate_aggregate_identity_and_checksum_drift_are_blocked(tmp_path: Path) -> None:
    row = _event(1, "2026-01-02T00:00:00Z", 1, False)
    path = _archive(tmp_path, "BTCUSDT", "2026-01-02", [row, row])
    with pytest.raises(source.EventFlowError, match="identity_or_timestamp_order"):
        list(source.archive_chunks(path))
    path.write_bytes(path.read_bytes() + b"drift")
    with pytest.raises(source.EventFlowError, match="archive_checksum_mismatch"):
        source.obtain_archive(tmp_path, "BTCUSDT", "2026-01-02", allow_download=False)


def test_cross_archive_identity_collision_blocked(tmp_path: Path) -> None:
    _archive(tmp_path, "BTCUSDT", "2026-01-01", [_event(1, "2026-01-01T23:56:00Z", 2, False)])
    _archive(tmp_path, "BTCUSDT", "2026-01-02", [_event(1, "2026-01-02T00:09:30Z", 1, False)])
    with pytest.raises(source.EventFlowError, match="cross_archive_aggregate_identity_collision"):
        source.align_event_flow(_candidate(), tmp_path)


def test_missing_cache_does_not_download_and_empty_window_not_imputed(tmp_path: Path) -> None:
    with pytest.raises(source.EventFlowError, match="SOURCE_CACHE_MISSING"):
        source.align_event_flow(_candidate(), tmp_path)
    assert list(tmp_path.iterdir()) == []
    _archive(tmp_path, "BTCUSDT", "2026-01-01", [_event(1, "2026-01-01T23:56:00Z", 2, False)])
    _archive(tmp_path, "BTCUSDT", "2026-01-02", [_event(2, "2026-01-02T00:00:00Z", 1, False)])
    aligned, coverage = source.align_event_flow(_candidate(), tmp_path)
    assert coverage["covered_count"] == 0
    assert pd.isna(aligned.mean_trade_size_1m.iloc[0])


def test_streaming_public_download_checks_hash_and_is_create_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-aggTrades-2026-01-02.csv", ",".join(source.RAW_COLUMNS) + "\n")
    raw = buffer.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    checksum = f"{digest}  BTCUSDT-aggTrades-2026-01-02.zip\n".encode()
    monkeypatch.setattr(source, "download", lambda _: checksum)

    class Response(io.BytesIO):
        url = (
            source.PUBLIC_ROOT
            + "futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2026-01-02.zip"
        )

    monkeypatch.setattr(source.urllib.request, "urlopen", lambda *args, **kwargs: Response(raw))
    path, observed, created = source.obtain_archive(
        tmp_path, "BTCUSDT", "2026-01-02", allow_download=True
    )
    assert created and observed == digest and path.read_bytes() == raw
    _, _, again = source.obtain_archive(tmp_path, "BTCUSDT", "2026-01-02", allow_download=False)
    assert again is False
    assert not list(path.parent.glob("tmp*"))


def test_consumed_oos_gate_is_blocked_without_later_candidates() -> None:
    previous, splits = _previous()
    gate = discovery.fresh_oos_gate(previous, splits)
    assert gate["certification_status"] == "BLOCKED_NO_FRESH_OOS"
    assert gate["candidate_count"] == gate["outcome_available_count"] == gate["overlap_count"] == 0
    assert gate["previous_oos_end"] == "2026-01-01T11:05:00+00:00"
    assert gate["candidate_new_oos_start"] is None


def test_later_candidate_requires_unique_identity_completed_outcome_and_time_independence() -> None:
    previous, splits = _previous()
    candidate = previous.iloc[:1].copy()
    candidate["order_id"] = "fresh-1"
    candidate["open_time_utc"] = pd.Timestamp("2026-01-02T00:00:00Z")
    candidate["close_time_utc"] = pd.Timestamp("2026-01-02T00:05:00Z")
    gate = discovery.fresh_oos_gate(
        previous, splits, candidate, observed_at_utc=pd.Timestamp("2026-01-03T00:00:00Z")
    )
    assert gate["fresh_oos_available"] and gate["outcome_available_count"] == 1
    assert gate["economic_candidate"] is False
    assert gate["certification_status"] == "PENDING_SEALED_ONE_SHOT_EVALUATION"
    candidate["order_id"] = previous.order_id.iloc[0]
    collision = discovery.fresh_oos_gate(previous, splits, candidate)
    assert collision["overlap_count"] == 1 and not collision["fresh_oos_available"]
    candidate["order_id"] = "fresh-1"
    candidate["label_economic_net_pnl"] = np.nan
    missing = discovery.fresh_oos_gate(previous, splits, candidate)
    assert missing["outcome_available_count"] == 0 and not missing["fresh_oos_available"]


def test_no_future_outcome_or_flow_lite_threshold_participates_in_fixed_selection() -> None:
    previous, splits = _previous()
    frame = _features(previous)
    selected = discovery.fixed_selection(frame)
    changed = frame.copy()
    changed["label_economic_net_pnl"] *= -1000
    changed["close_time_utc"] += pd.Timedelta(days=200)
    changed["capital_hours"] *= 1000
    pd.testing.assert_series_equal(selected, discovery.fixed_selection(changed))
    report = discovery.historical_discovery(
        frame, splits, discovery.fresh_oos_gate(previous, splits)
    )
    assert report["parameter_calibration_performed"] is False
    assert report["flow_lite_thresholds_used"] is False
    assert report["outcomes_used_for_selection"] is False


def test_positive_historical_metrics_never_certify_economic_candidate() -> None:
    previous, splits = _previous()
    frame = _features(previous)
    report = discovery.historical_discovery(
        frame, splits, discovery.fresh_oos_gate(previous, splits)
    )
    arm = report["variants"]["EVENT_FLOW_ONLY"]
    assert arm["delta_net_pnl_vs_control"] > 0
    assert arm["incremental_expectancy"] > 0
    assert arm["historical_metric_gate_passed"] is True
    assert arm["economic_candidate"] is report["economic_candidate"] is False
    assert report["promotion_allowed"] is False
    assert report["research_discovery_only"] is True
    assert report["decision"] == "RESEARCH_DISCOVERY_ONLY"


def test_fresh_holdout_is_not_consumed_by_historical_runner() -> None:
    previous, splits = _previous()
    with pytest.raises(source.EventFlowError, match="fresh_holdout_requires_sealed_one_shot_plan"):
        discovery.historical_discovery(_features(previous), splits, {"fresh_oos_available": True})


def test_gate_precedes_source_loading_and_builder_never_writes_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous, splits = _previous()
    monkeypatch.setattr(discovery, "_load_bundle", lambda **_: (previous, (), splits, {}))
    seen: list[str] = []
    original = discovery.fresh_oos_gate

    def gate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append("gate")
        return original(*args, **kwargs)

    def align(
        frame: pd.DataFrame, *args: Any, **kwargs: Any
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        seen.append("events")
        assert kwargs["allow_download"] is False
        return _features(frame), {"future_join_count": 0, "feature_coverage": 1.0}

    monkeypatch.setattr(discovery, "fresh_oos_gate", gate)
    monkeypatch.setattr(discovery, "align_event_flow", align)
    report = discovery.build_event_flow_discovery(
        project_root=tmp_path / "project", cache_root=tmp_path / "cache"
    )
    assert seen == ["gate", "events"]
    assert report["status"] == "ok" and report["write_performed"] is False
    assert report["economic_candidate"] is False
    assert list(tmp_path.iterdir()) == []
    json.dumps(report, allow_nan=False)
