from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.feature_contracts import build_dataset_manifest, build_feature_contract
from smartcrypto.learning.target_store import build_financial_label_target_store_report


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
            "duration_seconds": 300,
            "is_closed": True,
            "label_win_loss": "win",
            "label_sign": 1,
            "net_pnl": 1.25,
            "gross_pnl": 1.35,
            "profit_ratio": 0.01,
            "trading_fee": 0.05,
            "funding_fee": 0.01,
            "exit_price": 101.0,
            "exit_reason": "roi",
            "roi_hit": True,
            "stoploss_hit": False,
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
            "duration_seconds": 600,
            "is_closed": True,
            "label_win_loss": "loss",
            "label_sign": -1,
            "net_pnl": -0.5,
            "gross_pnl": -0.45,
            "profit_ratio": -0.004,
            "trading_fee": 0.04,
            "funding_fee": 0.0,
            "exit_price": 99.0,
            "exit_reason": "stoploss",
            "roi_hit": False,
            "stoploss_hit": True,
            "feature_side_long": 0,
            "feature_side_short": 1,
            "feature_symbol_btcusdt": 0,
            "feature_symbol_ethusdt": 1,
            "feature_entry_price": 100.0,
            "feature_quantity": 0.2,
            "feature_leverage": 2.0,
        },
        {
            "event_id": "e3",
            "order_id": "o3",
            "trade_id": "t3",
            "symbol_norm": "BTCUSDT",
            "side": "long",
            "open_time_utc": "2026-07-01T12:00:00Z",
            "close_time_utc": "2026-07-01T12:20:00Z",
            "duration_seconds": 1200,
            "is_closed": True,
            "label_win_loss": "breakeven",
            "label_sign": 0,
            "net_pnl": 0.0,
            "gross_pnl": 0.0,
            "profit_ratio": 0.0,
            "trading_fee": 0.0,
            "funding_fee": 0.0,
            "exit_price": 100.0,
            "exit_reason": "time_exit",
            "roi_hit": False,
            "stoploss_hit": False,
            "feature_side_long": 1,
            "feature_side_short": 0,
            "feature_symbol_btcusdt": 1,
            "feature_symbol_ethusdt": 0,
            "feature_entry_price": 100.0,
            "feature_quantity": 0.1,
            "feature_leverage": 2.0,
        },
    ]


