from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.data.trader_master_fingerprint_v2.source_profile import load_source_profile
from smartcrypto.research.profit_research_dataset.candle_alignment import (
    align_trades_to_candles,
    load_candles,
    normalize_candles,
)
from smartcrypto.research.profit_research_dataset.contracts import (
    SAFETY_FLAGS,
    dataset_contract,
    resolve_build_paths,
)
from smartcrypto.research.profit_research_dataset.dataset_builder import (
    build_profit_research_dataset,
)
from smartcrypto.research.profit_research_dataset.economic_segments import (
    build_economic_segments,
    evaluate_btc_block_hypothesis,
    financial_metrics,
)
from smartcrypto.research.profit_research_dataset.entry_features import (
    attach_entry_features,
)
from smartcrypto.research.profit_research_dataset.path_features import (
    attach_path_features,
    compute_path_feature_row,
    duration_bucket,
)
from smartcrypto.research.profit_research_dataset.trade_snapshot import (
    build_paper_trade_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_profit_research_dataset_snapshot_v1.py"
PROFILE = ROOT / "config" / "freqtrade_paper_closed_trades_source_profile_v2.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trade_row(
    trade_id: int,
    *,
    symbol: str = "BTC/USDT:USDT",
    side: str = "long",
    open_time: str = "2026-01-01T00:30:00Z",
    close_time: str = "2026-01-01T00:35:00Z",
    net_pnl: float = -5.3,
    fee_open: float | None = 0.1,
    fee_close: float | None = 0.3,
    funding: float | None = 0.2,
) -> dict[str, object]:
    is_short = side == "short"
    open_rate = 100.0
    close_rate = 95.0 if not is_short else 105.0
    gross = (close_rate - open_rate) if not is_short else (open_rate - close_rate)
    reconstructed = gross - (float(fee_open or 0.0) * 2.0 + float(fee_close or 0.0)) + float(funding or 0.0)
    resolved_net = net_pnl if net_pnl != -5.3 else reconstructed
    return {
        "id": trade_id,
        "exchange": "binance",
        "pair": symbol,
        "is_open": 0,
        "is_short": int(is_short),
        "open_rate": open_rate,
        "close_rate": close_rate,
        "amount": 1.0,
        "contract_size": 1.0,
        "leverage": 2.0,
        "fee_open_cost": fee_open,
        "fee_close_cost": fee_close,
        "fee_open_currency": "USDT",
        "fee_close_currency": "USDT",
        "funding_fees": funding,
        "close_profit_abs": resolved_net,
        "realized_profit": resolved_net,
        "open_date": open_time,
        "close_date": close_time,
        "close_profit": resolved_net / 50.0,
        "stake_amount": 50.0,
        "exit_reason": "stop_loss" if resolved_net < 0 else "exit_signal",
        "strategy": "FixturePaperStrategy",
        "timeframe": "1m",
        "enter_tag": "fixture",
    }


def write_runtime_db(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0])
    text_columns = {
        "exchange",
        "pair",
        "fee_open_currency",
        "fee_close_currency",
        "open_date",
        "close_date",
        "exit_reason",
        "strategy",
        "timeframe",
        "enter_tag",
    }
    integer_columns = {"id", "is_open", "is_short"}
    definitions = []
    for column in columns:
        kind = "TEXT" if column in text_columns else "INTEGER" if column in integer_columns else "REAL"
        definitions.append(f'"{column}" {kind}')
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"CREATE TABLE trades ({', '.join(definitions)})")
        placeholders = ", ".join("?" for _ in columns)
        names = ", ".join(f'"{column}"' for column in columns)
        connection.executemany(
            f"INSERT INTO trades ({names}) VALUES ({placeholders})",
            [[row[column] for column in columns] for row in rows],
        )
        connection.commit()
    finally:
        connection.close()


def candle_frame(*, periods: int = 90) -> pd.DataFrame:
    rows = []
    for symbol, direction in (("BTCUSDT", -1.0), ("ETHUSDT", 1.0)):
        for index, timestamp in enumerate(
            pd.date_range("2026-01-01T00:00:00Z", periods=periods, freq="min")
        ):
            base = 100.0 + direction * index * 0.2
            rows.append(
                {
                    "symbol": symbol,
                    "tf": "1m",
                    "ts": timestamp,
                    "open": base,
                    "high": base + 1.0,
                    "low": base - 1.0,
                    "close": base + direction * 0.1,
                    "volume": 100.0 + index,
                }
            )
    return pd.DataFrame(rows)


