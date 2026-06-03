from __future__ import annotations

import json
from pathlib import Path

from scripts import validate_runtime_safety_config as cli
from smartcrypto.config.runtime_safety_config import (
    build_runtime_safety_report,
    load_runtime_config,
    validate_runtime_config,
)


ROOT = Path(__file__).resolve().parents[1]


def safe_config(*, runtime_mode: str = "paper") -> dict:
    return {
        "schema_version": "runtime-safety.v1",
        "config_version": "unit-test.1",
        "runtime_mode": runtime_mode,
        "safety": {
            "dry_run": True,
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "sends_orders": False,
            "changes_risk": False,
            "ai_can_increase_risk": False,
            "ai_can_change_leverage": False,
            "ai_can_change_stake": False,
            "dashboard_can_change_risk": False,
            "dashboard_can_promote_model": False,
            "dashboard_can_enable_live": False,
        },
        "risk_limits": {
            "max_drawdown_pct": 8,
            "max_daily_loss_pct": 3,
            "max_weekly_loss_pct": 9,
            "max_consecutive_losses": 4,
            "max_spread_bps": 50,
            "max_slippage_bps": 25,
            "max_latency_ms": 1000,
            "max_data_age_seconds": 300,
            "stale_prediction_max_age_seconds": 300,
            "kill_switch_enabled": True,
            "max_leverage": 2,
            "stake_pct": 2,
        },
    }


def test_config_validation_accepts_safe_paper_config() -> None:
    report = validate_runtime_config(safe_config(), environment="paper")

    assert report["status"] == "ok"
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False


def test_config_validation_accepts_safe_shadow_config() -> None:
    report = validate_runtime_config(safe_config(runtime_mode="shadow"), environment="shadow")

    assert report["status"] == "ok"
    assert report["runtime_mode"] == "shadow"


def test_config_validation_blocks_live_enabled() -> None:
    config = safe_config()
    config["safety"]["live_trading_enabled"] = True

    report = validate_runtime_config(config, environment="paper")

    assert report["status"] == "blocked"
    assert "live_trading_enabled" in report["unsafe_flags"]
    assert "unsafe_flag:live_trading_enabled=true" in report["blocking_findings"]


def test_config_validation_blocks_order_submission() -> None:
    config = safe_config()
    config["safety"]["order_submission_enabled"] = True
    config["safety"]["real_order_submission_enabled"] = True

    report = validate_runtime_config(config, environment="paper")

    assert report["status"] == "blocked"
    assert "order_submission_enabled" in report["unsafe_flags"]
    assert "real_order_submission_enabled" in report["unsafe_flags"]


def test_config_validation_blocks_private_exchange_access() -> None:
    config = safe_config()
    config["safety"]["exchange_private_access"] = True

    report = validate_runtime_config(config, environment="paper")

    assert report["status"] == "blocked"
    assert "unsafe_flag:exchange_private_access=true" in report["blocking_findings"]


def test_config_validation_blocks_dry_run_false() -> None:
    config = safe_config()
    config["safety"]["dry_run"] = False

    report = validate_runtime_config(config, environment="paper")

    assert report["status"] == "blocked"
    assert "unsafe_flag:dry_run=false" in report["blocking_findings"]


def test_config_validation_blocks_ai_risk_authority() -> None:
    config = safe_config()
    config["safety"]["ai_can_increase_risk"] = True
    config["safety"]["ai_can_change_leverage"] = True
    config["safety"]["ai_can_change_stake"] = True

    report = validate_runtime_config(config, environment="paper")

    assert report["status"] == "blocked"
    assert "ai_can_increase_risk" in report["unsafe_flags"]
    assert "ai_can_change_leverage" in report["unsafe_flags"]
    assert "ai_can_change_stake" in report["unsafe_flags"]


def test_config_validation_blocks_dashboard_risk_authority() -> None:
    config = safe_config()
    config["safety"]["dashboard_can_change_risk"] = True
    config["safety"]["dashboard_can_promote_model"] = True
    config["safety"]["dashboard_can_enable_live"] = True

    report = validate_runtime_config(config, environment="paper")

    assert report["status"] == "blocked"
    assert "dashboard_can_change_risk" in report["unsafe_flags"]
    assert "dashboard_can_promote_model" in report["unsafe_flags"]
    assert "dashboard_can_enable_live" in report["unsafe_flags"]


