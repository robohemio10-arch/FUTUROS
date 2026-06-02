from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from smartcrypto.ml.model_registry import register_ai_shadow_challenger_model


ROOT = Path(__file__).resolve().parents[1]


def load_cli_module():
    path = ROOT / "scripts" / "register_ai_shadow_challenger_model.py"
    spec = importlib.util.spec_from_file_location("register_ai_shadow_challenger_model", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def trainer_report_payload(**overrides) -> dict:
    payload = {
        "status": "ok",
        "reason": "ok",
        "model_id": "ai_shadow_incremental_logistic_regression",
        "model_version": "shadow_incremental_v1",
        "trained_at_utc": "2026-06-02T20:00:00+00:00",
        "input_path": "data/features/incremental_training_microbatch.parquet",
        "input_rows": 120,
        "feature_columns": ["feature_close", "feature_volume", "feature_rsi"],
        "target_column": "target_profitable",
        "class_balance": {"0": 60, "1": 60},
        "metrics": {
            "accuracy": 0.72,
            "precision": 0.70,
            "recall": 0.75,
            "f1": 0.72,
            "roc_auc": 0.74,
            "train_rows": 90,
            "test_rows": 30,
        },
        "model_path": "data/models/shadow/shadow_incremental_v1.joblib",
        "metadata_path": "data/models/shadow/shadow_incremental_v1.metadata.json",
        "promotion_status": "pending",
        "auto_promote": False,
        "sample_warning": False,
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }
    payload.update(overrides)
    return payload


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def register(tmp_path: Path, payload: dict, *, strict: bool = False, **thresholds) -> dict:
    trainer_report = tmp_path / "reports" / "ai_shadow_incremental_trainer_report.json"
    write_report(trainer_report, payload)
    return register_ai_shadow_challenger_model(
        trainer_report_path=trainer_report,
        registry_path=tmp_path / "models" / "registry" / "model_registry.json",
        report_path=tmp_path / "reports" / "model_registry_promotion_gate_report.json",
        strict=strict,
        **thresholds,
    )


def load_registry(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "models" / "registry" / "model_registry.json").read_text(encoding="utf-8"))


