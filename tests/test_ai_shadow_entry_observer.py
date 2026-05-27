from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smartcrypto.ml.ai_shadow_entry_observer import (
    AIShadowEntryObserverError,
    BLOCKED,
    SHADOW_ENTRY,
    SHADOW_SKIP,
    run_ai_shadow_entry_observer,
    select_shadow_feature_columns,
    write_jsonl,
)


MODULE_PATH = Path("scripts/run_ai_shadow_entry_observer.py")


def feature_frame(rows: int = 40) -> pd.DataFrame:
    idx = np.arange(rows)
    target = (idx % 4 >= 2).astype(int)
    signal = np.where(target == 1, 2.0, -2.0)
    frame = pd.DataFrame(
        {
            "trade_id": [f"t{item}" for item in idx],
            "symbol": ["BTCUSDT" if item % 2 else "ETHUSDT" for item in idx],
            "open_1m_ts": pd.date_range("2026-01-01T00:00:00Z", periods=rows, freq="min"),
            "target_win": target,
            "open_1m_signal": signal,
            "open_1m_ret_1": signal / 10.0,
            "open_5m_ret_1": signal / 20.0,
            "duration_seconds": 300,
            "close_1m_ret_1": idx / 10.0,
            "return_pct": idx / 100.0,
            "net_return_pct": idx / 100.0,
            "pnl": idx / 10.0,
            "raw_return_resolved": idx / 10.0,
            "exit_price_repaired": 100 + idx,
            "mfe_pct": idx / 100.0,
            "mae_pct": idx / 100.0,
            "path_candles": ["[]"] * rows,
        }
    )
    frame.loc[rows - 2, "open_1m_signal"] = -3.0
    frame.loc[rows - 2, "open_1m_ret_1"] = -0.3
    frame.loc[rows - 2, "open_5m_ret_1"] = -0.15
    frame.loc[rows - 1, "open_1m_signal"] = 3.0
    frame.loc[rows - 1, "open_1m_ret_1"] = 0.3
    frame.loc[rows - 1, "open_5m_ret_1"] = 0.15
    return frame


def model_report(status: str = "OK") -> dict:
    return {
        "status": status,
        "best_model": "logistic_regression",
        "limitations": [] if status == "OK" else ["diagnostic_model_report_warning"],
    }


def run_observer(**overrides):
    kwargs = {
        "features": feature_frame(),
        "features_path": "features.parquet",
        "model_report": model_report(),
        "probability_threshold": 0.60,
        "max_rows": 2,
        "seed": 7,
    }
    kwargs.update(overrides)
    return run_ai_shadow_entry_observer(**kwargs)


def test_blocks_if_live_order_or_private_flags_are_enabled() -> None:
    for flag in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
    ):
        with pytest.raises(AIShadowEntryObserverError, match="unsafe_runtime_flags_blocked"):
            run_observer(**{flag: True})


def test_module_does_not_contain_order_submission_functions() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/ml/ai_shadow_entry_observer.py").read_text(encoding="utf-8"),
            MODULE_PATH.read_text(encoding="utf-8"),
        ]
    )
    forbidden = [
        "ccxt",
        "create_order",
        "cancel_order",
        "fetch_balance",
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
        ".env",
        "docker-compose",
        "START_PAPER_24H",
    ]
    assert all(token not in text for token in forbidden)


def test_removes_forbidden_features_and_uses_only_open_features() -> None:
    selected, excluded = select_shadow_feature_columns(feature_frame())

    assert "open_1m_signal" in selected
    assert "open_5m_ret_1" in selected
    assert "duration_seconds" in selected
    assert "target_win" in excluded
    assert "return_pct" in excluded
    assert "net_return_pct" in excluded
    assert "close_1m_ret_1" in excluded
    assert "mfe_pct" in excluded
    assert "mae_pct" in excluded
    assert "path_candles" in excluded
    assert all(column.startswith(("open_1m_", "open_5m_")) or column == "duration_seconds" for column in selected)


