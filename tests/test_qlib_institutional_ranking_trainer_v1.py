from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.feature_contracts import build_dataset_manifest, build_feature_contract
from smartcrypto.learning.qlib_trainer import build_qlib_institutional_ranking_trainer_report
from smartcrypto.learning.target_store import build_financial_label_target_store_report
from smartcrypto.learning.walkforward import build_walkforward_anti_leakage_report


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
                "feature_entry_price": 100.0 + index,
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
    build_walkforward_anti_leakage_report(project_root=root, write=True)
    return path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_default_no_write_no_train(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["candidate_decision"] == "NOT_TRAINED_DRY_RUN"
    assert report["training_requested"] is False
    assert report["write_report_performed"] is False
    assert not (tmp_path / "data" / "reports" / "qlib_institutional_ranking_trainer_v1.json").exists()


def test_write_report_outputs_only_reports(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, write_report=True)

    assert report["write_report_performed"] is True
    assert (tmp_path / "data" / "reports" / "qlib_institutional_ranking_trainer_v1.json").exists()
    assert (tmp_path / "data" / "reports" / "qlib_institutional_ranking_metrics_v1.json").exists()
    assert not (tmp_path / "data" / "models").exists()


def test_blocks_train_when_lineage_hash_drift_detected(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    target_path = tmp_path / "data" / "reports" / "financial_label_target_store_v1.json"
    target_store = load_json(target_path)
    target_store["dataset_hash"] = "drift"
    write_json(target_path, target_store)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, train=True, allow_research_fallback=True)

    assert report["status"] == "blocked"
    assert report["lineage_drift_detected"] is True


def test_blocks_target_columns_in_features(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_feature_columns(tmp_path, "target_net_pnl")
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert any("target_columns_in_features" in error for error in report["validation_errors"])


def test_blocks_label_columns_in_features(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_feature_columns(tmp_path, "label_sign")
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert any("label_columns_in_features" in error or "forbidden_role_columns" in error for error in report["validation_errors"])


def test_blocks_outcome_columns_in_features(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_feature_columns(tmp_path, "net_pnl")
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert any("outcome_or_identifier_columns_in_features" in error or "forbidden_role_columns" in error for error in report["validation_errors"])


def test_blocks_future_ret_columns_in_features(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_feature_columns(tmp_path, "future_ret_1")
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert any("future_ret_columns_in_features" in error for error in report["validation_errors"])


def test_blocks_when_walkforward_leakage_not_ok(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_walkforward(tmp_path, {"leakage_audit": {"leakage_status": "blocked"}})
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert "walkforward_leakage_not_ok" in report["validation_errors"]


def test_blocks_when_embargo_violation_exists(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_walkforward(tmp_path, {"leakage_audit": {"leakage_status": "ok", "embargo_violation_count": 1}})
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert "walkforward_embargo_violation_count_nonzero" in report["validation_errors"]


def test_uses_only_feature_contract_columns(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path)
    contract = load_json(tmp_path / "data" / "reports" / "ai_unified_feature_contract_v1.json")

    assert report["feature_columns"] == contract["feature_columns"]


def test_respects_walkforward_split_indices(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, train=True, allow_research_fallback=True)
    walkforward = load_json(tmp_path / "data" / "reports" / "walkforward_anti_leakage_split_engine_v1.json")

    assert [item["split_id"] for item in report["metrics_by_split"]] == [item["split_id"] for item in walkforward["splits"]]


def test_scaler_or_encoder_fit_only_on_train(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(
        project_root=tmp_path,
        train=True,
        write_challenger_artifact=True,
        allow_research_fallback=True,
    )
    model_path = Path(report["artifact_paths"]["model"])
    model_payload = load_json(model_path)
    train_counts = [metric["train_row_count"] for metric in report["metrics_by_split"]]

    assert model_payload["scaler_fit_row_counts"] == train_counts


def test_metrics_are_reported_by_split(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, train=True, allow_research_fallback=True)

    assert report["metrics_by_split"]
    assert "rank_ic" in report["metrics_by_split"][0]


def test_aggregate_metrics_are_reported(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, train=True, allow_research_fallback=True)

    assert report["aggregate_metrics"]["split_count"] == report["evaluated_split_count"]


def test_baseline_comparison_reported(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, train=True, allow_research_fallback=True)

    assert "baseline_random_expected_value" in report["baseline_comparison"]


def test_candidate_decision_never_promote(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, train=True, allow_research_fallback=True)

    assert report["candidate_decision"] in {"MANTER_EM_RESEARCH", "RESEARCH_CHALLENGER_ONLY"}
    assert "PROMOTE" not in report["candidate_decision"]
    assert report["promotion_eligible"] is False


def test_registry_write_blocked(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, registry_write_requested=True)

    assert report["status"] == "blocked"
    assert report["registry_write_performed"] is False


def test_model_promotion_blocked(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, model_promotion_requested=True)

    assert report["status"] == "blocked"
    assert report["model_promotion_performed"] is False


def test_active_model_not_changed(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, train=True, allow_research_fallback=True)

    assert report["active_model_changed"] is False


def test_challenger_artifact_requires_train(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, write_challenger_artifact=True)

    assert report["status"] == "blocked"
    assert "challenger_artifact_requires_train" in report["validation_errors"]


def test_challenger_artifact_written_only_to_challengers_path(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(
        project_root=tmp_path,
        train=True,
        write_challenger_artifact=True,
        allow_research_fallback=True,
    )

    assert report["write_challenger_artifact_performed"] is True
    assert "data\\models\\challengers" in report["artifact_paths"]["metadata"] or "data/models/challengers" in report["artifact_paths"]["metadata"]
    assert "champion" not in report["artifact_paths"]["metadata"]


def test_no_qib_runtime_update(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, train=True, allow_research_fallback=True)

    assert report["qlib_runtime_updated"] is False


def test_no_ai_shadow_training(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path, train=True, allow_research_fallback=True)

    assert report["ai_shadow_training_performed"] is False


def test_no_exchange_private_access(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path)

    assert report["exchange_private_access"] is False


def test_no_orders_sent(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path)

    assert report["sends_orders"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False


def test_safety_flags_preserved(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_qlib_institutional_ranking_trainer_report(project_root=tmp_path)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["changes_risk"] is False


def test_cli_no_write_json_executes(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    completed = subprocess.run(
        [sys.executable, "scripts/train_qlib_institutional_ranking_challenger_v1.py", "--project-root", str(tmp_path), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["candidate_decision"] == "NOT_TRAINED_DRY_RUN"


def test_cli_write_report_json_executes(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/train_qlib_institutional_ranking_challenger_v1.py",
            "--project-root",
            str(tmp_path),
            "--write-report",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["write_report_performed"] is True
    assert (tmp_path / "data" / "reports" / "qlib_institutional_ranking_trainer_v1.json").exists()


def test_cli_train_research_mode_executes_or_blocks_cleanly_when_backend_unavailable(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/train_qlib_institutional_ranking_challenger_v1.py",
            "--project-root",
            str(tmp_path),
            "--train",
            "--write-report",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["candidate_decision"] in {"BLOCKED_BACKEND_UNAVAILABLE", "MANTER_EM_RESEARCH", "RESEARCH_CHALLENGER_ONLY"}
    assert payload["model_promotion_performed"] is False


def mutate_feature_columns(root: Path, column: str) -> None:
    path = root / "data" / "reports" / "ai_unified_feature_contract_v1.json"
    contract = load_json(path)
    contract["feature_columns"].append(column)
    write_json(path, contract)


def mutate_walkforward(root: Path, update: dict[str, Any]) -> None:
    path = root / "data" / "reports" / "walkforward_anti_leakage_split_engine_v1.json"
    payload = load_json(path)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key].update(value)
        else:
            payload[key] = value
    write_json(path, payload)
