from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.feature_contracts import build_dataset_manifest, build_feature_contract
from smartcrypto.learning.target_store import build_financial_label_target_store_report
from smartcrypto.learning.walkforward import build_walkforward_anti_leakage_report
from smartcrypto.learning.walkforward.baselines import build_baseline_summary
from smartcrypto.learning.walkforward.purged_split_engine import embargo_indices, purge_indices


def microbatch_rows(count: int = 36) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = pd.Timestamp("2026-07-01T00:00:00Z")
    for index in range(count):
        opened = start + pd.Timedelta(days=index)
        closed = opened + pd.Timedelta(minutes=30)
        is_win = index % 3 == 0
        net_pnl = 1.0 if is_win else -0.5
        rows.append(
            {
                "event_id": f"e{index}",
                "order_id": f"o{index}",
                "trade_id": f"t{index}",
                "symbol_norm": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
                "side": "long" if index % 2 == 0 else "short",
                "open_time_utc": opened.isoformat(),
                "close_time_utc": closed.isoformat(),
                "duration_seconds": 1800,
                "is_closed": True,
                "label_win_loss": "win" if is_win else "loss",
                "label_sign": 1 if is_win else -1,
                "net_pnl": net_pnl,
                "gross_pnl": net_pnl,
                "profit_ratio": 0.01 if is_win else -0.005,
                "exit_price": 101.0 if is_win else 99.0,
                "exit_reason": "roi" if is_win else "stoploss",
                "roi_hit": is_win,
                "stoploss_hit": not is_win,
                "feature_side_long": 1 if index % 2 == 0 else 0,
                "feature_side_short": 0 if index % 2 == 0 else 1,
                "feature_symbol_btcusdt": 1 if index % 2 == 0 else 0,
                "feature_symbol_ethusdt": 0 if index % 2 == 0 else 1,
                "feature_entry_price": 100.0,
                "feature_quantity": 0.1,
                "feature_leverage": 2.0,
            }
        )
    return rows


def write_project_inputs(root: Path, rows: list[dict[str, Any]] | None = None) -> Path:
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
    build_financial_label_target_store_report(project_root=root, write=True)
    return path


def load_contract(root: Path) -> dict[str, Any]:
    return json.loads((root / "data" / "reports" / "ai_unified_feature_contract_v1.json").read_text(encoding="utf-8"))


def write_contract(root: Path, contract: dict[str, Any]) -> None:
    (root / "data" / "reports" / "ai_unified_feature_contract_v1.json").write_text(json.dumps(contract), encoding="utf-8")


def test_default_no_write(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "walkforward_anti_leakage_split_engine_v1.json").exists()


