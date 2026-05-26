from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from smartcrypto.risk.paper_risk_controller import (
    PaperRiskPolicy,
    SafetyViolation,
    assert_environment_safe,
    run_paper_risk_controller,
)


def write_csv_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["open_time_utc", "symbol", "side", "reported_pnl_usdt"],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_config(path: Path, input_path: Path, output_root: Path) -> None:
    path.write_text(
        f"""
runtime_mode: paper
inputs:
  default_paths:
    - {input_path.as_posix()}
policy:
  name: btc_075_eth_100_daily_stop_25
  multipliers:
    BTCUSDT: 0.75
    ETHUSDT: 1.00
  default_multiplier: 1.00
  daily_emergency_stop_usdt: -25.0
  cooldown_enabled: false
  order_submission_enabled: false
  real_order_submission_enabled: false
safety:
  order_submission_enabled: false
  real_order_submission_enabled: false
outputs:
  daily_summary_json: {(output_root / "daily_summary.json").as_posix()}
  daily_trades_csv: {(output_root / "daily_trades.csv").as_posix()}
  equity_csv: {(output_root / "equity.csv").as_posix()}
  state_json: {(output_root / "state.json").as_posix()}
""".strip(),
        encoding="utf-8",
    )


def test_applies_symbol_multipliers_without_order_submission(tmp_path: Path) -> None:
    trades_path = tmp_path / "trades.csv"
    config_path = tmp_path / "paper_risk.yml"
    output_root = tmp_path / "reports"
    write_csv_rows(
        trades_path,
        [
            {
                "open_time_utc": "2026-05-25T10:00:00+00:00",
                "symbol": "BTC/USDT:USDT",
                "side": "long",
                "reported_pnl_usdt": "10",
            },
            {
                "open_time_utc": "2026-05-25T11:00:00+00:00",
                "symbol": "ETHUSDT",
                "side": "short",
                "reported_pnl_usdt": "10",
            },
        ],
    )
    write_config(config_path, trades_path, output_root)

    result = run_paper_risk_controller(config_path=config_path)

    assert result.raw_net_pnl_usdt == 20.0
    assert result.paper_net_pnl_usdt == 17.5
    assert result.accepted_trades == 2
    assert result.skipped_trades == 0
    assert result.policy["order_submission_enabled"] is False
    assert result.policy["real_order_submission_enabled"] is False


def test_daily_emergency_stop_blocks_following_trades(tmp_path: Path) -> None:
    trades_path = tmp_path / "trades.csv"
    config_path = tmp_path / "paper_risk.yml"
    output_root = tmp_path / "reports"
    write_csv_rows(
        trades_path,
        [
            {
                "open_time_utc": "2026-05-25T10:00:00+00:00",
                "symbol": "ETHUSDT",
                "side": "long",
                "reported_pnl_usdt": "-30",
            },
            {
                "open_time_utc": "2026-05-25T11:00:00+00:00",
                "symbol": "ETHUSDT",
                "side": "long",
                "reported_pnl_usdt": "100",
            },
        ],
    )
    write_config(config_path, trades_path, output_root)

    result = run_paper_risk_controller(config_path=config_path)

    assert result.raw_net_pnl_usdt == 70.0
    assert result.paper_net_pnl_usdt == -30.0
    assert result.accepted_trades == 1
    assert result.skipped_trades == 1
    assert result.emergency_stop_days == ["2026-05-25"]
    assert result.daily[0]["emergency_stop_triggered"] is True


def test_runtime_outputs_are_written_only_when_requested(tmp_path: Path) -> None:
    trades_path = tmp_path / "trades.csv"
    config_path = tmp_path / "paper_risk.yml"
    output_root = tmp_path / "runtime_outputs"
    write_csv_rows(
        trades_path,
        [
            {
                "open_time_utc": "2026-05-25T10:00:00+00:00",
                "symbol": "BTCUSDT",
                "side": "long",
                "reported_pnl_usdt": "8",
            },
        ],
    )
    write_config(config_path, trades_path, output_root)

    result = run_paper_risk_controller(config_path=config_path)

    assert Path(result.outputs["daily_summary_json"]).exists()
    assert Path(result.outputs["daily_trades_csv"]).exists()
    assert Path(result.outputs["equity_csv"]).exists()
    assert Path(result.outputs["state_json"]).exists()
    state = json.loads(Path(result.outputs["state_json"]).read_text(encoding="utf-8"))
    assert state["mode"] == "paper_risk_controller_shadow"
    assert state["rows_used"] == 1


def test_no_write_mode_does_not_create_runtime_outputs(tmp_path: Path) -> None:
    trades_path = tmp_path / "trades.csv"
    config_path = tmp_path / "paper_risk.yml"
    output_root = tmp_path / "runtime_outputs"
    write_csv_rows(
        trades_path,
        [
            {
                "open_time_utc": "2026-05-25T10:00:00+00:00",
                "symbol": "BTCUSDT",
                "side": "long",
                "reported_pnl_usdt": "8",
            },
        ],
    )
    write_config(config_path, trades_path, output_root)

    result = run_paper_risk_controller(config_path=config_path, write_outputs=False)

    assert result.paper_net_pnl_usdt == 6.0
    assert not output_root.exists()


def test_live_and_real_order_flags_are_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORDER_SUBMISSION_ENABLED", "true")

    with pytest.raises(SafetyViolation):
        assert_environment_safe()


def test_policy_cannot_increase_risk() -> None:
    policy = PaperRiskPolicy(multipliers={"BTCUSDT": 1.25})

    with pytest.raises(SafetyViolation):
        policy.validate()
