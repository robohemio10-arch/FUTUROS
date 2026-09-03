from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.research.market_features_rematerialization_research_v2 import (
    build_market_features_rematerialization_research_v2,
    rematerialize_5m_features,
    write_research_report,
)
from smartcrypto.research.market_features_rematerialization_research_v2.engine import (
    FEATURE_COLUMNS,
    align_point_in_time_features,
    normalize_trades,
)


def _candles(*, count: int = 120, gap_index: int | None = None) -> pd.DataFrame:
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    rows: list[dict[str, object]] = []
    for index in range(count):
        if index == gap_index:
            continue
        open_price = 100.0 + index * 0.1
        close_price = open_price * (1.0005 if index % 2 else 0.9995)
        rows.append(
            {
                "symbol": "BTCUSDT",
                "tf": "5m",
                "ts": base + pd.Timedelta(minutes=5 * index),
                "open": open_price,
                "high": max(open_price, close_price) * 1.001,
                "low": min(open_price, close_price) * 0.999,
                "close": close_price,
                "volume": 1000.0 + index * 3.0,
            }
        )
    return pd.DataFrame(rows)


def _trades(*, start: int = 20, count: int = 80) -> pd.DataFrame:
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    return pd.DataFrame(
        [
            {
                "trade_id": f"trade_{index}",
                "symbol": "BTC/USDT:USDT",
                "open_time_utc": base
                + pd.Timedelta(minutes=5 * candle_index + 5, seconds=30),
                "net_pnl": 1.0 if index % 2 == 0 else -1.0,
            }
            for index, candle_index in enumerate(range(start, start + count))
        ]
    )


def test_same_candle_is_not_visible_until_close() -> None:
    candles = rematerialize_5m_features(_candles())
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    raw_trades = pd.DataFrame(
        [
            {
                "trade_id": "before_close",
                "symbol": "BTCUSDT",
                "open_time_utc": base + pd.Timedelta(minutes=104, seconds=59),
                "net_pnl": 1.0,
            },
            {
                "trade_id": "at_close",
                "symbol": "BTCUSDT",
                "open_time_utc": base + pd.Timedelta(minutes=105),
                "net_pnl": -1.0,
            },
        ]
    )
    aligned = align_point_in_time_features(normalize_trades(raw_trades), candles)

    assert aligned.loc[0, "feature_timestamp_utc"] == base + pd.Timedelta(minutes=95)
    assert aligned.loc[1, "feature_timestamp_utc"] == base + pd.Timedelta(minutes=100)
    assert aligned["row_status"].tolist() == ["ready", "ready"]


def test_gap_is_fail_closed_without_forward_fill() -> None:
    candles = rematerialize_5m_features(_candles(gap_index=20))
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    raw_trades = pd.DataFrame(
        [
            {
                "trade_id": "gap_trade",
                "symbol": "BTCUSDT",
                "open_time_utc": base + pd.Timedelta(minutes=105, seconds=1),
                "net_pnl": 1.0,
            }
        ]
    )
    aligned = align_point_in_time_features(normalize_trades(raw_trades), candles)

    assert aligned.loc[0, "row_status"] == "blocked"
    assert "five_minute_candle_gap_no_forward_fill" in aligned.loc[
        0, "validation_block_reasons"
    ]


def test_report_reuses_unified_feature_contract_without_outcome_leakage() -> None:
    report = build_market_features_rematerialization_research_v2(
        _trades(count=40),
        _candles(),
        generated_at_utc="2026-09-03T00:00:00+00:00",
    )

    assert report["status"] == "ok"
    assert report["ready_row_count"] == 40
    assert report["feature_contract"]["validation_status"] == "ok"
    assert report["feature_contract"]["feature_columns"] == sorted(FEATURE_COLUMNS)
    assert report["feature_contract"]["label_columns"] == ["label_profitable"]
    assert report["feature_contract"]["leakage_columns_detected"] == []
    assert report["point_in_time_contract"]["same_candle_lookahead_allowed"] is False
    assert report["point_in_time_contract"]["forward_fill_across_gaps"] is False
    assert report["point_in_time_contract"]["imputation_performed"] is False


def test_ephemeral_challenger_has_no_persistence_or_promotion_authority() -> None:
    report = build_market_features_rematerialization_research_v2(
        _trades(),
        _candles(),
        run_challenger=True,
        generated_at_utc="2026-09-03T00:00:00+00:00",
    )
    challenger = report["ephemeral_challenger"]

    assert report["status"] == "ok"
    assert challenger["status"] == "ok"
    assert challenger["temporal_split"] is True
    assert challenger["embargo_seconds"] == 300
    assert challenger["model_artifact_written"] is False
    assert challenger["registry_write_performed"] is False
    assert challenger["promotion_eligible"] is False
    assert challenger["model_promotion_performed"] is False
    assert challenger["active_model_changed"] is False
    assert report["qlib_training_performed"] is False
    assert report["p08_allowed"] is False


def test_challenger_blocks_small_sample_without_blocking_rematerialization() -> None:
    report = build_market_features_rematerialization_research_v2(
        _trades(count=20),
        _candles(),
        run_challenger=True,
        generated_at_utc="2026-09-03T00:00:00+00:00",
    )

    assert report["status"] == "warning"
    assert report["ready_row_count"] == 20
    assert report["ephemeral_challenger"]["status"] == "blocked"
    assert report["ephemeral_challenger"]["reason"] == "insufficient_labeled_rows"


def test_lineage_hashes_are_deterministic_for_same_inputs() -> None:
    first = build_market_features_rematerialization_research_v2(
        _trades(count=40),
        _candles(),
        generated_at_utc="2026-09-03T00:00:00+00:00",
    )
    second = build_market_features_rematerialization_research_v2(
        _trades(count=40),
        _candles(),
        generated_at_utc="2026-09-03T00:00:00+00:00",
    )

    assert first["dataset_lineage"] == second["dataset_lineage"]


def test_report_writer_is_restricted_to_data_reports(tmp_path: Path) -> None:
    report = build_market_features_rematerialization_research_v2(
        _trades(count=20),
        _candles(),
        generated_at_utc="2026-09-03T00:00:00+00:00",
    )
    target = write_research_report(report, project_root=tmp_path)

    assert target.is_file()
    assert target.parent == (tmp_path / "data" / "reports").resolve()
    with pytest.raises(ValueError, match="report_output_must_be_under_data_reports"):
        write_research_report(
            report,
            project_root=tmp_path,
            output_path=tmp_path / "outside.json",
        )


def test_safety_flags_remain_fail_closed() -> None:
    report = build_market_features_rematerialization_research_v2(
        _trades(count=20),
        _candles(),
        generated_at_utc="2026-09-03T00:00:00+00:00",
    )

    assert report["research_only"] is True
    assert report["operational_authority"] is False
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["exchange_private_access"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["qlib_security_gate_remains_blocked"] is True
    assert report["qlib_security_gate_bypassed"] is False