def test_config_validation_blocks_missing_risk_limits() -> None:
    config = safe_config()
    del config["risk_limits"]["max_drawdown_pct"]
    del config["risk_limits"]["max_daily_loss_pct"]
    del config["risk_limits"]["max_weekly_loss_pct"]
    del config["risk_limits"]["max_consecutive_losses"]
    del config["risk_limits"]["max_spread_bps"]
    del config["risk_limits"]["max_slippage_bps"]
    del config["risk_limits"]["max_latency_ms"]
    del config["risk_limits"]["max_data_age_seconds"]
    del config["risk_limits"]["stale_prediction_max_age_seconds"]
    del config["risk_limits"]["kill_switch_enabled"]

    report = validate_runtime_config(config, environment="paper")

    assert report["status"] == "blocked"
    assert "max_drawdown_pct" in report["missing_required_keys"]
    assert "max_daily_loss_pct" in report["missing_required_keys"]
    assert "max_weekly_loss_pct" in report["missing_required_keys"]
    assert "max_consecutive_losses" in report["missing_required_keys"]
    assert "max_spread_bps" in report["missing_required_keys"]
    assert "max_slippage_bps" in report["missing_required_keys"]
    assert "max_latency_ms" in report["missing_required_keys"]
    assert "max_data_age_seconds" in report["missing_required_keys"]
    assert "stale_prediction_max_age_seconds" in report["missing_required_keys"]
    assert "kill_switch_enabled" in report["missing_required_keys"]


def test_config_validation_blocks_invalid_risk_limits() -> None:
    config = safe_config()
    config["risk_limits"]["max_drawdown_pct"] = 0
    config["risk_limits"]["max_daily_loss_pct"] = -1
    config["risk_limits"]["max_weekly_loss_pct"] = 99
    config["risk_limits"]["max_leverage"] = 20
    config["risk_limits"]["stake_pct"] = 30

    report = validate_runtime_config(config, environment="paper")

    assert report["status"] == "blocked"
    assert "invalid_risk_limit:max_drawdown_pct" in report["risk_limit_findings"]
    assert "invalid_risk_limit:max_daily_loss_pct" in report["risk_limit_findings"]
    assert any(item.startswith("absurdly_permissive_risk_limit:max_weekly_loss_pct") for item in report["risk_limit_findings"])
    assert any(item.startswith("absurdly_permissive_risk_limit:max_leverage") for item in report["risk_limit_findings"])
    assert any(item.startswith("absurdly_permissive_risk_limit:stake_pct") for item in report["risk_limit_findings"])


def test_config_validation_blocks_invalid_runtime_mode() -> None:
    config = safe_config(runtime_mode="live")

    report = validate_runtime_config(config, environment="paper")

    assert report["status"] == "blocked"
    assert "runtime_mode_live_not_allowed" in report["blocking_findings"]
    assert any(item.startswith("runtime_mode_incompatible:live") for item in report["environment_findings"])


def test_config_validation_blocks_missing_schema_or_config_version() -> None:
    config = safe_config()
    del config["schema_version"]
    del config["config_version"]

    report = validate_runtime_config(config, environment="paper")

    assert report["status"] == "blocked"
    assert "schema_version" in report["missing_required_keys"]
    assert "config_version" in report["missing_required_keys"]


def test_config_validation_blocks_unsafe_safety_flags() -> None:
    config = safe_config()
    config["safety"]["paper_only"] = False
    config["safety"]["shadow_only"] = False
    config["safety"]["sends_orders"] = True
    config["safety"]["changes_risk"] = True

    report = validate_runtime_config(config, environment="shadow")

    assert report["status"] == "blocked"
    assert "unsafe_flag:paper_only=false" in report["blocking_findings"]
    assert "unsafe_flag:shadow_only=false" in report["blocking_findings"]
    assert "sends_orders" in report["unsafe_flags"]
    assert "changes_risk" in report["unsafe_flags"]


def test_config_validation_reports_warnings_in_non_strict_mode() -> None:
    config = safe_config()
    config["risk_limits"]["max_drawdown_pct"] = 15

    report = validate_runtime_config(config, environment="paper", strict=False)
    strict_report = validate_runtime_config(config, environment="paper", strict=True)

    assert report["status"] == "warning"
    assert report["blocking_findings"] == []
    assert any(item.startswith("permissive_risk_limit:max_drawdown_pct") for item in report["warnings"])
    assert strict_report["status"] == "blocked"
    assert any(item.startswith("strict_warning:permissive_risk_limit:max_drawdown_pct") for item in strict_report["blocking_findings"])


