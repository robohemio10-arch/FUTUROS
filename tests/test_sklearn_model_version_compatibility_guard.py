from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import sklearn

from scripts import run_sklearn_model_compatibility_guard as guard_cli
from smartcrypto.ml.sklearn_compatibility_guard import run_sklearn_model_compatibility_guard

NOW = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def write_model(path: Path, content: str = "model-bytes") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def metadata(version: str | None = None, **extra) -> dict:
    payload = {
        "model_id": "shadow_model",
        "model_version": "v1",
        "feature_columns": ["feature_ret_1"],
        "model_format_version": "shadow_joblib_v1",
        "python_version": "3.12",
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "auto_promote": False,
        "promotion_allowed": False,
    }
    if version is not None:
        payload["trained_sklearn_version"] = version
    payload.update(extra)
    return payload


def run_guard(tmp_path: Path, *, version: str | None = "1.7.0", runtime: str | None = "1.7.0", strict: bool = False, **extra) -> dict:
    model = write_model(tmp_path / "model.joblib")
    meta = write_json(tmp_path / "model.metadata.json", metadata(version, **extra))
    return run_sklearn_model_compatibility_guard(
        model_path=model,
        metadata_path=meta,
        report_path=tmp_path / "report.json",
        strict=strict,
        runtime_sklearn_version=runtime,
        now=NOW,
    )


def test_sklearn_guard_accepts_matching_runtime_and_model_version(tmp_path: Path) -> None:
    report = run_guard(tmp_path, version="1.7.0", runtime="1.7.0")
    assert report["status"] == "ok"
    assert report["runtime_sklearn_version"] == "1.7.0"
    assert report["model_declared_sklearn_version"] == "1.7.0"
    assert report["promotion_allowed"] is False
    assert report["auto_promote"] is False


def test_sklearn_guard_warns_on_patch_mismatch_non_strict(tmp_path: Path) -> None:
    report = run_guard(tmp_path, version="1.7.0", runtime="1.7.1", strict=False)
    assert report["status"] == "warning"
    assert "sklearn_patch_version_mismatch" in report["warnings"]


def test_sklearn_guard_blocks_major_minor_mismatch(tmp_path: Path) -> None:
    report = run_guard(tmp_path, version="1.8.0", runtime="1.7.0")
    assert report["status"] == "blocked"
    assert "sklearn_major_minor_mismatch" in report["blocking_findings"]


def test_sklearn_guard_blocks_missing_model_version_in_strict_mode(tmp_path: Path) -> None:
    report = run_guard(tmp_path, version=None, runtime="1.7.0", strict=True)
    assert report["status"] == "blocked"
    assert "missing_model_sklearn_version" in report["blocking_findings"]


