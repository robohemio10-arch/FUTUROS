from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import joblib
import pandas as pd

from smartcrypto.ml.ai_shadow_incremental_trainer import train_ai_shadow_incremental_model


ROOT = Path(__file__).resolve().parents[1]


def load_cli_module():
    path = ROOT / "scripts" / "train_ai_shadow_incremental_model.py"
    spec = importlib.util.spec_from_file_location("train_ai_shadow_incremental_model", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def microbatch_rows(rows: int = 26) -> list[dict]:
    output = []
    for index in range(rows):
        profitable = 1 if index % 2 == 0 else 0
        output.append(
            {
                "order_id": f"paper-{index}",
                "symbol": "BTCUSDT" if index % 3 else "ETHUSDT",
                "side": "long" if index % 2 == 0 else "short",
                "open_time_utc": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=index),
                "close_time_utc": pd.Timestamp("2026-01-01T00:05:00Z") + pd.Timedelta(minutes=index),
                "pnl_fechado": 1.0 if profitable else -0.75,
                "target_return": 0.01 if profitable else -0.008,
                "target_profitable": profitable,
                "feature_timestamp_utc": pd.Timestamp("2025-12-31T23:59:00Z") + pd.Timedelta(minutes=index),
                "feature_age_seconds": 60.0,
                "feature_close": 100.0 + index,
                "feature_volume": 10.0 + index,
                "feature_rsi": 40.0 + (index % 20),
                "source_feedback_path": "data/feedback/paper_closed_trades_incremental.parquet",
                "source_features_path": "data/features/market_features_60d.parquet",
                "built_at_utc": "2026-01-01T01:00:00+00:00",
                "record_hash": f"hash-{index}",
            }
        )
    return output


def write_microbatch(path: Path, rows: list[dict] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows or microbatch_rows()).to_parquet(path, index=False)


def run_trainer(tmp_path: Path, input_path: Path, *, strict: bool = False) -> dict:
    return train_ai_shadow_incremental_model(
        input_path=input_path,
        model_dir=tmp_path / "models" / "shadow",
        report_path=tmp_path / "reports" / "ai_shadow_incremental_trainer_report.json",
        strict=strict,
    )


def test_trainer_blocks_missing_input(tmp_path: Path) -> None:
    report = run_trainer(tmp_path, tmp_path / "features" / "missing.parquet")

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_input"
    assert report["write_performed"] is False
    assert not (tmp_path / "models" / "shadow").exists()


def test_trainer_blocks_future_ret_columns(tmp_path: Path) -> None:
    source = tmp_path / "features" / "incremental_training_microbatch.parquet"
    rows = microbatch_rows()
    rows[0]["future_ret_1"] = 0.01
    write_microbatch(source, rows)

    report = run_trainer(tmp_path, source)

    assert report["status"] == "blocked"
    assert report["reason"] == "lookahead_columns_detected"
    assert report["lookahead_columns"] == ["future_ret_1"]
    assert report["lookahead_columns_count"] == 1


def test_trainer_blocks_missing_target(tmp_path: Path) -> None:
    source = tmp_path / "features" / "incremental_training_microbatch.parquet"
    frame = pd.DataFrame(microbatch_rows()).drop(columns=["target_profitable"])
    write_microbatch(source, frame.to_dict("records"))

    report = run_trainer(tmp_path, source)

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_target_column"


def test_trainer_blocks_single_class(tmp_path: Path) -> None:
    source = tmp_path / "features" / "incremental_training_microbatch.parquet"
    rows = microbatch_rows()
    for row in rows:
        row["target_profitable"] = 1
    write_microbatch(source, rows)

    report = run_trainer(tmp_path, source)

    assert report["status"] == "blocked"
    assert report["reason"] == "single_target_class"


def test_trainer_blocks_missing_feature_columns(tmp_path: Path) -> None:
    source = tmp_path / "features" / "incremental_training_microbatch.parquet"
    frame = pd.DataFrame(microbatch_rows()).drop(columns=["feature_close", "feature_volume", "feature_rsi"])
    write_microbatch(source, frame.to_dict("records"))

    report = run_trainer(tmp_path, source)

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_numeric_feature_columns"


def test_trainer_writes_shadow_model_and_report(tmp_path: Path) -> None:
    source = tmp_path / "features" / "incremental_training_microbatch.parquet"
    report_path = tmp_path / "reports" / "ai_shadow_incremental_trainer_report.json"
    write_microbatch(source)

    report = train_ai_shadow_incremental_model(
        input_path=source,
        model_dir=tmp_path / "models" / "shadow",
        report_path=report_path,
    )
    saved_report = json.loads(report_path.read_text(encoding="utf-8"))
    bundle = joblib.load(report["model_path"])
    metadata = json.loads(Path(report["metadata_path"]).read_text(encoding="utf-8"))

    assert report["status"] == "ok"
    assert report["input_rows"] == 26
    assert report["feature_columns"] == ["feature_close", "feature_volume", "feature_rsi"]
    assert report["target_column"] == "target_profitable"
    assert report["class_balance"] == {"0": 13, "1": 13}
    assert report["sample_warning"] is True
    assert report["promotion_status"] == "pending"
    assert report["auto_promote"] is False
    assert Path(report["model_path"]).exists()
    assert Path(report["metadata_path"]).exists()
    assert saved_report["model_version"] == metadata["model_version"]
    assert bundle["metadata"]["promotion_status"] == "pending"
    for key in ["accuracy", "precision", "recall", "f1", "roc_auc", "train_rows", "test_rows"]:
        assert key in report["metrics"]


def test_trainer_is_paper_shadow_only(tmp_path: Path) -> None:
    source = tmp_path / "features" / "incremental_training_microbatch.parquet"
    write_microbatch(source)

    report = run_trainer(tmp_path, source)
    text = (ROOT / "smartcrypto" / "ml" / "ai_shadow_incremental_trainer.py").read_text(encoding="utf-8")

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["runtime_mode"] == "paper"
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    for forbidden in ["create_order(", "fetch_balance(", "ccxt.", "Freqtrade API", "model_registry", "training_dataset.parquet"]:
        assert forbidden not in text


def test_cli_runs_successfully(tmp_path: Path, capsys) -> None:
    module = load_cli_module()
    source = tmp_path / "features" / "incremental_training_microbatch.parquet"
    write_microbatch(source)

    exit_code = module.main(
        [
            "--input",
            str(source),
            "--model-dir",
            str(tmp_path / "models" / "shadow"),
            "--report",
            str(tmp_path / "reports" / "ai_shadow_incremental_trainer_report.json"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["write_performed"] is True
    assert Path(payload["model_path"]).exists()


def test_does_not_write_training_dataset_or_trades_master(tmp_path: Path) -> None:
    source = tmp_path / "features" / "incremental_training_microbatch.parquet"
    training_dataset = tmp_path / "features" / "training_dataset.parquet"
    trades_master = tmp_path / "trades" / "trades_master.xlsx"
    trades_master.parent.mkdir(parents=True, exist_ok=True)
    trades_master.write_bytes(b"master")
    before = trades_master.read_bytes()
    write_microbatch(source)

    report = run_trainer(tmp_path, source)

    assert report["status"] == "ok"
    assert not training_dataset.exists()
    assert trades_master.read_bytes() == before
