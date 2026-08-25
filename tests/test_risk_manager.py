from pathlib import Path

import pytest

from smartcrypto.risk.risk_manager import RiskLimits, RiskManager


def write_risk_yaml(path: Path, extra: str = "") -> Path:
    path.write_text(
        f"""
runtime_mode: paper
max_position_usdt: 50
max_leverage: 2
signal_ttl_seconds: 300
kill_switch_enabled: false
allowed_pairs:
  - BTC/USDT:USDT
  - ETH/USDT:USDT
{extra}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def test_approves_long_signal() -> None:
    manager = RiskManager(
        RiskLimits(
            runtime_mode="paper",
            max_position_usdt=50,
            max_leverage=2,
            signal_ttl_seconds=300,
            kill_switch_enabled=False,
            allowed_pairs=("BTC/USDT:USDT",),
        )
    )

    decision = manager.approve(
        {
            "pair": "BTC/USDT:USDT",
            "side": "long",
            "proposed_side": "long",
            "score": -0.9,
        }
    )

    assert decision.approved
    assert decision.signal["side"] == "long"


def test_from_yaml_loads_valid_paper_config(tmp_path: Path) -> None:
    manager = RiskManager.from_yaml(write_risk_yaml(tmp_path / "risk_limits.yml"))

    assert manager.limits.runtime_mode == "paper"
    assert manager.limits.max_position_usdt == 50.0
    assert manager.limits.allowed_pairs == ("BTC/USDT:USDT", "ETH/USDT:USDT")
    decision = manager.approve(
        {
            "pair": "BTC/USDT:USDT",
            "side": "long",
            "proposed_side": "long",
        }
    )
    assert decision.approved


def test_from_yaml_fails_for_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing_risk_limits.yml"

    with pytest.raises(FileNotFoundError, match="RiskManager config file not found"):
        RiskManager.from_yaml(missing)


def test_from_yaml_blocks_live_enabled_true(tmp_path: Path) -> None:
    path = write_risk_yaml(
        tmp_path / "risk_limits.yml",
        """
safety:
  live_enabled: true
""",
    )

    with pytest.raises(ValueError, match="live_enabled_must_be_false"):
        RiskManager.from_yaml(path)


def test_from_yaml_blocks_order_submission_enabled_true(tmp_path: Path) -> None:
    path = write_risk_yaml(
        tmp_path / "risk_limits.yml",
        """
safety:
  order_submission_enabled: true
""",
    )

    with pytest.raises(ValueError, match="order_submission_enabled_must_be_false"):
        RiskManager.from_yaml(path)


def test_from_yaml_blocks_real_order_submission_enabled_true(tmp_path: Path) -> None:
    path = write_risk_yaml(
        tmp_path / "risk_limits.yml",
        """
safety:
  real_order_submission_enabled: true
""",
    )

    with pytest.raises(ValueError, match="real_order_submission_enabled_must_be_false"):
        RiskManager.from_yaml(path)


def test_from_yaml_blocks_live_runtime_mode(tmp_path: Path) -> None:
    path = tmp_path / "risk_limits.yml"
    path.write_text(
        """
runtime_mode: live
max_position_usdt: 50
allowed_pairs:
  - BTC/USDT:USDT
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime_mode_not_allowed:live"):
        RiskManager.from_yaml(path)


def test_bot_caller_compatibility_from_yaml_and_approve_many(tmp_path: Path) -> None:
    manager = RiskManager.from_yaml(write_risk_yaml(tmp_path / "risk_limits.yml"))

    decisions = manager.approve_many(
        [
            {
                "pair": "BTC/USDT:USDT",
                "side": "long",
                "proposed_side": "long",
                "model_version": "unit",
            },
            {
                "pair": "ETH/USDT:USDT",
                "side": "short",
                "proposed_side": "short",
                "model_version": "unit",
            },
        ]
    )

    assert [decision.approved for decision in decisions] == [True, True]
    assert [decision.signal["side"] for decision in decisions] == ["long", "short"]
