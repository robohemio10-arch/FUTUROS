from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.feature_contracts import (
    build_dataset_manifest,
    build_feature_contract,
    build_unified_feature_contract_report,
)


def microbatch_rows() -> list[dict[str, Any]]:
    return [
        {
            "event_id": "e1",
            "order_id": "o1",
            "trade_id": "t1",
            "symbol_norm": "BTCUSDT",
            "side": "long",
            "open_time_utc": "2026-07-01T10:00:00Z",
            "close_time_utc": "2026-07-01T10:05:00Z",
            "label_win_loss": "win",
            "label_sign": 1,
            "net_pnl": 1.25,
            "profit_ratio": 0.01,
            "exit_price": 101.0,
            "exit_reason": "roi",
            "feature_side_long": 1,
            "feature_side_short": 0,
            "feature_symbol_btcusdt": 1,
            "feature_symbol_ethusdt": 0,
            "feature_entry_price": 100.0,
            "feature_quantity": 0.1,
            "feature_leverage": 2.0,
        },
        {
            "event_id": "e2",
            "order_id": "o2",
            "trade_id": "t2",
            "symbol_norm": "ETHUSDT",
            "side": "short",
            "open_time_utc": "2026-07-01T11:00:00Z",
            "close_time_utc": "2026-07-01T11:10:00Z",
            "label_win_loss": "loss",
            "label_sign": -1,
            "net_pnl": -0.5,
            "profit_ratio": -0.004,
            "exit_price": 99.0,
            "exit_reason": "stoploss",
            "feature_side_long": 0,
            "feature_side_short": 1,
            "feature_symbol_btcusdt": 0,
            "feature_symbol_ethusdt": 1,
            "feature_entry_price": 100.0,
            "feature_quantity": None,
            "feature_leverage": 2.0,
        },
    ]


def write_project_dataset(root: Path, rows: list[dict[str, Any]] | None = None) -> Path:
    path = root / "data" / "feedback" / "training_microbatches" / "2026-07-01.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows or microbatch_rows()).to_parquet(path, index=False)
    return path