def prepare_project(
    root: Path,
    *,
    trades: list[dict[str, object]] | None = None,
    candles: pd.DataFrame | None = None,
) -> tuple[Path, Path]:
    profile = root / "config" / PROFILE.name
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(PROFILE.read_text(encoding="utf-8"), encoding="utf-8")
    db = root / "data" / "paper.sqlite"
    write_runtime_db(
        db,
        trades
        or [
            trade_row(1),
            trade_row(
                2,
                symbol="ETH/USDT:USDT",
                side="short",
                open_time="2026-01-01T00:40:00Z",
                close_time="2026-01-01T00:45:00Z",
                net_pnl=4.5,
                funding=-0.1,
            ),
        ],
    )
    candle_path = root / "data" / "candles.parquet"
    candle_path.parent.mkdir(parents=True, exist_ok=True)
    (candles if candles is not None else candle_frame()).to_parquet(candle_path, index=False)
    return db, candle_path


def build(root: Path, **kwargs: object):
    db, candles = prepare_project(root)
    paths = resolve_build_paths(
        root,
        paper_db=db,
        paper_snapshot_db=root / "missing.snapshot.sqlite",
        candle_root=candles,
        output_root=root / "data",
    )
    timeframe = str(kwargs.pop("timeframe", "1m"))
    return build_profit_research_dataset(
        paths,
        timeframe=timeframe,
        allow_runtime_read=True,
        generated_at_utc="2026-07-14T00:00:00+00:00",
        **kwargs,
    )


def aligned_fixture(open_time: str = "2026-01-01T00:30:00Z"):
    trades = pd.DataFrame(
        [
            {
                "stable_trade_id": "trade-1",
                "symbol": "BTCUSDT",
                "side": "long",
                "open_time_utc": pd.Timestamp(open_time),
                "close_time_utc": pd.Timestamp("2026-01-01T00:35:00Z"),
                "entry_price": 94.0,
                "quantity": 1.0,
                "contract_size": 1.0,
                "net_pnl": -1.0,
                "gross_pnl": -0.5,
                "fees": 0.5,
                "financial_decomposition_status": "authoritative_reconciled",
                "duration_seconds": 300.0,
                "analysis_eligible": True,
            }
        ]
    )
    candles = normalize_candles(candle_frame(), default_timeframe="1m")
    return trades, candles


def test_exact_candle_alignment() -> None:
    trades, candles = aligned_fixture()
    result = align_trades_to_candles(trades, candles, timeframe="1m")
    assert result.frame.loc[0, "entry_candle_distance_seconds"] == 0.0
    assert result.frame.loc[0, "candle_alignment_status"] == "aligned"


def test_candle_alignment_with_tolerance() -> None:
    trades, candles = aligned_fixture("2026-01-01T00:30:30Z")
    result = align_trades_to_candles(trades, candles, timeframe="1m")
    assert result.frame.loc[0, "entry_candle_distance_seconds"] == 30.0
    assert result.frame.loc[0, "candle_alignment_status"] == "aligned"


def test_missing_symbol_candles_is_structured() -> None:
    trades, candles = aligned_fixture()
    trades.loc[0, "symbol"] = "SOLUSDT"
    result = align_trades_to_candles(trades, candles, timeframe="1m")
    assert result.frame.loc[0, "candle_missing_reason"] == "symbol_candles_missing"


def test_trade_outside_candle_coverage_is_unaligned() -> None:
    trades, candles = aligned_fixture("2025-12-01T00:00:00Z")
    result = align_trades_to_candles(trades, candles, timeframe="1m")
    assert result.frame.loc[0, "candle_missing_reason"] == "trade_outside_candle_coverage"


def test_timestamps_are_utc() -> None:
    trades, candles = aligned_fixture()
    result = align_trades_to_candles(trades, candles, timeframe="1m")
    assert str(result.frame["entry_candle_timestamp_utc"].dt.tz) == "UTC"


def test_duplicate_trade_identity_is_rejected(tmp_path: Path) -> None:
    db, _ = prepare_project(tmp_path, trades=[trade_row(1), trade_row(1)])
    profile = load_source_profile(tmp_path / "config" / PROFILE.name)
    frame, metadata = build_paper_trade_snapshot(
        project_root=tmp_path,
        source_path=db,
        profile=profile,
        authoritative_snapshot=False,
    )
    assert metadata["duplicate_trade_count"] == 1
    assert frame["analysis_eligible"].tolist() == [True, False]


def test_dataset_order_is_deterministic(tmp_path: Path) -> None:
    first = build(tmp_path / "one")
    second = build(tmp_path / "two")
    assert first.dataset["stable_trade_id"].tolist() == second.dataset["stable_trade_id"].tolist()


