from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.ml.anti_leakage_audit import BLOCKED, audit_feature_leakage


MODULE_PATH = Path("scripts/build_open_decision_clean_dataset.py")


def load_builder_module():
    spec = importlib.util.spec_from_file_location("build_open_decision_clean_dataset", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def leaky_dataset() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["t1", "t2", "t3"],
            "symbol": ["BTCUSDT", "ETHUSDT", "BTCUSDT"],
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=3, freq="min"),
            "open_5m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=3, freq="5min"),
            "target_win": [1, 0, 1],
            "target_aux": [0, 1, 0],
            "return_pct": [0.02, -0.01, 0.03],
            "mfe_pct": [0.03, 0.01, 0.04],
            "mae_pct": [-0.01, -0.02, -0.01],
            "pnl": [10.0, -5.0, 15.0],
            "duration_seconds": [60, 120, 180],
            "path_candles": ["[]", "[]", "[]"],
            "future_ret_3": [0.01, -0.02, 0.03],
            "open_1m_ret": [0.001, -0.002, 0.003],
            "open_1m_volume": [100.0, 200.0, 300.0],
            "open_5m_ret": [0.004, -0.005, 0.006],
            "close_1m_ret": [0.007, -0.008, 0.009],
            "close_5m_ret": [0.010, -0.011, 0.012],
        }
    )


def run_builder(tmp_path, monkeypatch, *, allow_path_candles: bool = False):
    module = load_builder_module()
    source = leaky_dataset()
    written: dict[str, pd.DataFrame] = {}

    monkeypatch.setattr(module, "read_dataset", lambda path: source.copy(deep=True))

    def fake_to_parquet(self, path, index=False):
        written["frame"] = self.copy(deep=True)
        Path(path).write_text("parquet-placeholder", encoding="utf-8")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fake_to_parquet)
    manifest = module.build_open_decision_clean_dataset(
        input_path=tmp_path / "input.parquet",
        output_path=tmp_path / "output.parquet",
        report_path=tmp_path / "report.json",
        target_column="target_win",
        decision_mode="open",
        allow_path_candles=allow_path_candles,
    )
    return source, written["frame"], manifest, tmp_path / "report.json"


def test_builder_removes_close_1m_and_close_5m_features(tmp_path, monkeypatch) -> None:
    _, output, manifest, _ = run_builder(tmp_path, monkeypatch)

    assert "close_1m_ret" not in output.columns
    assert "close_5m_ret" not in output.columns
    assert "close_1m_ret" in manifest.removed_columns
    assert "close_5m_ret" in manifest.removed_columns


def test_builder_removes_outcomes_from_features(tmp_path, monkeypatch) -> None:
    _, output, manifest, _ = run_builder(tmp_path, monkeypatch)

    assert "return_pct" not in output.columns
    assert "mfe_pct" not in output.columns
    assert "mae_pct" not in output.columns
    assert {"return_pct", "mfe_pct", "mae_pct"}.issubset(set(manifest.outcome_columns))
    assert not {"return_pct", "mfe_pct", "mae_pct"} & set(manifest.feature_columns)


def test_builder_keeps_target_as_label_and_open_features(tmp_path, monkeypatch) -> None:
    _, output, manifest, _ = run_builder(tmp_path, monkeypatch)

    assert "target_win" in output.columns
    assert manifest.label_columns == ["target_win"]
    assert "open_1m_ret" in output.columns
    assert "open_5m_ret" in output.columns
    assert "open_1m_ret" in manifest.feature_columns
    assert "open_5m_ret" in manifest.feature_columns


def test_builder_removes_path_candles_by_default(tmp_path, monkeypatch) -> None:
    _, output, manifest, _ = run_builder(tmp_path, monkeypatch)

    assert "path_candles" not in output.columns
    assert "path_candles" in manifest.removed_columns


def test_builder_allows_path_candles_only_when_enabled(tmp_path, monkeypatch) -> None:
    _, output, manifest, _ = run_builder(
        tmp_path,
        monkeypatch,
        allow_path_candles=True,
    )

    assert "path_candles" in output.columns
    assert "path_candles" in manifest.feature_columns


def test_builder_generates_json_serializable_report(tmp_path, monkeypatch) -> None:
    _, _, manifest, report_path = run_builder(tmp_path, monkeypatch)

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] in {"OK", "WARNING"}
    assert json.dumps(manifest.to_dict(), sort_keys=True)


def test_builder_does_not_mutate_original_input(tmp_path, monkeypatch) -> None:
    source, _, _, _ = run_builder(tmp_path, monkeypatch)

    assert list(source.columns) == list(leaky_dataset().columns)
    assert "close_1m_ret" in source.columns


def test_output_passes_anti_leakage_audit_not_blocked(tmp_path, monkeypatch) -> None:
    _, output, manifest, _ = run_builder(tmp_path, monkeypatch)

    report = audit_feature_leakage(
        output,
        target_column="target_win",
        feature_columns=manifest.feature_columns,
        metadata_columns=manifest.metadata_columns,
        decision_mode="open",
    )

    assert report.status != BLOCKED


def test_runner_accepts_tmp_path_and_does_not_write_data(tmp_path, monkeypatch) -> None:
    _, output, _, report_path = run_builder(tmp_path, monkeypatch)

    assert output is not None
    assert report_path.exists()
    assert not (tmp_path / "data").exists()


def test_builder_modules_do_not_reference_exchange_or_live_flags() -> None:
    text = "\n".join(
        [
            MODULE_PATH.read_text(encoding="utf-8"),
            Path("docs/OPEN_DECISION_CLEAN_DATASET.md").read_text(encoding="utf-8"),
        ]
    )
    forbidden = [
        "ccxt",
        "create_order",
        "submit_order",
        "fetch_balance",
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
    ]
    assert all(token not in text for token in forbidden)
