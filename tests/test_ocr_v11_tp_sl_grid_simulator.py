from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.research.reporting import (
    build_tp_sl_executive_summary,
    prepare_tp_sl_chart_data,
    render_tp_sl_executive_markdown,
)
from smartcrypto.research.tp_sl_simulator import (
    GRID_OUTPUT_COLUMNS,
    SAFETY_FLAGS,
    SimulatorConfig,
    StrategySpec,
    TradeContext,
    build_grid,
    financial_metrics,
    max_drawdown,
    prepare_candles,
    prepare_trade_contexts,
    rank_grid,
    resolve_paths,
    run_simulation,
    simulate_strategy,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_ocr_v11_tp_sl_grid_simulator.py"


def research_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "trade_id": "trade-1",
        "symbol": "BTCUSDT",
        "side": "long",
        "open_time": pd.Timestamp("2026-06-01T10:00:00Z"),
        "close_time": pd.Timestamp("2026-06-01T10:04:00Z"),
        "entry_price": 100_000.0,
        "exit_price": 101_000.0,
        "volume_closed": 0.001,
        "net_pnl": 0.9,
        "is_win": 1,
        "is_research_eligible": True,
        "research_block_reason": "ok",
        "mfe_pct": 1.2,
        "mae_pct": -0.8,
        "max_favorable_price": 101_200.0,
        "max_adverse_price": 99_200.0,
        "time_to_mfe_seconds": 120.0,
        "time_to_mae_seconds": 60.0,
    }
    row.update(overrides)
    return row


def candle_frame(
    *,
    path_highs: tuple[float, ...] = (100_400.0, 101_200.0, 100_800.0, 100_900.0),
    path_lows: tuple[float, ...] = (99_600.0, 99_500.0, 99_200.0, 99_700.0),
) -> pd.DataFrame:
    timestamps = pd.date_range("2026-06-01T09:40:00Z", periods=24, freq="min")
    frame = pd.DataFrame(
        {
            "symbol": "BTCUSDT",
            "tf": "1m",
            "ts": timestamps,
            "open": 100_000.0,
            "high": 100_200.0,
            "low": 99_800.0,
            "close": 100_000.0,
            "volume": 1.0,
            "atr_14": 100.0,
        }
    )
    path_times = pd.date_range("2026-06-01T10:00:00Z", periods=4, freq="min")
    for timestamp, high, low in zip(path_times, path_highs, path_lows, strict=True):
        selector = frame["ts"].eq(timestamp)
        frame.loc[selector, "high"] = high
        frame.loc[selector, "low"] = low
    return frame


def fixed_strategy(tp: float = 100.0, sl: float = 100.0) -> StrategySpec:
    return StrategySpec(
        strategy_id=f"fixed_tp_{tp:g}_sl_{sl:g}",
        tp_mode="fixed_bps",
        sl_mode="fixed_bps",
        tp_value=tp,
        sl_value=sl,
    )


def direct_context(
    *,
    side: str = "long",
    highs: tuple[float, ...] = (100_400.0, 101_100.0),
    lows: tuple[float, ...] = (99_600.0, 99_500.0),
    block_reason: str | None = None,
) -> TradeContext:
    path = pd.DataFrame(
        {
            "ts": pd.date_range("2026-06-01T10:00:00Z", periods=len(highs), freq="min"),
            "high": highs,
            "low": lows,
        }
    )
    return TradeContext(
        row=research_row(side=side),
        path=path,
        entry_atr=1_000.0,
        block_reason=block_reason,
    )


def compact_config(*, fee_bps: float = 0.0, slippage_bps: float = 0.0) -> SimulatorConfig:
    return SimulatorConfig(
        tp_bps=(100.0,),
        sl_bps=(100.0,),
        atr_multipliers=(1.0,),
        trailing_atr_multipliers=(1.0,),
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        workers=10,
        max_ram_gb=16.0,
    )


def write_project(tmp_path: Path, rows: list[dict[str, object]] | None = None) -> tuple[Path, Path]:
    research_path = tmp_path / "data" / "research" / "ocr_v11_trade_research_dataset.parquet"
    candles_path = tmp_path / "data" / "features" / "market_features_60d.parquet"
    research_path.parent.mkdir(parents=True)
    candles_path.parent.mkdir(parents=True)
    pd.DataFrame(rows or [research_row()]).to_parquet(research_path, index=False)
    candle_frame().to_parquet(candles_path, index=False)
    return research_path, candles_path


def cli_args(project: Path, *, write: bool) -> list[str]:
    return [
        sys.executable,
        str(SCRIPT),
        "--project-root",
        str(project),
        "--tp-bps",
        "100",
        "--sl-bps",
        "100",
        "--atr-multipliers",
        "1",
        "--trailing-atr-multipliers",
        "1",
        "--fee-bps",
        "0",
        "--slippage-bps",
        "0",
        "--write" if write else "--no-write",
        "--json",
    ]


