from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

from smartcrypto.qlib_engine.paper_refresh_supervisor import (
    BLOCKED,
    MARKET_FEATURES_FAILED,
    OK,
    PHASE13_FAILED,
    PREDICTIONS_FAILED,
    STALE_AFTER_REFRESH,
    PaperRefreshSupervisorConfig,
    run_paper_refresh_supervisor,
)


MODULE_PATH = Path("scripts/run_qlib_paper_refresh_supervisor.py")
COMPOSE_PATH = Path("docker-compose.paper.yml")


def compose_command(service: dict[str, object]) -> list[str]:
    value = service["command"]
    if isinstance(value, list):
        return [str(item) for item in value]
    return str(value).split()


def config(tmp_path: Path) -> PaperRefreshSupervisorConfig:
    return PaperRefreshSupervisorConfig(
        report_path=tmp_path / "supervisor.json",
        market_source_path=tmp_path / "raw.parquet",
        existing_market_features_path=tmp_path / "features.parquet",
        market_features_output_path=tmp_path / "features.parquet",
        market_features_report_path=tmp_path / "market_report.json",
        qlib_model_path=tmp_path / "model.joblib",
        qlib_model_config_path=tmp_path / "qlib_model.yml",
        predictions_output_path=tmp_path / "predictions.parquet",
        predictions_report_path=tmp_path / "prediction_report.json",
        signal_config_path=tmp_path / "signal_producer.yml",
        pinned_signals_path=tmp_path / "active_freqtrade_signals.json",
        next_recommended_run_seconds=123,
        public_download_enabled=False,
    )


def market_ok(**kwargs):
    return {"status": "ok", "rows": 10, "market_features_age_minutes": 1.0}


def predictions_ok(**kwargs):
    return {
        "status": "ok",
        "rows": 2,
        "input_data_status": "input_data_fresh",
        "prediction_freshness": freshness_ok(),
    }


def phase13_ok(**kwargs):
    return {"status": "ok", "signals_after": 2, "written_pinned": True}


def freshness_ok(*args, **kwargs):
    return {
        "freshness_status": "fresh",
        "stale": False,
        "input_data_status": "input_data_fresh",
        "reason": None,
        "rows": 2,
    }


def signals_after(path):
    return {"path": str(path), "exists": True, "signal_count": 2, "active_signal_count": 2}


def signal_permission_ok(path, *, required: bool):
    assert required is True
    return {
        "status": "ok",
        "reason": "shared_signal_permission_contract_established",
        "path": str(path),
        "consumer_readable": True,
    }