def test_cli_validate_runtime_safety_config_runs_successfully(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "runtime.yml"
    report_path = tmp_path / "reports" / "runtime_safety.json"
    config_path.write_text(
        """
schema_version: runtime-safety.v1
config_version: cli-test.1
runtime_mode: paper
safety:
  dry_run: true
  paper_only: true
  shadow_only: true
  live_trading_enabled: false
  order_submission_enabled: false
  real_order_submission_enabled: false
  exchange_private_access: false
risk_limits:
  max_drawdown_pct: 8
  max_daily_loss_pct: 3
  max_weekly_loss_pct: 9
  max_consecutive_losses: 4
  max_spread_bps: 50
  max_slippage_bps: 25
  max_latency_ms: 1000
  max_data_age_seconds: 300
  stale_prediction_max_age_seconds: 300
  kill_switch_enabled: true
""".strip(),
        encoding="utf-8",
    )

    rc = cli.main([
        "--config",
        str(config_path),
        "--environment",
        "paper",
        "--report",
        str(report_path),
    ])
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)

    assert rc == 0
    assert payload["status"] == "ok"
    assert payload["config_path"] == str(config_path)
    assert report_path.exists()
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "ok"
    assert load_runtime_config(config_path)["runtime_mode"] == "paper"


def test_does_not_touch_env_docker_or_runtime_files(tmp_path: Path) -> None:
    sentinels = {
        tmp_path / ".env": "env-stays",
        tmp_path / "Dockerfile": "docker-stays",
        tmp_path / "docker-compose.paper.yml": "compose-stays",
        tmp_path / "data" / "runtime" / "active_freqtrade_signals.json": "signals-stay",
    }
    for path, content in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    report = build_runtime_safety_report(
        config=safe_config(),
        environment="paper",
        report_path=tmp_path / "reports" / "runtime_safety.json",
    )

    assert report["status"] == "ok"
    for path, content in sentinels.items():
        assert path.read_text(encoding="utf-8") == content

    module_text = (ROOT / "smartcrypto" / "config" / "runtime_safety_config.py").read_text(encoding="utf-8").lower()
    assert "docker" not in module_text
    assert ".env" not in module_text


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    training_dataset = tmp_path / "data" / "features" / "training_dataset.parquet"
    trades_master = tmp_path / "data" / "trades" / "trades_master.xlsx"
    training_dataset.parent.mkdir(parents=True, exist_ok=True)
    trades_master.parent.mkdir(parents=True, exist_ok=True)
    training_dataset.write_text("training-stays", encoding="utf-8")
    trades_master.write_text("master-stays", encoding="utf-8")

    report = build_runtime_safety_report(
        config=safe_config(),
        environment="paper",
        report_path=tmp_path / "reports" / "runtime_safety.json",
    )

    assert report["status"] == "ok"
    assert training_dataset.read_text(encoding="utf-8") == "training-stays"
    assert trades_master.read_text(encoding="utf-8") == "master-stays"


def test_does_not_touch_registry_models_signal_producer_risk_manager_or_freqtrade(tmp_path: Path) -> None:
    protected = {
        tmp_path / "data" / "models" / "registry" / "model_registry.json": "registry-stays",
        tmp_path / "data" / "models" / "shadow" / "model.joblib": "model-stays",
        tmp_path / "scripts" / "phase13_generate_active_signals.py": "signal-producer-stays",
        tmp_path / "smartcrypto" / "risk" / "risk_manager.py": "risk-manager-stays",
        tmp_path / "freqtrade" / "user_data" / "tradesv3.paper.sqlite": "db-stays",
    }
    for path, content in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    report = build_runtime_safety_report(
        config=safe_config(),
        environment="paper",
        report_path=tmp_path / "reports" / "runtime_safety.json",
    )

    assert report["status"] == "ok"
    for path, content in protected.items():
        assert path.read_text(encoding="utf-8") == content

    script_text = (ROOT / "scripts" / "validate_runtime_safety_config.py").read_text(encoding="utf-8").lower()
    module_text = (ROOT / "smartcrypto" / "config" / "runtime_safety_config.py").read_text(encoding="utf-8").lower()
    combined = script_text + module_text
    for forbidden in ("ccxt", "freqtradeapi", "create_order", "fetch_balance"):
        assert forbidden not in combined
