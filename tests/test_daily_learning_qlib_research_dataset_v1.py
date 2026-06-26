from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.daily_learning_qlib_research_dataset import (
    DAILY_LEARNING_QLIB_RESEARCH_DATASET_SCHEMA_VERSION,
    build_daily_learning_qlib_research_dataset_report,
    build_qlib_dataset_row,
    build_qlib_research_dataset,
    separate_feature_label_columns,
    validate_daily_learning_qlib_research_dataset_report,
)


def _catalog_entry(trade_id: str = "t1", classification: str = "winner") -> dict:
    return {
        "trade_id": trade_id,
        "classification": classification,
        "subclassification": "profitable_trade" if classification == "winner" else "stop_loss_loss",
        "severity": "low" if classification == "winner" else "high",
        "symbol": "BTCUSDT",
        "side": "long",
        "source": "paper",
        "confidence": 0.8,
    }


def _feature_row(trade_id: str = "t1") -> dict:
    return {
        "trade_id": trade_id,
        "symbol": "BTCUSDT",
        "side": "long",
        "entry_time": "2026-06-26T10:00:00Z",
        "has_entry_candle": True,
        "max_lookback_covered": 30,
        "entry_close": 100.0,
        "rsi_14": 61.0,
        "dist_sma_20_pct": 0.012,
        "pre_entry_volatility_20": 0.004,
        "lb_5m_ret_close": 0.001,
        "lb_10m_ret_close": 0.002,
        "lb_30m_ret_close": 0.003,
        "net_pnl": 999.0,
    }


def test_report_with_none_inputs_uses_no_runtime_mode() -> None:
    report = build_daily_learning_qlib_research_dataset_report(project_root=".")
    assert report["schema_version"] == DAILY_LEARNING_QLIB_RESEARCH_DATASET_SCHEMA_VERSION
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["write_performed"] is False
    assert report["runs_training"] is False
    assert report["updates_qlib_runtime"] is False
    assert report["operational_authority"] is False
    assert report["validation_errors"] == []


def test_report_with_empty_lists_is_blocked() -> None:
    report = build_daily_learning_qlib_research_dataset_report(
        catalog_entries=[], feature_rows=[]
    )
    assert report["status"] == "blocked"
    assert report["qlib_research_dataset"]["dataset_row_count"] == 0
    assert report["validation_errors"] == []


def test_build_row_separates_pre_entry_features_and_labels() -> None:
    row = build_qlib_dataset_row(_catalog_entry(), _feature_row(), 0)
    assert row["feature_rsi_14"] == 61.0
    assert row["feature_lb_10m_ret_close"] == 0.002
    assert row["label_classification"] == "winner"
    assert row["label_is_winner"] == 1
    assert row["uses_net_pnl_as_feature"] is False
    assert "feature_net_pnl" not in row


def test_feature_columns_exclude_outcome_fragments() -> None:
    dataset = build_qlib_research_dataset(
        catalog_entries=[_catalog_entry()], feature_rows=[_feature_row()]
    )
    forbidden_fragments = ["pnl", "profit", "loss", "winner", "mistake", "label"]
    assert dataset["feature_columns"]
    for column in dataset["feature_columns"]:
        lowered = column.lower()
        assert lowered.startswith("feature_")
        assert not any(fragment in lowered for fragment in forbidden_fragments)


def test_label_columns_are_separate_from_features() -> None:
    dataset = build_qlib_research_dataset(
        catalog_entries=[_catalog_entry()], feature_rows=[_feature_row()]
    )
    assert "label_classification" in dataset["label_columns"]
    assert "label_classification" not in dataset["feature_columns"]
    assert dataset["dataset_scope"]["separates_features_and_labels"] is True


def test_dataset_summary_counts_classification_symbol_and_side() -> None:
    dataset = build_qlib_research_dataset(
        catalog_entries=[_catalog_entry("t1", "winner"), _catalog_entry("t2", "mistake")],
        feature_rows=[_feature_row("t1"), _feature_row("t2")],
    )
    summary = dataset["dataset_summary"]
    assert summary["row_count"] == 2
    assert summary["classification_counts"] == {"mistake": 1, "winner": 1}
    assert summary["symbol_counts"] == {"BTCUSDT": 2}
    assert summary["side_counts"] == {"long": 2}


