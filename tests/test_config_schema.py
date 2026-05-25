from __future__ import annotations

from pathlib import Path

import pytest

from smartcrypto.config.schema import (
    ConfigValidationError,
    SafeConfig,
    validate_config,
    validate_config_file,
)


def valid_config() -> dict:
    return {
        "runtime_mode": "paper",
        "safety": {
            "live_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "allow_ai_to_increase_size": False,
            "allow_dashboard_direct_order": False,
        },
        "risk_limits": {
            "max_drawdown_pct": 5.0,
            "max_data_age_seconds": 300,
            "max_spread_bps": 25.0,
            "max_order_notional": 50.0,
            "max_capital_global": 500.0,
        },
    }


def test_valid_config_passes_and_defaults_dangerous_flags_false() -> None:
    config = valid_config()
    config["safety"] = {}

    validated = validate_config(config)

    assert isinstance(validated, SafeConfig)
    assert validated.runtime_mode == "paper"
    assert validated.live_enabled is False
    assert validated.order_submission_enabled is False
    assert validated.real_order_submission_enabled is False
    assert validated.allow_ai_to_increase_size is False
    assert validated.allow_dashboard_direct_order is False


def test_blocks_runtime_mode_live() -> None:
    config = valid_config()
    config["runtime_mode"] = "live"

    with pytest.raises(ConfigValidationError, match="runtime_mode_not_allowed:live"):
        validate_config(config)


def test_blocks_live_and_order_submission_flags() -> None:
    config = valid_config()
    config["safety"]["live_enabled"] = True
    config["safety"]["order_submission_enabled"] = True
    config["safety"]["real_order_submission_enabled"] = True

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(config)

    message = str(exc_info.value)
    assert "live_enabled_must_be_false" in message
    assert "order_submission_enabled_must_be_false" in message
    assert "real_order_submission_enabled_must_be_false" in message


def test_blocks_ai_size_and_dashboard_direct_order() -> None:
    config = valid_config()
    config["safety"]["allow_ai_to_increase_size"] = True
    config["safety"]["allow_dashboard_direct_order"] = True

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(config)

    message = str(exc_info.value)
    assert "allow_ai_to_increase_size_must_be_false" in message
    assert "allow_dashboard_direct_order_must_be_false" in message


def test_requires_all_institutional_limits() -> None:
    config = valid_config()
    del config["risk_limits"]["max_drawdown_pct"]
    del config["risk_limits"]["max_data_age_seconds"]
    del config["risk_limits"]["max_spread_bps"]
    del config["risk_limits"]["max_order_notional"]
    del config["risk_limits"]["max_capital_global"]

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_config(config)

    message = str(exc_info.value)
    assert "missing_required_limit:max_drawdown_pct" in message
    assert "missing_required_limit:max_data_age_seconds" in message
    assert "missing_required_limit:max_spread_bps" in message
    assert "missing_required_limit:max_order_notional" in message
    assert "missing_required_limit:max_capital_global" in message


def test_invalid_numeric_limit_fails_explicitly() -> None:
    config = valid_config()
    config["risk_limits"]["max_spread_bps"] = 0

    with pytest.raises(ConfigValidationError, match="max_spread_bps_must_be_positive_number"):
        validate_config(config)


def test_example_configs_are_safe() -> None:
    paper = validate_config_file("config/paper.example.yml")
    live = validate_config_file("config/live.example.yml")

    assert paper.runtime_mode == "paper"
    assert live.runtime_mode == "shadow"
    assert not paper.live_enabled
    assert not live.live_enabled


def test_loads_yaml_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text(
        """
runtime_mode: research
risk_limits:
  max_drawdown_pct: 4
  max_data_age_seconds: 60
  max_spread_bps: 12
  max_order_notional: 20
  max_capital_global: 200
""".strip(),
        encoding="utf-8",
    )

    validated = validate_config_file(path)

    assert validated.runtime_mode == "research"
    assert validated.order_submission_enabled is False