def write_project_dataset(root: Path, rows: list[dict[str, Any]] | None = None) -> Path:
    path = root / "data" / "feedback" / "training_microbatches" / "2026-07-01.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows or microbatch_rows()).to_parquet(path, index=False)
    frame = pd.read_parquet(path)
    contract = build_feature_contract(frame, source_datasets=[str(path)])
    manifest = build_dataset_manifest(
        frame,
        selected_dataset_path=path,
        source_paths=[path],
        feature_contract_hash=contract["contract_hash"],
        label_columns=contract["label_columns"],
    )
    report_dir = root / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "ai_unified_feature_contract_v1.json").write_text(json.dumps(contract), encoding="utf-8")
    (report_dir / "ai_unified_dataset_manifest_v1.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_default_no_write(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "financial_label_target_store_v1.json").exists()


def test_write_outputs_only_reports(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path, write=True)

    assert report["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "financial_label_target_store_v1.json").exists()
    assert (tmp_path / "data" / "reports" / "financial_label_target_store_v1.md").exists()
    assert (tmp_path / "data" / "reports" / "financial_label_target_store_summary_v1.json").exists()
    assert (tmp_path / "data" / "reports" / "financial_label_target_store_summary_v1.md").exists()
    assert not list((tmp_path / "data" / "reports").rglob("*.parquet"))


def test_target_store_hash_is_deterministic(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    first = build_financial_label_target_store_report(project_root=tmp_path)
    second = build_financial_label_target_store_report(project_root=tmp_path)

    assert first["target_store_hash"] == second["target_store_hash"]


def test_target_columns_are_deterministic(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["target_columns"] == report["target_store"]["target_columns"]
    assert report["target_column_count"] == 20


def test_targets_derived_from_closed_trades_only(tmp_path: Path) -> None:
    rows = microbatch_rows()
    open_row = dict(rows[0])
    open_row["event_id"] = "open"
    open_row["is_closed"] = False
    open_row["close_time_utc"] = None
    rows.append(open_row)
    write_project_dataset(tmp_path, rows)

    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["target_row_count"] == 3


def test_blocks_dataset_without_net_pnl(tmp_path: Path) -> None:
    rows = [{key: value for key, value in row.items() if key != "net_pnl"} for row in microbatch_rows()]
    write_project_dataset(tmp_path, rows)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert "missing_net_pnl" in report["validation_errors"]


def test_blocks_dataset_without_profit_ratio(tmp_path: Path) -> None:
    rows = [{key: value for key, value in row.items() if key != "profit_ratio"} for row in microbatch_rows()]
    write_project_dataset(tmp_path, rows)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert "missing_profit_ratio" in report["validation_errors"]


def test_blocks_dataset_without_labels(tmp_path: Path) -> None:
    rows = [
        {key: value for key, value in row.items() if key not in {"label_sign", "label_win_loss"}}
        for row in microbatch_rows()
    ]
    write_project_dataset(tmp_path, rows)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert "missing_labels" in report["validation_errors"]


def test_triple_barrier_mode_is_closed_trade_derived(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["triple_barrier_mode"] == "closed_trade_derived_v1"
    assert report["target_store"]["triple_barrier_mode"] == "closed_trade_derived_v1"


def test_intrabar_full_triple_barrier_not_claimed(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["intrabar_price_path_available"] is False
    assert report["candle_path_required_for_full_triple_barrier"] is True
    assert report["target_store"]["triple_barrier_config"]["full_triple_barrier_claimed"] is False


def test_roi_stoploss_time_exit_counts(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["target_roi_hit_count"] == 1
    assert report["target_stoploss_hit_count"] == 1
    assert report["target_time_exit_count"] == 1


def test_expected_value_components_are_reported(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["expected_value_proxy_total"] < report["avg_target_net_pnl"] * report["target_row_count"]
    assert report["cost_total"] > 0
    assert report["risk_penalty_total"] > 0
    assert "expected_value_config" in report["target_store"]


def test_target_columns_not_added_to_features(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)
    feature_columns = report["target_store"]["source_hashes"]  # source hashes are evidence, not features.
    contract = json.loads((tmp_path / "data" / "reports" / "ai_unified_feature_contract_v1.json").read_text(encoding="utf-8"))

    assert feature_columns
    assert not any(column.startswith("target_") for column in contract["feature_columns"])


def test_feature_contract_hash_preserved(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    contract = json.loads((tmp_path / "data" / "reports" / "ai_unified_feature_contract_v1.json").read_text(encoding="utf-8"))
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["feature_contract_hash"] == contract["contract_hash"]
    assert report["target_store"]["feature_contract_hash"] == contract["contract_hash"]


def test_dataset_hash_preserved(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    manifest = json.loads((tmp_path / "data" / "reports" / "ai_unified_dataset_manifest_v1.json").read_text(encoding="utf-8"))
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["dataset_hash"] == manifest["dataset_hash"]
    assert report["target_store"]["dataset_hash"] == manifest["dataset_hash"]


def test_no_training_performed(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["training_requested"] is False
    assert report["qlib_training_performed"] is False
    assert report["ai_shadow_training_performed"] is False


def test_no_registry_write_performed(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["registry_write_performed"] is False


def test_no_model_promotion(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False


def test_no_exchange_private_access(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["exchange_private_access"] is False


def test_no_orders_sent(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["sends_orders"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False


def test_safety_flags_preserved(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    report = build_financial_label_target_store_report(project_root=tmp_path)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["safety_flags"]["paper_only"] is True


def test_cli_no_write_json_executes(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_financial_label_target_store_v1.py",
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
    assert payload["target_store_status"] == "ok"
    assert "target_records" not in payload["target_store"]
    assert payload["target_store"]["target_records_count"] == 3


def test_cli_write_json_executes(tmp_path: Path) -> None:
    write_project_dataset(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_financial_label_target_store_v1.py",
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
    assert (tmp_path / "data" / "reports" / "financial_label_target_store_v1.json").exists()