def test_generates_shadow_entry_and_shadow_skip_by_threshold() -> None:
    result = run_observer()
    decisions = result["decisions"]

    assert result["report"]["status"] == "OK"
    assert result["report"]["model_name"] == "logistic_regression_shadow_observer"
    assert result["report"]["model_source"] == "model_vs_baseline_financial_evaluation:logistic_regression"
    assert result["report"]["model_version"] == "logistic_regression_in_memory_research_v1"
    assert decisions[0]["model_name"] == result["report"]["model_name"]
    assert decisions[0]["model_source"] == result["report"]["model_source"]
    assert {item["decision"] for item in decisions} == {SHADOW_ENTRY, SHADOW_SKIP}
    assert decisions[0]["probability_win"] < 0.60
    assert decisions[1]["probability_win"] >= 0.60
    assert decisions[0]["decision"] == SHADOW_SKIP
    assert decisions[1]["decision"] == SHADOW_ENTRY


def test_model_metadata_uses_report_selected_model_without_contradiction() -> None:
    result = run_observer(model_report=model_report() | {"best_model": "random_forest"})

    assert result["report"]["model_name"] == "random_forest_shadow_observer"
    assert result["report"]["model_source"] == "model_vs_baseline_financial_evaluation:random_forest"
    assert result["report"]["model_version"] == "random_forest_in_memory_research_v1"
    assert all(item["model_name"] == result["report"]["model_name"] for item in result["decisions"])
    assert all(item["model_source"] == result["report"]["model_source"] for item in result["decisions"])


def test_generates_blocked_when_minimum_features_are_missing() -> None:
    frame = feature_frame()[["trade_id", "symbol", "open_1m_ts", "target_win"]].copy()
    result = run_observer(features=frame)

    assert result["report"]["status"] == BLOCKED
    assert result["report"]["reason"] == "no_open_decision_feature_columns_available"
    assert result["decisions"] == []


def test_report_is_json_serializable() -> None:
    result = run_observer()

    assert json.dumps(result["report"], sort_keys=True)
    assert result["report"]["safety_status"]["order_submission_enabled"] is False
    assert result["report"]["safety_status"]["real_order_submission_enabled"] is False


def test_writes_jsonl_decisions(tmp_path) -> None:
    result = run_observer()
    output = tmp_path / "decisions.jsonl"

    write_jsonl(output, result["decisions"])

    lines = output.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["decision"] in {SHADOW_ENTRY, SHADOW_SKIP} for line in lines)


def test_runner_accepts_tmp_path_without_data_writes(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("run_ai_shadow_entry_observer", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    features_path = tmp_path / "features.parquet"
    model_report_path = tmp_path / "model_report.json"
    output_path = tmp_path / "report.json"
    decisions_path = tmp_path / "decisions.jsonl"
    model_report_path.write_text(json.dumps(model_report()), encoding="utf-8")
    monkeypatch.setattr(module, "read_parquet", lambda path: feature_frame())

    rc = module.main(
        [
            "--features",
            str(features_path),
            "--model-report",
            str(model_report_path),
            "--output",
            str(output_path),
            "--decisions-output",
            str(decisions_path),
            "--probability-threshold",
            "0.60",
            "--max-rows",
            "2",
            "--dry-run",
            "true",
            "--shadow-only",
            "true",
            "--seed",
            "7",
        ]
    )

    assert rc == 0
    assert output_path.exists()
    assert decisions_path.exists()
    assert not (tmp_path / "data").exists()


def test_deterministic_with_seed() -> None:
    first = run_observer(seed=123)
    second = run_observer(seed=123)

    first_pairs = [(item["decision"], round(item["probability_win"], 10), item["decision_id"]) for item in first["decisions"]]
    second_pairs = [(item["decision"], round(item["probability_win"], 10), item["decision_id"]) for item in second["decisions"]]
    assert first_pairs == second_pairs