def test_default_cli_does_not_write(tmp_path: Path) -> None:
    write_project(tmp_path)
    command = cli_args(tmp_path, write=False)
    command.remove("--no-write")
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "ocr_v11_tp_sl_grid_summary.json").exists()


def test_write_materializes_parquet_json_and_markdown(tmp_path: Path) -> None:
    write_project(tmp_path)
    completed = subprocess.run(
        cli_args(tmp_path, write=True),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert payload["write_performed"] is True
    assert Path(payload["output_grid_path"]).exists()
    assert Path(payload["output_trade_path"]).exists()
    assert Path(payload["report_path"]).exists()
    assert Path(payload["executive_report_path"]).exists()
    assert Path(payload["summary_path"]).exists()


def test_safety_flags_are_preserved(tmp_path: Path) -> None:
    write_project(tmp_path)
    report = run_simulation(resolve_paths(tmp_path), compact_config()).report
    for key, expected in SAFETY_FLAGS.items():
        assert report[key] is expected


def test_missing_research_dataset_is_blocked(tmp_path: Path) -> None:
    candles = tmp_path / "data" / "features" / "market_features_60d.parquet"
    candles.parent.mkdir(parents=True)
    candle_frame().to_parquet(candles, index=False)
    report = run_simulation(resolve_paths(tmp_path), compact_config()).report
    assert report["status"] == "blocked"
    assert report["reason"] == "missing_research_dataset"


def test_long_tp_hit_before_sl() -> None:
    result = simulate_strategy(direct_context(), fixed_strategy(), compact_config())
    assert result.first_hit == "tp"
    assert result.tp_hit is True
    assert result.exit_price == pytest.approx(101_000.0)


def test_long_sl_hit_before_tp() -> None:
    context = direct_context(
        highs=(100_400.0, 101_100.0),
        lows=(98_900.0, 99_500.0),
    )
    result = simulate_strategy(context, fixed_strategy(), compact_config())
    assert result.first_hit == "sl"
    assert result.exit_price == pytest.approx(99_000.0)


def test_short_tp_hit_before_sl() -> None:
    context = direct_context(
        side="short",
        highs=(100_400.0, 100_500.0),
        lows=(99_600.0, 98_900.0),
    )
    result = simulate_strategy(context, fixed_strategy(), compact_config())
    assert result.first_hit == "tp"
    assert result.exit_price == pytest.approx(99_000.0)


def test_short_sl_hit_before_tp() -> None:
    context = direct_context(
        side="short",
        highs=(101_100.0, 100_500.0),
        lows=(99_600.0, 98_900.0),
    )
    result = simulate_strategy(context, fixed_strategy(), compact_config())
    assert result.first_hit == "sl"
    assert result.exit_price == pytest.approx(101_000.0)


def test_same_candle_tp_and_sl_assumes_sl_first() -> None:
    context = direct_context(highs=(101_100.0,), lows=(98_900.0,))
    result = simulate_strategy(context, fixed_strategy(), compact_config())
    assert result.tp_hit is True and result.sl_hit is True
    assert result.first_hit == "sl"
    assert result.same_candle_rule_applied is True


def test_fee_and_slippage_reduce_result() -> None:
    context = direct_context(highs=(100_100.0,), lows=(99_900.0,))
    result = simulate_strategy(
        context,
        fixed_strategy(tp=500.0, sl=500.0),
        compact_config(fee_bps=4.0, slippage_bps=2.0),
    )
    assert result.gross_pnl == pytest.approx(1.0)
    assert result.fee == pytest.approx(0.0804)
    assert result.slippage == pytest.approx(0.0402)
    assert result.net_pnl == pytest.approx(0.8794)


def test_trade_without_candles_is_blocked() -> None:
    result = simulate_strategy(
        direct_context(block_reason="no_complete_candles_during_trade"),
        fixed_strategy(),
        compact_config(),
    )
    assert result.status == "blocked"
    assert result.reason == "no_complete_candles_during_trade"


def test_existing_mfe_mae_are_recomputed_and_validated() -> None:
    research = pd.DataFrame([research_row()])
    contexts = prepare_trade_contexts(research, prepare_candles(candle_frame()))
    context = contexts[0]
    assert context.recomputed_mfe_pct == pytest.approx(1.2)
    assert context.recomputed_mae_pct == pytest.approx(-0.8)
    assert context.mfe_mae_consistent is True


def test_grid_aggregates_profit_factor_correctly() -> None:
    winner = direct_context()
    loser = direct_context(highs=(100_300.0,), lows=(98_900.0,))
    loser.row["trade_id"] = "trade-2"
    grid, _best = build_grid([winner, loser], compact_config())
    fixed = grid.loc[grid["strategy_id"].eq("fixed_tp_100_sl_100")].iloc[0]
    assert fixed["profit_factor"] == pytest.approx(1.0)


def test_grid_aggregates_win_rate_correctly() -> None:
    winner = direct_context()
    loser = direct_context(highs=(100_300.0,), lows=(98_900.0,))
    loser.row["trade_id"] = "trade-2"
    grid, _best = build_grid([winner, loser], compact_config())
    fixed = grid.loc[grid["strategy_id"].eq("fixed_tp_100_sl_100")].iloc[0]
    assert fixed["win_rate"] == pytest.approx(0.5)


def test_drawdown_is_deterministic() -> None:
    values = [10.0, -4.0, -8.0, 5.0]
    assert max_drawdown(values) == pytest.approx(12.0)
    assert financial_metrics(values)["max_drawdown"] == pytest.approx(12.0)


def test_ranking_selects_one_candidate_without_promotion() -> None:
    rows = []
    for index, pnl in enumerate((10.0, 20.0)):
        row = {column: 0.0 for column in GRID_OUTPUT_COLUMNS}
        row.update(
            strategy_id=f"strategy-{index}",
            net_pnl=pnl,
            profit_factor=1.0 + index,
            expectancy=pnl / 2,
            max_drawdown=5.0,
            blocked_trades=0,
            evaluated_trades=2,
            max_consecutive_losses=1,
        )
        rows.append(row)
    ranked = rank_grid(pd.DataFrame(rows))
    assert int(ranked["is_candidate_best"].sum()) == 1
    assert ranked.loc[ranked["is_candidate_best"], "strategy_id"].iloc[0] == "strategy-1"
    assert "promotion_status" not in ranked.columns


def test_executive_report_contains_main_metrics() -> None:
    context = direct_context()
    grid, best = build_grid([context], compact_config())
    assert best is not None
    research = pd.DataFrame([research_row()])
    candles = prepare_candles(candle_frame())
    contexts = prepare_trade_contexts(research, candles)
    from smartcrypto.research.tp_sl_simulator import build_trade_outcomes

    trades = build_trade_outcomes(contexts, best, compact_config())
    summary = build_tp_sl_executive_summary(
        grid,
        trades,
        {"status": "ok", "research_dataset_path": "research.parquet"},
        analysis_date_utc="2026-06-23T12:00:00Z",
    )
    markdown = render_tp_sl_executive_markdown(summary)
    charts = prepare_tp_sl_chart_data(grid, trades)
    assert "Trades avaliados" in markdown
    assert "Premissa conservadora" in markdown
    assert summary["auto_promote"] is False
    assert charts["top_10_by_ranking"]


def test_does_not_modify_official_artifacts(tmp_path: Path) -> None:
    write_project(tmp_path)
    protected = [
        tmp_path / "data" / "trades" / "trades_master.xlsx",
        tmp_path / "data" / "features" / "training_dataset.parquet",
        tmp_path / "data" / "models" / "qlib_market_model.joblib",
        tmp_path / "data" / "sqlite" / "paper.sqlite",
        tmp_path / "freqtrade" / "strategy.py",
    ]
    before: dict[Path, str] = {}
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"protected:{path.name}".encode())
        before[path] = hashlib.sha256(path.read_bytes()).hexdigest()
    run_simulation(resolve_paths(tmp_path), compact_config(), write=True)
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    assert after == before


def test_module_has_no_private_exchange_or_freqtrade_imports() -> None:
    paths = [
        ROOT / "smartcrypto" / "research" / "tp_sl_simulator.py",
        ROOT / "scripts" / "run_ocr_v11_tp_sl_grid_simulator.py",
    ]
    imported: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    forbidden = ("freqtrade", "ccxt", "docker", "smartcrypto.execution", "smartcrypto.risk")
    assert not any(module.startswith(forbidden) for module in imported)


def test_workers_and_ram_are_reported(tmp_path: Path) -> None:
    write_project(tmp_path)
    report = run_simulation(
        resolve_paths(tmp_path),
        compact_config(),
    ).report
    assert report["configured_workers"] == 10
    assert report["configured_max_ram_gb"] == 16.0


def test_results_are_deterministic_for_same_inputs(tmp_path: Path) -> None:
    write_project(tmp_path)
    paths = resolve_paths(tmp_path)
    first = run_simulation(paths, compact_config())
    second = run_simulation(paths, compact_config())
    pd.testing.assert_frame_equal(first.grid, second.grid)
    pd.testing.assert_frame_equal(first.trades, second.trades)
    assert first.report == second.report