def test_registry_blocks_missing_trainer_report(tmp_path: Path) -> None:
    report = register_ai_shadow_challenger_model(
        trainer_report_path=tmp_path / "missing.json",
        registry_path=tmp_path / "models" / "registry" / "model_registry.json",
        report_path=tmp_path / "reports" / "gate.json",
        strict=True,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_trainer_report"
    assert not (tmp_path / "models" / "registry" / "model_registry.json").exists()


def test_registry_registers_challenger_pending(tmp_path: Path) -> None:
    report = register(tmp_path, trainer_report_payload(), min_rows=100, min_accuracy=0.6, min_f1=0.6, min_roc_auc=0.6)
    registry = load_registry(tmp_path)

    assert report["status"] == "ok"
    assert report["promotion_status"] == "pending"
    assert report["promotion_gate_status"] == "eligible_pending_manual_review"
    assert report["auto_promote"] is False
    assert registry["champion_model_id"] is None
    assert registry["champion_model_version"] is None
    assert len(registry["challengers"]) == 1
    assert registry["challengers"][0]["promotion_status"] == "pending"


def test_registry_blocks_auto_promotion(tmp_path: Path) -> None:
    report = register(tmp_path, trainer_report_payload(auto_promote=True), strict=True)

    assert report["status"] == "blocked"
    assert "auto_promotion_forbidden" in report["promotion_violations"]
    assert not (tmp_path / "models" / "registry" / "model_registry.json").exists()


def test_registry_blocks_small_sample_promotion(tmp_path: Path) -> None:
    report = register(tmp_path, trainer_report_payload(input_rows=26, sample_warning=True), min_rows=100)
    registry = load_registry(tmp_path)

    assert report["status"] == "ok"
    assert report["promotion_gate_status"] == "blocked"
    assert "sample_warning_true" in report["promotion_violations"]
    assert "input_rows_below_minimum:26<100" in report["promotion_violations"]
    assert registry["challengers"][0]["promotion_status"] == "pending"
    assert registry["rejected_promotions"][0]["promotion_gate_status"] == "blocked"


def test_registry_blocks_unsafe_safety_flags(tmp_path: Path) -> None:
    report = register(tmp_path, trainer_report_payload(live_trading_enabled=True))

    assert report["status"] == "blocked"
    assert report["reason"] == "invalid_trainer_metadata"
    assert "unsafe_safety_flag:live_trading_enabled=True" in report["promotion_violations"]


def test_registry_blocks_missing_model_identity(tmp_path: Path) -> None:
    report = register(tmp_path, trainer_report_payload(model_id="", model_version=""))

    assert report["status"] == "blocked"
    assert "missing_model_id" in report["promotion_violations"]
    assert "missing_model_version" in report["promotion_violations"]


def test_registry_blocks_missing_features(tmp_path: Path) -> None:
    report = register(tmp_path, trainer_report_payload(feature_columns=[]))

    assert report["status"] == "blocked"
    assert "missing_feature_columns" in report["promotion_violations"]


def test_registry_blocks_low_metrics(tmp_path: Path) -> None:
    low = trainer_report_payload(metrics={**trainer_report_payload()["metrics"], "accuracy": 0.42, "f1": 0.30, "roc_auc": None})

    report = register(tmp_path, low, strict=True, min_accuracy=0.60, min_f1=0.55, min_roc_auc=0.60)

    assert report["status"] == "blocked"
    assert any(item.startswith("accuracy_below_minimum") for item in report["promotion_violations"])
    assert any(item.startswith("f1_below_minimum") for item in report["promotion_violations"])
    assert any(item.startswith("roc_auc_below_minimum") for item in report["promotion_violations"])


def test_registry_blocks_single_target_class(tmp_path: Path) -> None:
    report = register(tmp_path, trainer_report_payload(class_balance={"1": 120}), strict=True)

    assert report["status"] == "blocked"
    assert "single_target_class" in report["promotion_violations"]


def test_registry_preserves_existing_champion(tmp_path: Path) -> None:
    registry_path = tmp_path / "models" / "registry" / "model_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "registry_version": 2,
                "updated_at_utc": "2026-01-01T00:00:00Z",
                "champion_model_id": "current_champion",
                "champion_model_version": "champion_v1",
                "challengers": [],
                "rejected_promotions": [],
                "models": [],
                "paper_only": True,
                "shadow_only": True,
                "runtime_mode": "paper",
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    trainer_report = tmp_path / "reports" / "trainer.json"
    write_report(trainer_report, trainer_report_payload())

    register_ai_shadow_challenger_model(
        trainer_report_path=trainer_report,
        registry_path=registry_path,
        report_path=tmp_path / "reports" / "gate.json",
        min_rows=100,
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    assert registry["champion_model_id"] == "current_champion"
    assert registry["champion_model_version"] == "champion_v1"
    assert len(registry["challengers"]) == 1


def test_cli_registers_challenger(tmp_path: Path, capsys) -> None:
    module = load_cli_module()
    trainer_report = tmp_path / "reports" / "trainer.json"
    registry = tmp_path / "models" / "registry" / "model_registry.json"
    report_path = tmp_path / "reports" / "gate.json"
    write_report(trainer_report, trainer_report_payload())

    exit_code = module.main(
        [
            "--trainer-report",
            str(trainer_report),
            "--registry",
            str(registry),
            "--report",
            str(report_path),
            "--min-rows",
            "100",
            "--min-accuracy",
            "0.60",
            "--min-f1",
            "0.60",
            "--min-roc-auc",
            "0.60",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert registry.exists()
    assert report_path.exists()


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    trainer_report = tmp_path / "reports" / "trainer.json"
    training_dataset = tmp_path / "features" / "training_dataset.parquet"
    trades_master = tmp_path / "trades" / "trades_master.xlsx"
    trades_master.parent.mkdir(parents=True, exist_ok=True)
    trades_master.write_bytes(b"master")
    before = trades_master.read_bytes()
    write_report(trainer_report, trainer_report_payload())

    report = register_ai_shadow_challenger_model(
        trainer_report_path=trainer_report,
        registry_path=tmp_path / "models" / "registry" / "model_registry.json",
        report_path=tmp_path / "reports" / "gate.json",
    )

    assert report["status"] == "ok"
    assert not training_dataset.exists()
    assert trades_master.read_bytes() == before