def test_missing_history_is_not_imputed() -> None:
    trades, candles = aligned_fixture("2026-01-01T00:05:00Z")
    aligned = align_trades_to_candles(trades, candles, timeframe="1m")
    result = attach_entry_features(aligned.frame, candles, timeframe="1m")
    assert result.loc[0, "entry_feature_complete"] == False  # noqa: E712
    assert pd.isna(result.loc[0, "entry_return_24"])


def test_entry_and_outcome_contracts_are_separate(tmp_path: Path) -> None:
    paths = resolve_build_paths(tmp_path, output_root=tmp_path / "data")
    contract = dataset_contract(paths)
    entry = contract["feature_availability_rules"]
    assert all(item["leakage_classification"] == "entry_time_observable" for item in entry)
    assert contract["label_definitions"]["path_features"].startswith("diagnostic_only")


def test_entry_feature_timestamp_never_exceeds_open_time() -> None:
    trades, candles = aligned_fixture()
    aligned = align_trades_to_candles(trades, candles, timeframe="1m")
    result = attach_entry_features(aligned.frame, candles, timeframe="1m")
    assert result.loc[0, "entry_feature_timestamp_utc"] <= result.loc[0, "open_time_utc"]


def test_future_columns_are_blocked(tmp_path: Path) -> None:
    path = tmp_path / "candles.parquet"
    frame = candle_frame()
    frame["future_ret_1"] = 0.1
    frame.to_parquet(path, index=False)
    result = load_candles(path, timeframe="1m")
    assert result.frame.empty
    assert any("candle_source_unreadable" in warning for warning in result.warnings)


def test_mfe_and_mae_for_long() -> None:
    trades, candles = aligned_fixture()
    path = candles.loc[
        candles["symbol"].eq("BTCUSDT")
        & candles["ts"].between("2026-01-01T00:30:00Z", "2026-01-01T00:35:00Z")
    ].reset_index(drop=True)
    values = compute_path_feature_row(trades.iloc[0], path)
    assert values["mfe_absolute"] > 0
    assert values["mae_absolute"] < 0


def test_retracement_after_mfe_is_non_negative() -> None:
    trades, candles = aligned_fixture()
    path = candles.loc[candles["symbol"].eq("BTCUSDT")].iloc[30:36].reset_index(drop=True)
    values = compute_path_feature_row(trades.iloc[0], path)
    assert values["retracement_after_mfe_absolute"] >= 0


def test_winner_to_loser_conversion() -> None:
    trades, candles = aligned_fixture()
    alignment = align_trades_to_candles(trades, candles, timeframe="1m")
    result = attach_path_features(alignment.frame, alignment.paths_by_trade)
    assert bool(result.loc[0, "winner_to_loser_conversion"]) is True


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (60, "lt_15m"),
        (900, "15m_30m"),
        (1800, "30m_60m"),
        (3600, "1h_3h"),
        (10800, "3h_6h"),
        (21600, "gte_6h"),
    ],
)
def test_duration_buckets(seconds: int, expected: str) -> None:
    assert duration_bucket(seconds) == expected


def test_entry_regimes_are_materialized() -> None:
    trades, candles = aligned_fixture()
    alignment = align_trades_to_candles(trades, candles, timeframe="1m")
    result = attach_entry_features(alignment.frame, candles, timeframe="1m")
    assert result.loc[0, "entry_trend_regime"] in {"uptrend", "downtrend", "sideways"}
    assert result.loc[0, "entry_volatility_regime"] in {"low", "normal", "high", "unknown"}


def test_economic_segmentation_includes_symbol_side() -> None:
    frame = pd.DataFrame(
        {
            "stable_trade_id": ["a", "b"],
            "close_time_utc": pd.to_datetime(
                ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"], utc=True
            ),
            "symbol": ["BTCUSDT", "ETHUSDT"],
            "side": ["long", "short"],
            "net_pnl": [-2.0, 1.0],
            "fees": [0.1, 0.1],
        }
    )
    segments = build_economic_segments(frame)
    assert any(item["segment_dimension"] == "symbolxside" for item in segments)


def test_profit_factor_is_safe() -> None:
    frame = pd.DataFrame(
        {
            "stable_trade_id": ["a", "b", "c"],
            "close_time_utc": pd.date_range("2026-01-01", periods=3, tz="UTC"),
            "net_pnl": [3.0, -1.0, -2.0],
            "fees": [0.0, 0.0, 0.0],
        }
    )
    assert financial_metrics(frame)["profit_factor"] == pytest.approx(1.0)


