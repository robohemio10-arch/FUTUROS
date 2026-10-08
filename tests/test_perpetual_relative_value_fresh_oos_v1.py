from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from smartcrypto.research.aibot_parity import perpetual_relative_value_fresh_oos as rv
from smartcrypto.research.aibot_parity import perpetual_relative_value_source as source


def _history(
    start: str = "2026-08-19T00:00:00Z",
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    times = pd.date_range(start, periods=12, freq="min").asi8 // 1_000_000
    prices = {}
    funding = {}
    for symbol in source.SYMBOLS:
        prices[symbol] = pd.DataFrame(
            {
                "time_ms": times,
                "spot_open": 100.0,
                "spot_close": 100.0,
                "perp_open": [100.5] * 5 + [100.0] * 7,
                "perp_close": [100.5] * 5 + [100.0] * 7,
            }
        )
        events = np.array(
            [times[0] - 16 * 3_600_000, times[0] - 8 * 3_600_000, times[0] + 5 * 60_000]
        )
        funding[symbol] = pd.DataFrame(
            {
                "event_ms": events,
                "available_ms": events + 300_000,
                "rate": [0.001, 0.001, -0.0001],
                "mark": [100.0, 100.0, 100.0],
                "interval_hours": [np.nan, 8.0, 8.0],
            }
        )
    return prices, funding


def _rule() -> dict[str, Any]:
    return {
        "entry_basis_bps": 5.0,
        "minimum_expected_net_carry_bps": 0.5,
        "maximum_holding_hours": 24.0,
        "convergence_exit_bps": 0.0,
    }


def _replay(
    prices: dict[str, pd.DataFrame], funding: dict[str, pd.DataFrame]
) -> list[dict[str, Any]]:
    start = pd.Timestamp(prices["BTCUSDT"].time_ms.iloc[0], unit="ms", tz="UTC")
    end = start + pd.Timedelta(minutes=12)
    return rv.replay(prices, funding, _rule(), start, end)


def test_two_leg_pnl_actual_funding_mark_and_positive_costs() -> None:
    prices, funding = _history()
    trades = _replay(prices, funding)
    assert len(trades) == 2
    r = trades[0]
    assert r["quantity"] == 10
    assert r["spot_leg_pnl"] == 0
    assert r["perp_leg_pnl"] == 5
    assert r["funding_pnl"] == -0.1
    assert r["last_known_funding_event_ms"] < r["decision_ms"]
    m = rv.costs_and_metrics(trades, prices)
    assert m["entry_fees"] > 0 and m["exit_fees"] > 0 and m["slippage_cost"] > 0
    assert m["net_pnl_usdt"] == pytest.approx(
        m["spot_leg_pnl"]
        + m["perp_leg_pnl"]
        + m["funding_pnl"]
        - m["total_fees"]
        - m["slippage_cost"]
    )
    assert m["capital_committed"] > m["gross_notional"] > 0
    assert m["future_join_count"] == 0 and m["anti_leakage_status"] == "PASS"


def test_funding_future_outcome_and_next_fill_price_never_select_or_size_entry() -> None:
    prices, funding = _history()
    original = _replay(prices, funding)
    changed = {s: f.copy() for s, f in funding.items()}
    for f in changed.values():
        f.loc[2, ["rate", "mark"]] = [0.9, 9000.0]
    fills = {s: p.copy() for s, p in prices.items()}
    for p in fills.values():
        p.loc[3, "spot_open"] = 120.0
    replayed = _replay(fills, changed)
    assert [(r["decision_ms"], r["entry_ms"], r["quantity"]) for r in original] == [
        (r["decision_ms"], r["entry_ms"], r["quantity"]) for r in replayed
    ]
    assert original[0]["funding_pnl"] != replayed[0]["funding_pnl"]


def test_cost_stress_reuses_identical_trades_without_recalibration() -> None:
    prices, funding = _history()
    trades = _replay(prices, funding)
    results = [rv.costs_and_metrics(trades, prices, multiplier=k) for k in (1.0, 1.5, 2.0)]
    assert all(r["trade_count"] == 2 for r in results)
    assert results[0]["net_pnl_usdt"] > results[1]["net_pnl_usdt"] > results[2]["net_pnl_usdt"]
    assert results[2]["total_fees"] == 2 * results[0]["total_fees"]
    assert results[2]["slippage_cost"] == 2 * results[0]["slippage_cost"]


def test_zero_cost_and_plan_change_rejected() -> None:
    plan = copy.deepcopy(rv.PLAN)
    plan["costs"]["spot_fee_bps"] = 0.0
    with pytest.raises(source.PITSourceError):
        rv.validate_plan(plan)


def test_negative_basis_or_negative_known_funding_prevents_reverse_sleeve() -> None:
    prices, funding = _history()
    for f in funding.values():
        f["rate"] = -0.001
    assert _replay(prices, funding) == []
    for f in funding.values():
        f["rate"] = 0.001
    for p in prices.values():
        p["perp_close"] = 99.0
    assert _replay(prices, funding) == []


def test_stale_or_not_yet_available_funding_fails_closed() -> None:
    prices, funding = _history()
    for f in funding.values():
        f["available_ms"] += 48 * 3_600_000
    assert _replay(prices, funding) == []
    for f in funding.values():
        f["available_ms"] -= 96 * 3_600_000
    assert _replay(prices, funding) == []


def test_horizon_and_end_close_predefined_not_outcome_optimized() -> None:
    prices, funding = _history()
    for p in prices.values():
        p["perp_close"] = 100.5
    trades = _replay(prices, funding)
    assert len(trades) == 2 and all(r["exit_reason"] == "window_end" for r in trades)
    assert all(r["exit_ms"] == int(prices[r["symbol"]].time_ms.iloc[-1]) for r in trades)


def test_maximum_holding_horizon_applies_to_fill_not_signal() -> None:
    prices, funding = _history()
    for p in prices.values():
        p["perp_close"] = 100.5
    rule = _rule()
    rule["maximum_holding_hours"] = 5 / 60
    rule["minimum_expected_net_carry_bps"] = 0
    start = source.timestamp("2026-08-19T00:00:00Z")
    trades = rv.replay(prices, funding, rule, start, start + pd.Timedelta(minutes=12))
    assert trades and all(r["exit_ms"] - r["entry_ms"] <= 300_000 for r in trades)


def test_calibration_input_after_old_cutoff_blocked() -> None:
    prices, funding = _history("2026-08-29T00:00:00Z")
    with pytest.raises(source.PITSourceError, match="fresh_outcome_in_calibration_input"):
        rv.calibrate(prices, funding)


def test_small_grid_calibration_deterministic_and_no_fresh_labels() -> None:
    prices, funding = _history()
    first = rv.calibrate(prices, funding)
    assert len(first["trials"]) == 24
    assert first == rv.calibrate(prices, funding)
    assert first["fresh_oos_used_for_calibration"] is False


def test_unsealed_evaluation_never_loads_fresh_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> object:
        raise AssertionError("fresh loaded before seal")

    monkeypatch.setattr(rv, "load_public_history", forbidden)
    r = rv.run_relative_value(
        project_root=tmp_path / "project",
        artifact_root=tmp_path / "external",
        phase="evaluate",
        allow_download=True,
        write_evidence=True,
    )
    assert r["status"] == "blocked" and "frozen_config_required" in r["reason"]
    assert not (tmp_path / "external").exists()


def test_no_write_calibration_and_frozen_config_no_recalibration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prices, funding = _history()
    monkeypatch.setattr(
        rv,
        "load_public_history",
        lambda *args, **kwargs: (prices, funding, {"source": "test_fixture"}),
    )
    root = tmp_path / "external"
    dry = rv.run_relative_value(
        project_root=tmp_path / "project", artifact_root=root, phase="calibrate"
    )
    assert dry["status"] == "ok" and dry["write_performed"] is False and not root.exists()
    sealed = rv.run_relative_value(
        project_root=tmp_path / "project",
        artifact_root=root,
        phase="calibrate",
        write_evidence=True,
    )
    assert sealed["decision"] == "CONFIG_FROZEN"
    saved = json.loads((root / "frozen_config_v1.json").read_bytes())
    assert saved["config_hash"] == sealed["config_hash"]
    again = rv.run_relative_value(
        project_root=tmp_path / "project",
        artifact_root=root,
        phase="calibrate",
        write_evidence=True,
    )
    assert again["status"] == "blocked" and "already_frozen" in again["reason"]


def test_tampered_seal_blocked_before_fresh_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prices, funding = _history()
    monkeypatch.setattr(rv, "load_public_history", lambda *args, **kwargs: (prices, funding, {}))
    root = tmp_path / "external"
    rv.run_relative_value(
        project_root=tmp_path / "project",
        artifact_root=root,
        phase="calibrate",
        write_evidence=True,
    )
    path = root / "frozen_config_v1.json"
    seal = json.loads(path.read_bytes())
    seal["calibration"]["selected_rule"]["entry_basis_bps"] = 999
    path.write_bytes(rv.encode(seal))
    r = rv.run_relative_value(
        project_root=tmp_path / "project", artifact_root=root, phase="evaluate", write_evidence=True
    )
    assert r["status"] == "blocked" and "hash_mismatch" in r["reason"]
    assert not (root / "evaluation_started_v1.json").exists()


def test_future_join_proof_and_mtm_drawdown_not_closed_only() -> None:
    prices, funding = _history()
    trades = _replay(prices, funding)
    for p in prices.values():
        p.loc[4, "perp_open"] = 105
    m = rv.costs_and_metrics(trades, prices)
    assert m["max_drawdown"] > 80
    trades[0]["last_known_funding_available_ms"] = trades[0]["decision_ms"] + 1
    with pytest.raises(source.PITSourceError, match="future_join_detected"):
        rv.costs_and_metrics(trades, prices)


def test_real_funding_response_schema_hash_and_mark_prices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    start, end = source.timestamp("2026-08-01T00:00:00Z"), source.timestamp("2026-08-02T00:00:00Z")
    raw = json.dumps(
        [
            {
                "symbol": "BTCUSDT",
                "fundingTime": start.value // 1_000_000 + h * 3_600_000 + 1,
                "fundingRate": "0.0001",
                "markPrice": "60000",
            }
            for h in (0, 8, 16)
        ]
    ).encode()

    class Response(io.BytesIO):
        url = (
            source.FUNDING_ENDPOINT
            + "?"
            + source.urlencode(
                {
                    "symbol": "BTCUSDT",
                    "startTime": start.value // 1_000_000,
                    "endTime": end.value // 1_000_000 - 1,
                    "limit": 1000,
                }
            )
        )

    monkeypatch.setattr(source.urllib.request, "urlopen", lambda *a, **k: Response(raw))
    frame, proof = source.public_funding(tmp_path, "BTCUSDT", start, end, allow_download=True)
    assert frame.mark.eq(60000).all() and proof["sha256"] == source.sha256(raw)
    assert (frame.available_ms - frame.event_ms).eq(300_000).all()
    offline, _ = source.public_funding(tmp_path, "BTCUSDT", start, end, allow_download=False)
    pd.testing.assert_frame_equal(frame, offline)


def test_missing_source_default_never_downloads_or_writes(tmp_path: Path) -> None:
    with pytest.raises(source.PITSourceError, match="SOURCE_CACHE_MISSING"):
        source.public_funding(
            tmp_path,
            "BTCUSDT",
            source.timestamp("2026-08-01T00:00:00Z"),
            source.timestamp("2026-08-02T00:00:00Z"),
            allow_download=False,
        )
    assert list(tmp_path.iterdir()) == []


def test_one_shot_result_readback_never_replays_or_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prices, funding = _history()
    monkeypatch.setattr(rv, "load_public_history", lambda *args, **kwargs: (prices, funding, {}))
    root = tmp_path / "external"
    sealed = rv.run_relative_value(
        project_root=tmp_path / "project",
        artifact_root=root,
        phase="calibrate",
        write_evidence=True,
    )
    recorded = {
        "status": "ok",
        "decision": "RELATIVE_VALUE_NO_FRESH_OOS_EDGE",
        "config_hash": sealed["config_hash"],
        "write_performed": True,
        "economic_candidate": False,
        "promotion_allowed": False,
    }
    raw = rv.encode(recorded)
    (root / "fresh_oos_result_v1.json").write_bytes(raw)
    (root / "fresh_oos_result_v1.sha256").write_text(rv.sha256(raw), encoding="ascii")

    def forbidden(*args: Any, **kwargs: Any) -> object:
        raise AssertionError("fresh outcome consumed twice")

    monkeypatch.setattr(rv, "load_public_history", forbidden)
    r = rv.run_relative_value(
        project_root=tmp_path / "project",
        artifact_root=root,
        phase="evaluate",
        allow_download=True,
        write_evidence=True,
    )
    assert r["already_evaluated"] and r["write_performed"] is False


def test_incomplete_public_candle_history_is_not_imputed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start, end = source.timestamp("2026-08-01T00:00:00Z"), source.timestamp("2026-08-02T00:00:00Z")

    def sparse(relative: str, *args: Any, **kwargs: Any) -> tuple[list[list[str]], str]:
        scale = 1_000_000 if relative.startswith("spot") else 1000
        opened = int(start.timestamp()) * scale
        return [
            [
                str(opened),
                "100",
                "100",
                "100",
                "100",
                "1",
                str(opened + 60 * scale - 1),
                "100",
                "1",
                "1",
                "100",
                "0",
            ]
        ], "a" * 64

    monkeypatch.setattr(source, "archive_rows", sparse)
    with pytest.raises(source.PITSourceError, match="incomplete_public_1m_history"):
        source.load_public_history(tmp_path, start, end, allow_download=False)
