from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.ai_shadow_trainer import build_ai_shadow_quality_veto_trainer_report
from smartcrypto.learning.feature_contracts import build_dataset_manifest, build_feature_contract
from smartcrypto.learning.target_store import build_financial_label_target_store_report
from smartcrypto.learning.walkforward import build_walkforward_anti_leakage_report


def microbatch_rows(count: int = 36) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = pd.Timestamp("2026-07-01T00:00:00Z")
    for index in range(count):
        opened = start + pd.Timedelta(days=index)
        is_win = index % 3 == 0
        net_pnl = 1.0 if is_win else -0.5
        rows.append(
            {
                "event_id": f"e{index}",
                "order_id": f"o{index}",
                "trade_id": f"t{index}",
                "symbol_norm": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
                "side": "long" if index % 4 in {0, 1} else "short",
                "open_time_utc": opened.isoformat(),
                "close_time_utc": (opened + pd.Timedelta(minutes=30)).isoformat(),
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
                "feature_side_long": 1 if index % 4 in {0, 1} else 0,
                "feature_side_short": 0 if index % 4 in {0, 1} else 1,
                "feature_symbol_btcusdt": 1 if index % 2 == 0 else 0,
                "feature_symbol_ethusdt": 0 if index % 2 == 0 else 1,
                "feature_entry_price": 100.0 + index,
                "feature_quantity": 0.1,
                "feature_leverage": 2.0,
                "feature_notional": 10.0 + index,
                "feature_paper_candidate_filter_called": False,
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

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["trainer_status"] == "ok"
    assert report["training_requested"] is False
    assert report["write_report_performed"] is False
    assert not (tmp_path / "data" / "reports" / "ai_shadow_quality_veto_trainer_v1.json").exists()


def test_write_report_outputs_only_reports(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, write_report=True)

    assert report["write_report_performed"] is True
    assert (tmp_path / "data" / "reports" / "ai_shadow_quality_veto_trainer_v1.json").exists()
    assert (tmp_path / "data" / "reports" / "ai_shadow_quality_veto_metrics_v1.json").exists()
    assert not (tmp_path / "data" / "models").exists()


def test_train_generates_probability_quality_and_thresholds(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True, write_report=True)

    assert report["status"] == "ok"
    assert report["trainer_status"] == "ok"
    assert report["lineage_drift_detected"] is False
    assert report["split_count"] == 3
    assert report["feature_column_count"] == 9
    assert report["trained_split_count"] == 3
    assert report["aggregate_metrics"]["split_count"] == 3
    assert report["probability_column"] == "probability_quality"
    assert report["probability_output"] == "probability_quality"
    assert report["decision_output"] == "ai_shadow_candidate_decision"
    assert report["threshold_by_symbol_side_regime"]
    assert all("threshold_quality" in row for row in report["threshold_by_symbol_side_regime"])


def test_train_blocks_only_for_backend_unavailable_when_backend_missing(tmp_path: Path, monkeypatch: Any) -> None:
    write_project_inputs(tmp_path)
    monkeypatch.setattr(
        "smartcrypto.learning.ai_shadow_trainer.quality_veto_trainer.sklearn_backend_available",
        lambda: False,
    )

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["status"] == "blocked"
    assert report["trainer_status"] == "blocked"
    assert report["reason"] == "ai_shadow_backend_unavailable"
    assert report["ai_shadow_challenger_training_performed"] is False


def test_safety_flags_preserve_no_runtime_authority(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["promotion_eligible"] is False
    assert report["ai_shadow_runtime_updated"] is False
    assert report["veto_runtime_active"] is False
    assert report["veto_registry_write_performed"] is False
    assert report["registry_write_performed"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["qlib_runtime_updated"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["changes_risk"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False


def test_thresholds_include_symbol_side_regime(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)
    keys = {(row["symbol"], row["side"], row["regime"]) for row in report["threshold_by_symbol_side_regime"]}

    assert ("BTCUSDT", "long", "global") in keys
    assert ("ETHUSDT", "long", "global") in keys or ("ETHUSDT", "short", "global") in keys


def test_cli_no_train_json_executes(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    completed = subprocess.run(
        [sys.executable, "scripts/train_ai_shadow_quality_veto_challenger_v1.py", "--project-root", str(tmp_path), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["training_requested"] is False
    assert payload["veto_runtime_active"] is False


def test_cli_write_report_json_executes(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/train_ai_shadow_quality_veto_challenger_v1.py",
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
    assert (tmp_path / "data" / "reports" / "ai_shadow_quality_veto_trainer_v1.json").exists()


def test_cli_train_json_executes_without_runtime_authority(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/train_ai_shadow_quality_veto_challenger_v1.py",
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

    assert payload["trainer_status"] == "ok"
    assert payload["promotion_eligible"] is False
    assert payload["veto_runtime_active"] is False
    assert payload["registry_write_performed"] is False
    assert payload["sends_orders"] is False


def test_train_blocks_or_executes_research_only(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["trainer_status"] in {"ok", "blocked"}
    if report["trainer_status"] == "blocked":
        assert report["reason"] == "ai_shadow_backend_unavailable"
    assert report["veto_runtime_active"] is False


def test_blocks_train_when_lineage_hash_drift_detected(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    target_path = tmp_path / "data" / "reports" / "financial_label_target_store_v1.json"
    target_store = load_json(target_path)
    target_store["dataset_hash"] = "drift"
    write_json(target_path, target_store)

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["status"] == "blocked"
    assert report["lineage_drift_detected"] is True


def test_blocks_target_columns_in_features(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_feature_columns(tmp_path, "target_net_pnl")

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert any("target_columns_in_features" in error for error in report["validation_errors"])


def test_blocks_label_columns_in_features(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_feature_columns(tmp_path, "label_sign")

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert any("label_columns_in_features" in error or "forbidden_role_columns" in error for error in report["validation_errors"])


def test_blocks_outcome_columns_in_features(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_feature_columns(tmp_path, "net_pnl")

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert any("outcome_or_identifier_columns_in_features" in error or "forbidden_role_columns" in error for error in report["validation_errors"])


def test_blocks_future_ret_columns_in_features(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_feature_columns(tmp_path, "future_ret_1")

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert any("future_ret_columns_in_features" in error for error in report["validation_errors"])


def test_blocks_when_walkforward_leakage_not_ok(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_walkforward(tmp_path, {"leakage_audit": {"leakage_status": "blocked"}})

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert "walkforward_leakage_not_ok" in report["validation_errors"]


def test_blocks_when_embargo_violation_exists(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    mutate_walkforward(tmp_path, {"leakage_audit": {"leakage_status": "ok", "embargo_violation_count": 1}})

    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert "walkforward_embargo_violation_count_nonzero" in report["validation_errors"]


def test_uses_only_feature_contract_columns(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path)
    contract = load_json(tmp_path / "data" / "reports" / "ai_unified_feature_contract_v1.json")

    assert report["feature_columns"] == contract["feature_columns"]


def test_respects_walkforward_split_indices(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)
    walkforward = load_json(tmp_path / "data" / "reports" / "walkforward_anti_leakage_split_engine_v1.json")

    assert [item["split_id"] for item in report["metrics_by_split"]] == [item["split_id"] for item in walkforward["splits"]]


def test_thresholds_are_research_only(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert all(row["research_only"] is True for row in report["threshold_by_symbol_side_regime"])
    assert all(row["veto_runtime_active"] is False for row in report["threshold_by_symbol_side_regime"])


def test_veto_runtime_not_active(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["veto_runtime_active"] is False


def test_ai_shadow_runtime_not_updated(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["ai_shadow_runtime_updated"] is False


def test_probability_quality_reported_when_trained(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["probability_output"] == "probability_quality"
    assert all("probability_quality" in row for row in report["decision_sample"])


def test_ai_accept_reject_decisions_reported_when_trained(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert {row["ai_shadow_candidate_decision"] for row in report["decision_sample"]} <= {"AI_ACCEPT", "AI_REJECT"}


def test_threshold_by_symbol_side_regime_reported(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["threshold_scope"] == "symbol_side_regime"
    assert report["threshold_by_symbol_side_regime"]


def test_metrics_are_reported_by_split(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["metrics_by_split"]
    assert "precision_reject" in report["metrics_by_split"][0]


def test_aggregate_metrics_are_reported(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["aggregate_metrics"]["split_count"] == report["evaluated_split_count"]


def test_baseline_comparison_reported(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert "always_accept_expected_value" in report["baseline_comparison"]


def test_candidate_decision_never_promote(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert "PROMOTE" not in report["candidate_decision"]
    assert report["candidate_decision"] in {"MANTER_EM_RESEARCH", "RESEARCH_CHALLENGER_ONLY"}
    assert report["promotion_eligible"] is False


def test_registry_write_blocked(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, registry_write_requested=True)

    assert report["status"] == "blocked"
    assert report["registry_write_performed"] is False


def test_model_promotion_blocked(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, model_promotion_requested=True)

    assert report["status"] == "blocked"
    assert report["model_promotion_performed"] is False


def test_active_model_not_changed(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["active_model_changed"] is False


def test_challenger_artifact_requires_train(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, write_challenger_artifact=True)

    assert report["status"] == "blocked"
    assert "challenger_artifact_requires_train" in report["validation_errors"]


def test_challenger_artifact_written_only_to_challengers_path(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True, write_challenger_artifact=True)

    assert report["write_challenger_artifact_performed"] is True
    assert "data\\models\\challengers" in report["artifact_paths"]["metadata"] or "data/models/challengers" in report["artifact_paths"]["metadata"]
    assert "champion" not in report["artifact_paths"]["metadata"]


def test_no_qib_runtime_update(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path, train=True)

    assert report["qlib_runtime_updated"] is False


def test_no_exchange_private_access(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path)

    assert report["exchange_private_access"] is False


def test_no_orders_sent(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path)

    assert report["sends_orders"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False


def test_safety_flags_preserved(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    report = build_ai_shadow_quality_veto_trainer_report(project_root=tmp_path)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["changes_risk"] is False


def test_cli_train_research_mode_executes_or_blocks_cleanly_when_backend_unavailable(tmp_path: Path) -> None:
    write_project_inputs(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/train_ai_shadow_quality_veto_challenger_v1.py",
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