def test_sklearn_guard_blocks_missing_runtime_version(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("smartcrypto.ml.sklearn_compatibility_guard.get_runtime_sklearn_version", lambda: None)
    model = write_model(tmp_path / "model.joblib")
    meta = write_json(tmp_path / "model.metadata.json", metadata("1.7.0"))
    report = run_sklearn_model_compatibility_guard(model_path=model, metadata_path=meta, report_path=tmp_path / "report.json", now=NOW)
    assert report["status"] == "blocked"
    assert "missing_runtime_sklearn_version" in report["blocking_findings"]


def test_sklearn_guard_blocks_future_model_version(tmp_path: Path) -> None:
    report = run_guard(tmp_path, version="1.7.9", runtime="1.7.0")
    assert report["status"] == "blocked"
    assert "model_sklearn_version_future" in report["blocking_findings"]


def test_sklearn_guard_reads_registry_metadata(tmp_path: Path) -> None:
    registry = write_json(
        tmp_path / "model_registry.json",
        {
            "champion_model_id": "champion",
            "champion_model_version": "v0",
            "challengers": [{"model_id": "shadow_model", "model_version": "v1", "metadata": metadata("1.7.0")}],
        },
    )
    report = run_sklearn_model_compatibility_guard(registry_path=registry, report_path=tmp_path / "report.json", runtime_sklearn_version="1.7.0", now=NOW)
    assert report["status"] == "ok"
    assert report["registry_declared_sklearn_version"] == "1.7.0"


def test_sklearn_guard_reads_trainer_report_metadata(tmp_path: Path) -> None:
    trainer = write_json(tmp_path / "trainer_report.json", metadata("1.7.0"))
    report = run_sklearn_model_compatibility_guard(trainer_report_path=trainer, report_path=tmp_path / "report.json", runtime_sklearn_version="1.7.0", now=NOW)
    assert report["status"] == "ok"
    assert report["trainer_declared_sklearn_version"] == "1.7.0"


def test_sklearn_guard_detects_warning_in_logs(tmp_path: Path) -> None:
    logs = tmp_path / "runtime.log"
    logs.write_text("InconsistentVersionWarning: sklearn_version_mismatch 1.8.0 vs 1.7.0", encoding="utf-8")
    report = run_sklearn_model_compatibility_guard(logs_path=logs, report_path=tmp_path / "report.json", runtime_sklearn_version="1.7.0", now=NOW)
    assert report["status"] in {"warning", "missing_metadata"}
    assert report["log_warnings"]
    assert any("sklearn_warning_detected" in item for item in report["warnings"])


def test_sklearn_guard_calculates_model_and_metadata_hash(tmp_path: Path) -> None:
    model = write_model(tmp_path / "model.joblib", "hash-me")
    meta = write_json(tmp_path / "model.metadata.json", metadata("1.7.0"))
    report = run_sklearn_model_compatibility_guard(model_path=model, metadata_path=meta, report_path=tmp_path / "report.json", runtime_sklearn_version="1.7.0", now=NOW)
    assert report["model_hash"]
    assert report["metadata_hash"]
    assert len(report["model_hash"]) == 64
    assert len(report["metadata_hash"]) == 64


def test_sklearn_guard_never_allows_auto_promotion(tmp_path: Path) -> None:
    report = run_guard(tmp_path, version="1.7.0", runtime="1.7.0", auto_promote=True)
    assert report["status"] == "blocked"
    assert report["auto_promote"] is False
    assert "unsafe_safety_flag:auto_promote" in report["blocking_findings"]


def test_sklearn_guard_blocks_unsafe_safety_flags(tmp_path: Path) -> None:
    report = run_guard(
        tmp_path,
        version="1.7.0",
        runtime="1.7.0",
        live_trading_enabled=True,
        order_submission_enabled=True,
        real_order_submission_enabled=True,
        exchange_private_access=True,
        sends_orders=True,
        changes_risk=True,
    )
    assert report["status"] == "blocked"
    assert "unsafe_safety_flag:live_trading_enabled" in report["blocking_findings"]
    assert "unsafe_safety_flag:order_submission_enabled" in report["blocking_findings"]
    assert "unsafe_safety_flag:real_order_submission_enabled" in report["blocking_findings"]
    assert "unsafe_safety_flag:exchange_private_access" in report["blocking_findings"]
    assert "unsafe_safety_flag:sends_orders" in report["blocking_findings"]
    assert "unsafe_safety_flag:changes_risk" in report["blocking_findings"]


def test_cli_run_sklearn_model_compatibility_guard_runs_successfully(tmp_path: Path, capsys) -> None:
    model = write_model(tmp_path / "model.joblib")
    meta = write_json(tmp_path / "model.metadata.json", metadata(sklearn.__version__))
    rc = guard_cli.main(["--model", str(model), "--metadata", str(meta), "--report", str(tmp_path / "report.json")])
    output = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert output["status"] == "ok"
    assert output["runtime_sklearn_version"] == sklearn.__version__


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    protected = [
        tmp_path / "data" / "features" / "training_dataset.parquet",
        tmp_path / "data" / "trades" / "trades_master.xlsx",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel", encoding="utf-8")
    run_guard(tmp_path, version="1.7.0", runtime="1.7.0")
    assert all(path.read_text(encoding="utf-8") == "sentinel" for path in protected)


def test_does_not_touch_freqtrade_db_registry_models_signal_producer_or_config(tmp_path: Path) -> None:
    protected = [
        tmp_path / "freqtrade" / "user_data" / "tradesv3.paper.sqlite",
        tmp_path / "data" / "models" / "registry" / "model_registry.json",
        tmp_path / "data" / "models" / "shadow" / "model.joblib",
        tmp_path / "data" / "runtime" / "active_freqtrade_signals.json",
        tmp_path / ".env",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel", encoding="utf-8")
    run_sklearn_model_compatibility_guard(report_path=tmp_path / "guard.json", runtime_sklearn_version="1.7.0", now=NOW)
    assert all(path.read_text(encoding="utf-8") == "sentinel" for path in protected)


def test_never_sends_orders_or_accesses_exchange() -> None:
    checked = [
        Path("smartcrypto/ml/sklearn_compatibility_guard.py"),
        Path("scripts/run_sklearn_model_compatibility_guard.py"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in checked)
    forbidden = ["create_order", "fetch_balance", "private_get", "freqtradeapi", "ccxt.", "requests.post"]
    assert not any(token in combined for token in forbidden)