def load_cli_module():
    spec = importlib.util.spec_from_file_location("run_qlib_paper_refresh_supervisor", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def compose_payload() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_supervisor_ok_report_contains_required_fields(tmp_path: Path) -> None:
    report = run_paper_refresh_supervisor(
        config(tmp_path),
        market_refresh_fn=market_ok,
        prediction_refresh_fn=predictions_ok,
        phase13_fn=phase13_ok,
        freshness_fn=freshness_ok,
        signal_inspect_fn=signals_after,
        signal_permission_fn=signal_permission_ok,
    )

    assert report["status"] == OK
    assert report["market_features_status"] == "ok"
    assert report["predictions_status"] == "ok"
    assert report["phase13_status"] == "ok"
    assert report["input_data_status"] == "input_data_fresh"
    assert report["prediction_freshness"]["freshness_status"] == "fresh"
    assert report["signals_after"]["active_signal_count"] == 2
    assert report["signal_permission_contract"]["consumer_readable"] is True
    assert report["next_recommended_run_seconds"] == 123
    assert json.loads((tmp_path / "supervisor.json").read_text(encoding="utf-8"))["status"] == OK


def test_market_features_failure_short_circuits(tmp_path: Path) -> None:
    calls = {"predictions": 0}

    def market_failed(**kwargs):
        return {"status": "blocked", "reason": "missing_source"}

    def predictions_called(**kwargs):
        calls["predictions"] += 1
        return predictions_ok()

    report = run_paper_refresh_supervisor(
        config(tmp_path),
        market_refresh_fn=market_failed,
        prediction_refresh_fn=predictions_called,
        phase13_fn=phase13_ok,
        freshness_fn=freshness_ok,
        signal_inspect_fn=signals_after,
    )

    assert report["status"] == MARKET_FEATURES_FAILED
    assert report["reason"] == "missing_source"
    assert calls["predictions"] == 0


def test_predictions_failure_short_circuits_phase13(tmp_path: Path) -> None:
    calls = {"phase13": 0}

    def predictions_failed(**kwargs):
        return {"status": "blocked", "reason": "model_missing"}

    def phase13_called(**kwargs):
        calls["phase13"] += 1
        return phase13_ok()

    report = run_paper_refresh_supervisor(
        config(tmp_path),
        market_refresh_fn=market_ok,
        prediction_refresh_fn=predictions_failed,
        phase13_fn=phase13_called,
        freshness_fn=freshness_ok,
        signal_inspect_fn=signals_after,
    )

    assert report["status"] == PREDICTIONS_FAILED
    assert report["reason"] == "model_missing"
    assert calls["phase13"] == 0


def test_phase13_failure_is_controlled(tmp_path: Path) -> None:
    def phase13_failed(**kwargs):
        return {"status": "blocked", "reason": "qlib_predictions_stale", "signals_after": 0}

    report = run_paper_refresh_supervisor(
        config(tmp_path),
        market_refresh_fn=market_ok,
        prediction_refresh_fn=predictions_ok,
        phase13_fn=phase13_failed,
        freshness_fn=freshness_ok,
        signal_inspect_fn=signals_after,
    )

    assert report["status"] == PHASE13_FAILED
    assert report["reason"] == "qlib_predictions_stale"


def test_stale_after_refresh_is_reported(tmp_path: Path) -> None:
    def stale(*args, **kwargs):
        return {
            "freshness_status": "stale",
            "stale": True,
            "input_data_status": "input_data_stale",
            "reason": "qlib_predictions_stale",
        }

    report = run_paper_refresh_supervisor(
        config(tmp_path),
        market_refresh_fn=market_ok,
        prediction_refresh_fn=predictions_ok,
        phase13_fn=phase13_ok,
        freshness_fn=stale,
        signal_inspect_fn=signals_after,
        signal_permission_fn=signal_permission_ok,
    )

    assert report["status"] == STALE_AFTER_REFRESH
    assert report["reason"] == "qlib_predictions_stale"


def test_runtime_flags_block_before_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORDER_SUBMISSION_ENABLED", "true")
    calls = {"market": 0}

    def market_called(**kwargs):
        calls["market"] += 1
        return market_ok()

    report = run_paper_refresh_supervisor(
        config(tmp_path),
        market_refresh_fn=market_called,
        prediction_refresh_fn=predictions_ok,
        phase13_fn=phase13_ok,
        freshness_fn=freshness_ok,
        signal_inspect_fn=signals_after,
    )

    assert report["status"] == BLOCKED
    assert "ORDER_SUBMISSION_ENABLED=true" in report["reason"]
    assert calls["market"] == 0


def test_cli_once_default_uses_single_cycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    module = load_cli_module()

    def fake_run(cfg):
        return {
            "status": "ok",
            "report_path": str(cfg.report_path),
            "runtime_mode": "paper",
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
        }

    monkeypatch.setattr(module, "run_paper_refresh_supervisor", fake_run)
    rc = module.main(["--report", str(tmp_path / "report.json")])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["report_path"] == str(tmp_path / "report.json")


def test_supervisor_does_not_reference_private_exchange_or_orders() -> None:
    text = "\n".join(
        [
            Path("smartcrypto/qlib_engine/paper_refresh_supervisor.py").read_text(encoding="utf-8"),
            Path("scripts/run_qlib_paper_refresh_supervisor.py").read_text(encoding="utf-8"),
        ]
    )
    forbidden = [
        "create_order(",
        "fetch_balance(",
        "ccxt.",
        "Freqtrade API",
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
        ".env",
    ]
    assert all(token not in text for token in forbidden)


def test_compose_contains_runtime_supervisor_service() -> None:
    service = compose_payload()["services"]["qlib-refresh-supervisor-paper"]

    argv = compose_command(service)
    separator = argv.index("--")
    assert argv[separator + 1 :] == [
        "python",
        "scripts/run_qlib_paper_refresh_supervisor.py",
        "--interval-seconds",
        "300",
    ]
