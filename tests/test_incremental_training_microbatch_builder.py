from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "build_incremental_training_microbatch.py"
    spec = importlib.util.spec_from_file_location("build_incremental_training_microbatch", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def feedback_row(
    order_id: str,
    *,
    symbol: str = "BTCUSDT",
    side: str = "long",
    open_time: str = "2026-01-01T00:02:30Z",
    close_time: str = "2026-01-01T00:07:30Z",
    pnl: float = 1.25,
    ret: float = 0.015,
) -> dict:
    return {
        "order_id": order_id,
        "moeda": symbol,
        "fechar_side": side,
        "horario_abertura": open_time,
        "horario_fechamento": close_time,
        "preco_abertura": 100.0,
        "preco_fechamento": 101.0,
        "pnl_fechado": pnl,
        "taxa_lucros_perdas_fechados_pct": ret,
        "exit_reason": "roi",
        "is_open": 0,
    }


def feature_rows() -> list[dict]:
    return [
        {"symbol": "BTCUSDT", "tf": "1m", "ts": "2026-01-01T00:00:00Z", "close": 100.0, "volume": 10.0, "rsi": 45.0},
        {"symbol": "BTCUSDT", "tf": "1m", "ts": "2026-01-01T00:02:00Z", "close": 102.0, "volume": 12.0, "rsi": 46.0},
        {"symbol": "BTCUSDT", "tf": "1m", "ts": "2026-01-01T00:04:00Z", "close": 104.0, "volume": 14.0, "rsi": 47.0},
        {"symbol": "ETHUSDT", "tf": "1m", "ts": "2026-01-01T00:01:00Z", "close": 200.0, "volume": 20.0, "rsi": 55.0},
    ]


def write_feedback(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def write_features(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def run_builder(tmp_path: Path, feedback: Path, features: Path, *, strict: bool = False) -> dict:
    module = load_module()
    return module.build_microbatch(
        feedback_path=feedback,
        features_path=features,
        output_path=tmp_path / "features" / "incremental_training_microbatch.parquet",
        report_path=tmp_path / "reports" / "incremental_training_microbatch_report.json",
        strict=strict,
    )


def read_output(tmp_path: Path) -> pd.DataFrame:
    return pd.read_parquet(tmp_path / "features" / "incremental_training_microbatch.parquet")


def test_builds_microbatch_with_valid_feedback_and_features(tmp_path: Path) -> None:
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    features = tmp_path / "features" / "market_features_60d.parquet"
    write_feedback(feedback, [feedback_row("paper-1"), feedback_row("paper-2", symbol="ETHUSDT", side="short")])
    write_features(features, feature_rows())

    report = run_builder(tmp_path, feedback, features)
    output = read_output(tmp_path)

    assert report["status"] == "ok"
    assert report["feedback_rows"] == 2
    assert report["features_rows"] == 4
    assert report["output_rows"] == 2
    assert report["missing_feature_rows"] == 0
    assert {"feature_close", "feature_volume", "feature_rsi"}.issubset(output.columns)
    assert {"source_feedback_path", "source_features_path", "built_at_utc", "record_hash"}.issubset(output.columns)
    assert output["record_hash"].str.len().eq(64).all()


def test_temporal_join_uses_previous_or_equal_feature_timestamp(tmp_path: Path) -> None:
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    features = tmp_path / "features" / "market_features_60d.parquet"
    write_feedback(feedback, [feedback_row("paper-1", open_time="2026-01-01T00:02:30Z")])
    write_features(features, feature_rows())

    report = run_builder(tmp_path, feedback, features)
    output = read_output(tmp_path)

    assert report["status"] == "ok"
    assert output.loc[0, "feature_timestamp_utc"] == pd.Timestamp("2026-01-01T00:02:00Z")
    assert output.loc[0, "feature_age_seconds"] == 30.0
    assert output.loc[0, "feature_close"] == 102.0


def test_blocks_operational_lookahead_future_ret_columns(tmp_path: Path) -> None:
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    features = tmp_path / "features" / "market_features_60d.parquet"
    write_feedback(feedback, [feedback_row("paper-1")])
    contaminated = feature_rows()
    contaminated[0]["future_ret_1"] = 0.01
    write_features(features, contaminated)

    report = run_builder(tmp_path, feedback, features, strict=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "lookahead_columns_detected"
    assert report["lookahead_columns"] == ["future_ret_1"]
    assert report["lookahead_columns_count"] == 1
    assert not (tmp_path / "features" / "incremental_training_microbatch.parquet").exists()


def test_strict_blocks_empty_output(tmp_path: Path) -> None:
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    features = tmp_path / "features" / "market_features_60d.parquet"
    write_feedback(feedback, [feedback_row("paper-early", open_time="2025-12-31T23:00:00Z")])
    write_features(features, feature_rows())

    report = run_builder(tmp_path, feedback, features, strict=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "empty_output"
    assert report["output_rows"] == 0


def test_reports_missing_feature_rows_without_blocking_non_strict(tmp_path: Path) -> None:
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    features = tmp_path / "features" / "market_features_60d.parquet"
    write_feedback(
        feedback,
        [
            feedback_row("paper-1"),
            feedback_row("paper-missing", symbol="ETHUSDT", open_time="2025-12-31T23:00:00Z"),
        ],
    )
    write_features(features, feature_rows())

    report = run_builder(tmp_path, feedback, features)

    assert report["status"] == "ok"
    assert report["output_rows"] == 1
    assert report["missing_feature_rows"] == 1


def test_preserves_utc_and_generates_targets(tmp_path: Path) -> None:
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    features = tmp_path / "features" / "market_features_60d.parquet"
    write_feedback(feedback, [feedback_row("win", pnl=2.0, ret=0.02), feedback_row("loss", pnl=-1.0, ret=-0.01)])
    write_features(features, feature_rows())

    report = run_builder(tmp_path, feedback, features)
    output = read_output(tmp_path)

    assert report["status"] == "ok"
    assert str(output["open_time_utc"].dt.tz) == "UTC"
    assert str(output["close_time_utc"].dt.tz) == "UTC"
    assert set(output["target_profitable"]) == {0, 1}
    assert output.set_index("order_id").loc["win", "target_return"] == 0.02
    assert output.set_index("order_id").loc["loss", "target_return"] == -0.01


def test_does_not_alter_trades_master(tmp_path: Path) -> None:
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    features = tmp_path / "features" / "market_features_60d.parquet"
    master = tmp_path / "trades" / "trades_master.xlsx"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(b"master")
    before = master.read_bytes()
    write_feedback(feedback, [feedback_row("paper-1")])
    write_features(features, feature_rows())

    report = run_builder(tmp_path, feedback, features)

    assert report["status"] == "ok"
    assert master.read_bytes() == before


def test_does_not_write_training_dataset(tmp_path: Path) -> None:
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    features = tmp_path / "features" / "market_features_60d.parquet"
    training_dataset = tmp_path / "features" / "training_dataset.parquet"
    write_feedback(feedback, [feedback_row("paper-1")])
    write_features(features, feature_rows())

    run_builder(tmp_path, feedback, features)

    assert not training_dataset.exists()


def test_preserves_paper_shadow_only_safety(tmp_path: Path) -> None:
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    features = tmp_path / "features" / "market_features_60d.parquet"
    write_feedback(feedback, [feedback_row("paper-1")])
    write_features(features, feature_rows())

    report = run_builder(tmp_path, feedback, features)
    text = (ROOT / "scripts" / "build_incremental_training_microbatch.py").read_text(encoding="utf-8")

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["runtime_mode"] == "paper"
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    for forbidden in ["create_order(", "fetch_balance(", "ccxt.", "Freqtrade API", "fit(", "train("]:
        assert forbidden not in text


def test_cli_prints_controlled_json(tmp_path: Path, capsys) -> None:
    module = load_module()
    feedback = tmp_path / "feedback" / "paper_closed_trades_incremental.parquet"
    features = tmp_path / "features" / "market_features_60d.parquet"
    write_feedback(feedback, [feedback_row("paper-1")])
    write_features(features, feature_rows())

    exit_code = module.main(
        [
            "--feedback",
            str(feedback),
            "--features",
            str(features),
            "--output",
            str(tmp_path / "features" / "incremental_training_microbatch.parquet"),
            "--report",
            str(tmp_path / "reports" / "incremental_training_microbatch_report.json"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["output_rows"] == 1