def test_drawdown_is_deterministic() -> None:
    frame = pd.DataFrame(
        {
            "stable_trade_id": ["a", "b", "c"],
            "close_time_utc": pd.date_range("2026-01-01", periods=3, tz="UTC"),
            "net_pnl": [5.0, -3.0, -4.0],
            "fees": [0.0, 0.0, 0.0],
        }
    )
    assert financial_metrics(frame)["max_drawdown"] == pytest.approx(7.0)


@pytest.mark.parametrize(("field", "reason"), [("fee_open", "missing"), ("funding", "missing")])
def test_missing_financial_evidence_is_rejected(tmp_path: Path, field: str, reason: str) -> None:
    kwargs = {field: None}
    db, _ = prepare_project(tmp_path, trades=[trade_row(1, **kwargs)])
    profile = load_source_profile(tmp_path / "config" / PROFILE.name)
    frame, _ = build_paper_trade_snapshot(
        project_root=tmp_path,
        source_path=db,
        profile=profile,
        authoritative_snapshot=False,
    )
    assert reason == "missing"
    assert not bool(frame.loc[0, "analysis_eligible"])
    assert frame.loc[0, "rejection_reason"] == "missing_required_financial_field"


def test_btc_block_hypothesis_supported_when_btc_is_stably_harmful() -> None:
    frame = pd.DataFrame(
        {
            "stable_trade_id": [f"t-{index}" for index in range(10)],
            "close_time_utc": pd.date_range("2026-01-01", periods=10, tz="UTC"),
            "symbol": ["BTCUSDT" if index % 2 == 0 else "ETHUSDT" for index in range(10)],
            "net_pnl": [-2.0 if index % 2 == 0 else 1.0 for index in range(10)],
            "fees": 0.1,
            "entry_trend_regime": "sideways",
            "entry_volatility_regime": "normal",
        }
    )
    result = evaluate_btc_block_hypothesis(frame)
    assert result["conclusion"] == "supported"
    assert result["operational_rule_created"] is False


def test_explicit_writes_are_atomic_and_restricted(tmp_path: Path) -> None:
    result = build(tmp_path, write_report=True, write_dataset=True)
    assert result.report["write_performed"] is True
    assert len(result.report["outputs_written"]) == 7
    assert not list((tmp_path / "data").rglob(".*.parquet"))
    assert all(Path(path).is_file() for path in result.report["outputs_written"])


def test_no_write_default_with_runtime_read(tmp_path: Path) -> None:
    result = build(tmp_path)
    assert result.report["write_performed"] is False
    assert not (tmp_path / "data" / "reports").exists()
    assert not (tmp_path / "data" / "research").exists()


def test_runtime_read_is_blocked_by_default(tmp_path: Path) -> None:
    paths = resolve_build_paths(tmp_path, output_root=tmp_path / "data")
    result = build_profit_research_dataset(paths)
    assert result.report["status"] == "blocked"
    assert result.report["reason"] == "runtime_read_not_allowed"


def test_sqlite_source_hash_is_preserved(tmp_path: Path) -> None:
    db, _ = prepare_project(tmp_path)
    before = sha256(db)
    result = build_profit_research_dataset(
        resolve_build_paths(
            tmp_path,
            paper_db=db,
            paper_snapshot_db=tmp_path / "missing.sqlite",
            candle_root=tmp_path / "data" / "candles.parquet",
            output_root=tmp_path / "data",
        ),
        allow_runtime_read=True,
    )
    assert result.report["paper_source_metadata"]["snapshot_source_hashes_preserved"] is True
    assert sha256(db) == before


def test_trader_master_is_never_written(tmp_path: Path) -> None:
    master = tmp_path / "data" / "trades" / "trades_master.parquet"
    master.parent.mkdir(parents=True)
    master.write_bytes(b"protected-master")
    before = sha256(master)
    build(tmp_path)
    assert sha256(master) == before


def test_safety_flags_never_authorize_operation(tmp_path: Path) -> None:
    report = build(tmp_path).report
    for key, expected in SAFETY_FLAGS.items():
        assert report[key] is expected


def test_output_paths_are_git_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/" in gitignore


def test_two_builds_have_identical_dataset_hash(tmp_path: Path) -> None:
    first = build(tmp_path / "first").report
    second = build(tmp_path / "second").report
    assert first["dataset_in_memory_sha256"] == second["dataset_in_memory_sha256"]


def test_cli_no_runtime_read_is_fail_closed(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["reason"] == "runtime_read_not_allowed"
    assert payload["write_performed"] is False