def test_write_outputs_only_reports(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path, write=True)

    assert report["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "walkforward_anti_leakage_split_engine_v1.json").exists()
    assert (tmp_path / "data" / "reports" / "walkforward_anti_leakage_split_engine_v1.md").exists()
    assert (tmp_path / "data" / "reports" / "walkforward_baseline_summary_v1.json").exists()
    assert (tmp_path / "data" / "reports" / "walkforward_baseline_summary_v1.md").exists()
    assert not list((tmp_path / "data" / "reports").rglob("*.parquet"))


def test_split_engine_hash_is_deterministic(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    first = build_walkforward_anti_leakage_report(project_root=tmp_path)
    second = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert first["split_engine_hash"] == second["split_engine_hash"]


def test_split_hashes_are_deterministic(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    first = build_walkforward_anti_leakage_report(project_root=tmp_path)
    second = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert [split["split_hash"] for split in first["split_engine"]["splits"]] == [
        split["split_hash"] for split in second["split_engine"]["splits"]
    ]


def test_random_baseline_is_seeded_and_deterministic(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)
    frame = pd.DataFrame(report["split_engine"]["baseline_summary"], index=[0])

    assert report["baseline_status"] == "ok"
    assert report["random_deterministic_expected_value"] == report["split_engine"]["baseline_summary"]["random_deterministic_expected_value"]
    assert build_baseline_summary(pd.DataFrame(microbatch_rows()), seed=1337)["baseline_seed"] == frame["baseline_seed"].iloc[0]


def test_no_random_dataset_split(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["split_engine"]["split_policy"]["random_split_used"] is False
    assert report["split_engine"]["split_policy"]["shuffle_used"] is False


def test_open_close_interval_purging_removes_overlap() -> None:
    frame = pd.DataFrame(
        {
            "open_time_utc": pd.to_datetime(["2026-07-01T00:00:00Z", "2026-07-01T01:00:00Z"], utc=True),
            "close_time_utc": pd.to_datetime(["2026-07-01T02:00:00Z", "2026-07-01T01:30:00Z"], utc=True),
        }
    )

    assert purge_indices(frame, [0], [1]) == {0}


def test_embargo_removes_rows_after_validation_or_test() -> None:
    frame = pd.DataFrame(
        {
            "open_time_utc": pd.to_datetime(["2026-07-01T00:00:00Z", "2026-07-01T02:00:00Z"], utc=True),
            "close_time_utc": pd.to_datetime(["2026-07-01T00:30:00Z", "2026-07-01T02:30:00Z"], utc=True),
        }
    )

    assert embargo_indices(frame, [1], [0], 86_400) == {1}


def test_embargo_seconds_derived_from_vertical_barrier(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["embargo_seconds"] >= 86_400


def test_blocks_missing_open_time(tmp_path: Path) -> None:
    rows = [{key: value for key, value in row.items() if key != "open_time_utc"} for row in microbatch_rows()]
    write_project_inputs(tmp_path, rows)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert "missing_open_time_utc" in report["validation_errors"]


def test_blocks_missing_close_time_and_holding_seconds(tmp_path: Path) -> None:
    rows = [{key: value for key, value in row.items() if key not in {"close_time_utc", "duration_seconds"}} for row in microbatch_rows()]
    write_project_inputs(tmp_path, rows)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert "missing_close_time_or_target_holding_seconds" in report["validation_errors"]


def test_blocks_target_columns_in_features(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    contract = load_contract(tmp_path)
    contract["feature_columns"].append("target_net_pnl")
    write_contract(tmp_path, contract)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["target_columns_in_features_count"] == 1


def test_blocks_label_columns_in_features(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    contract = load_contract(tmp_path)
    contract["feature_columns"].append("label_sign")
    write_contract(tmp_path, contract)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["outcome_columns_in_features_count"] == 1


def test_blocks_future_ret_columns_in_features(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    contract = load_contract(tmp_path)
    contract["feature_columns"].append("future_ret_1")
    write_contract(tmp_path, contract)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["future_columns_in_features_count"] == 1


def test_leakage_audit_reports_zero_overlap_for_valid_splits(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["leakage_status"] == "ok"
    assert report["temporal_overlap_count"] == 0
    assert report["embargo_violation_count"] == 0
    assert report["label_interval_overlap_count"] == 0


def test_baselines_are_reported(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["baseline_status"] == "ok"
    assert "always_allow_expected_value" in report
    assert "always_block_expected_value" in report


def test_feature_contract_hash_preserved(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    contract = load_contract(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["feature_contract_hash"] == contract["contract_hash"]


def test_dataset_hash_preserved(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    manifest = json.loads((tmp_path / "data" / "reports" / "ai_unified_dataset_manifest_v1.json").read_text(encoding="utf-8"))
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["dataset_hash"] == manifest["dataset_hash"]


def test_target_store_hash_preserved(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    target_store = json.loads((tmp_path / "data" / "reports" / "financial_label_target_store_v1.json").read_text(encoding="utf-8"))
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["target_store_hash"] == target_store["target_store_hash"]


def test_no_training_performed(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["training_requested"] is False
    assert report["qlib_training_performed"] is False
    assert report["ai_shadow_training_performed"] is False


def test_no_registry_write_performed(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["registry_write_performed"] is False


def test_no_model_promotion(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False


def test_no_exchange_private_access(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["exchange_private_access"] is False


def test_no_orders_sent(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["sends_orders"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False


def test_safety_flags_preserved(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_walkforward_anti_leakage_report(project_root=tmp_path)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["live_release_allowed"] is False
    assert report["canary_release_allowed"] is False
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False


def test_cli_no_write_json_executes(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_walkforward_anti_leakage_split_engine_v1.py",
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
    assert payload["split_engine_status"] == "ok"
    assert "splits" not in payload["split_engine"]
    assert payload["split_engine"]["splits_count"] > 0


def test_cli_write_json_executes(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/build_walkforward_anti_leakage_split_engine_v1.py",
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
    assert (tmp_path / "data" / "reports" / "walkforward_anti_leakage_split_engine_v1.json").exists()