def test_default_no_write(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_unified_feature_contract_report(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "ai_unified_feature_contract_v1.json").exists()


def test_write_outputs_only_reports(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_unified_feature_contract_report(project_root=tmp_path, write=True)

    assert report["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "ai_unified_feature_contract_v1.json").exists()
    assert (tmp_path / "data" / "reports" / "ai_unified_feature_contract_v1.md").exists()
    assert (tmp_path / "data" / "reports" / "ai_unified_dataset_manifest_v1.json").exists()
    assert (tmp_path / "data" / "reports" / "ai_unified_dataset_manifest_v1.md").exists()
    assert list((tmp_path / "data" / "reports").glob("*"))


def test_feature_contract_detects_feature_columns() -> None:
    contract = build_feature_contract(pd.DataFrame(microbatch_rows()), source_datasets=["fixture"])

    assert contract["validation_status"] == "ok"
    assert contract["feature_columns"] == sorted(contract["feature_columns"])
    assert "feature_side_long" in contract["feature_columns"]
    assert "feature_symbol_btcusdt" in contract["feature_columns"]


def test_dataset_manifest_hash_is_deterministic(tmp_path: Path) -> None:
    path = write_project_dataset(tmp_path)
    frame = pd.read_parquet(path)
    contract = build_feature_contract(frame, source_datasets=[str(path)])

    first = build_dataset_manifest(frame, selected_dataset_path=path, source_paths=[path], feature_contract_hash=contract["contract_hash"], label_columns=contract["label_columns"])
    second = build_dataset_manifest(frame, selected_dataset_path=path, source_paths=[path], feature_contract_hash=contract["contract_hash"], label_columns=contract["label_columns"])

    assert first["dataset_hash"] == second["dataset_hash"]


def test_contract_hash_is_deterministic() -> None:
    frame = pd.DataFrame(microbatch_rows())
    first = build_feature_contract(frame, source_datasets=["fixture"])
    second = build_feature_contract(frame, source_datasets=["fixture"])

    assert first["contract_hash"] == second["contract_hash"]
    assert first["schema_hash"] == second["schema_hash"]


def test_feature_order_is_deterministic() -> None:
    frame = pd.DataFrame(microbatch_rows())[
        ["feature_symbol_ethusdt", "label_sign", "feature_side_long", "feature_symbol_btcusdt", "feature_side_short"]
    ]
    contract = build_feature_contract(frame, source_datasets=["fixture"])

    assert contract["deterministic_feature_order"] is True
    assert contract["feature_columns"] == sorted(contract["feature_columns"])


def test_future_ret_columns_are_forbidden_as_features() -> None:
    frame = pd.DataFrame(microbatch_rows())
    frame["future_ret_1"] = [0.1, -0.1]
    contract = build_feature_contract(frame, source_datasets=["fixture"])

    assert "future_ret_1" in contract["forbidden_columns"]
    assert "future_ret_1" not in contract["feature_columns"]
    assert contract["future_ret_columns_detected"] == ["future_ret_1"]


def test_label_columns_are_not_features() -> None:
    contract = build_feature_contract(pd.DataFrame(microbatch_rows()), source_datasets=["fixture"])

    assert "label_sign" in contract["label_columns"]
    assert "label_sign" not in contract["feature_columns"]


def test_outcome_columns_are_not_features() -> None:
    contract = build_feature_contract(pd.DataFrame(microbatch_rows()), source_datasets=["fixture"])

    assert "exit_reason" in contract["outcome_columns"]
    assert "exit_reason" not in contract["feature_columns"]


def test_net_pnl_profit_ratio_are_not_features() -> None:
    contract = build_feature_contract(pd.DataFrame(microbatch_rows()), source_datasets=["fixture"])

    assert "net_pnl" in contract["outcome_columns"]
    assert "profit_ratio" in contract["outcome_columns"]
    assert "net_pnl" not in contract["feature_columns"]
    assert "profit_ratio" not in contract["feature_columns"]


def test_close_time_exit_price_are_not_features() -> None:
    contract = build_feature_contract(pd.DataFrame(microbatch_rows()), source_datasets=["fixture"])

    assert "close_time_utc" in contract["outcome_columns"]
    assert "exit_price" in contract["outcome_columns"]
    assert "close_time_utc" not in contract["feature_columns"]
    assert "exit_price" not in contract["feature_columns"]


def test_identifier_columns_are_not_features() -> None:
    contract = build_feature_contract(pd.DataFrame(microbatch_rows()), source_datasets=["fixture"])

    assert "order_id" in contract["identifier_columns"]
    assert "event_id" in contract["identifier_columns"]
    assert "order_id" not in contract["feature_columns"]


def test_blocks_dataset_without_valid_label() -> None:
    frame = pd.DataFrame(microbatch_rows()).drop(columns=["label_sign", "label_win_loss"])
    contract = build_feature_contract(frame, source_datasets=["fixture"])

    assert contract["validation_status"] == "blocked"
    assert "missing_valid_label_columns" in contract["validation_errors"]


def test_blocks_dataset_without_valid_feature() -> None:
    frame = pd.DataFrame(microbatch_rows()).drop(columns=[column for column in pd.DataFrame(microbatch_rows()).columns if column.startswith("feature_")])
    contract = build_feature_contract(frame, source_datasets=["fixture"])

    assert contract["validation_status"] == "blocked"
    assert "missing_valid_feature_columns" in contract["validation_errors"]


def test_reports_null_counts_and_dtypes(tmp_path: Path) -> None:
    path = write_project_dataset(tmp_path)
    report = build_unified_feature_contract_report(project_root=tmp_path)
    manifest = report["dataset_manifest"]

    assert manifest["null_counts"]["feature_quantity"] == 1
    assert "feature_quantity" in manifest["dtype_map"]
    assert manifest["row_count"] == 2


def test_reports_lineage_and_source_hashes(tmp_path: Path) -> None:
    path = write_project_dataset(tmp_path)
    report = build_unified_feature_contract_report(project_root=tmp_path)
    manifest = report["dataset_manifest"]

    assert manifest["selected_training_dataset"] == str(path.resolve())
    assert str(path.resolve()) in manifest["source_hashes"]
    assert manifest["dataset_lineage"]["training_performed"] is False


def test_no_training_performed(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_unified_feature_contract_report(project_root=tmp_path)

    assert report["training_requested"] is False
    assert report["qlib_training_performed"] is False
    assert report["ai_shadow_training_performed"] is False


def test_no_registry_write_performed(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_unified_feature_contract_report(project_root=tmp_path)

    assert report["registry_write_performed"] is False


def test_no_model_promotion(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_unified_feature_contract_report(project_root=tmp_path)

    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False


def test_no_exchange_private_access(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_unified_feature_contract_report(project_root=tmp_path)

    assert report["exchange_private_access"] is False


def test_no_orders_sent(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_unified_feature_contract_report(project_root=tmp_path)

    assert report["sends_orders"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False


def test_safety_flags_preserved(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_unified_feature_contract_report(project_root=tmp_path)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["safety_flags"]["paper_only"] is True


def test_cli_no_write_json_executes(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_ai_unified_feature_contract_v1.py",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["write_performed"] is False
    assert payload["feature_contract_status"] == "ok"
    assert payload["dataset_manifest_status"] == "ok"


def test_cli_write_json_executes(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_ai_unified_feature_contract_v1.py",
            "--project-root",
            str(tmp_path),
            "--write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "ai_unified_feature_contract_v1.json").exists()