def test_context_summary_is_not_used_as_features() -> None:
    dataset = build_qlib_research_dataset(
        catalog_entries=[_catalog_entry()],
        feature_rows=[_feature_row()],
        oos_validation_results=[{"oos_status": "oos_research_pass"}],
        feedback_events=[{"feedback_type": "candidate_positive_signal"}],
        candidate_rules=[{"rule_kind": "allow_candidate"}],
    )
    context = dataset["research_context_summary"]
    assert context["oos_validation_result_count"] == 1
    assert context["feedback_event_count"] == 1
    assert context["candidate_rule_count"] == 1
    assert context["context_used_as_features"] is False
    assert context["context_used_for_training"] is False


def test_sample_is_limited_to_twenty_rows() -> None:
    catalog = [_catalog_entry(f"t{i}", "winner") for i in range(25)]
    features = [_feature_row(f"t{i}") for i in range(25)]
    dataset = build_qlib_research_dataset(catalog_entries=catalog, feature_rows=features)
    assert dataset["dataset_row_count"] == 25
    assert len(dataset["qlib_rows_sample"]) == 20


def test_separate_feature_label_columns() -> None:
    columns = separate_feature_label_columns(
        [{"feature_a": 1, "label_b": 0, "trade_id": "t1"}]
    )
    assert columns["feature_columns"] == ["feature_a"]
    assert columns["label_columns"] == ["label_b"]
    assert "trade_id" in columns["metadata_columns"]


def test_validation_rejects_training_or_runtime_authority() -> None:
    report = build_daily_learning_qlib_research_dataset_report()
    report["runs_training"] = True
    report["updates_qlib_runtime"] = True
    errors = validate_daily_learning_qlib_research_dataset_report(report)
    assert "runs_training_must_be_false" in errors
    assert "updates_qlib_runtime_must_be_false" in errors


def test_validation_rejects_unsafe_feature_column() -> None:
    report = build_daily_learning_qlib_research_dataset_report(
        catalog_entries=[_catalog_entry()], feature_rows=[_feature_row()]
    )
    report["qlib_research_dataset"]["feature_columns"].append("feature_net_pnl")
    errors = validate_daily_learning_qlib_research_dataset_report(report)
    assert "unsafe_feature_column:feature_net_pnl" in errors


def test_validation_rejects_operational_authority_and_writes() -> None:
    report = build_daily_learning_qlib_research_dataset_report()
    report["operational_authority"] = True
    report["writes_data"] = True
    errors = validate_daily_learning_qlib_research_dataset_report(report)
    assert "operational_authority_must_be_false" in errors
    assert "writes_data_must_be_false" in errors


def test_cli_no_write_json_returns_valid_payload() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_daily_learning_qlib_research_dataset_v1.py",
            "--project-root",
            ".",
            "--no-write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["input_mode"] == "no_runtime_rows_loaded"
    assert payload["write_performed"] is False
    assert payload["validation_errors"] == []


def test_cli_output_to_temp_path_writes_payload(tmp_path: Path) -> None:
    output = tmp_path / "qlib_research_dataset.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_daily_learning_qlib_research_dataset_v1.py",
            "--project-root",
            ".",
            "--output",
            str(output),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["write_requested"] is True
    assert payload["write_performed"] is True
    assert output.exists()


def test_cli_blocks_output_under_project_data(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    blocked_output = project_root / "data" / "blocked.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_daily_learning_qlib_research_dataset_v1.py",
            "--project-root",
            str(project_root),
            "--output",
            str(blocked_output),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["write_performed"] is False
    assert "output_path_under_blocked_project_area" in payload["validation_errors"]
    assert not blocked_output.exists()


def test_static_files_avoid_disallowed_dependencies() -> None:
    disallowed_parts = [
        "req" + "uests",
        "ht" + "tpx",
        "aio" + "http",
        "c" + "cxt",
        "pan" + "das",
        "open" + "pyxl",
        "sql" + "ite3",
        "to_" + "parquet",
        "to_" + "excel",
        "to_" + "sql",
        "create_" + "order",
        "cancel_" + "order",
        "fetch_" + "balance",
        "send_" + "order",
        "TELE" + "GRAM",
        "NT" + "FY",
        "BINANCE_" + "SECRET",
        "BINANCE_" + "API_KEY",
    ]
    files = [
        Path("smartcrypto/research/daily_learning_qlib_research_dataset.py"),
        Path("scripts/build_daily_learning_qlib_research_dataset_v1.py"),
        Path("docs/DAILY_LEARNING_QLIB_RESEARCH_DATASET_V1.md"),
    ]
    for file_path in files:
        text = file_path.read_text(encoding="utf-8")
        for token in disallowed_parts:
            assert token not in text
