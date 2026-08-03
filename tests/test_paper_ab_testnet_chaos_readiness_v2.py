from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.research.paper_ab_testnet_chaos_readiness import (
    CONFIG_SCHEMA_VERSION,
    DECISION_BLOCKED,
    DECISION_READY,
    EVIDENCE_SCHEMA_VERSION,
    REQUIRED_CHAOS_SCENARIOS,
    REQUIRED_TESTNET_STAGES,
    build_paper_ab_testnet_chaos_readiness_v2,
)


def config() -> dict[str, Any]:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "paper_ab": {
            "minimum_trades_per_strategy": 30,
            "minimum_expectancy_delta": 0.0,
            "minimum_profit_factor_delta": 0.0,
            "maximum_drawdown_regression_ratio": 0.10,
            "maximum_total_cost_bps": 50.0,
        },
        "testnet_e2e": {
            "minimum_runs": 3,
            "required_stages": list(REQUIRED_TESTNET_STAGES),
        },
        "chaos": {
            "maximum_recovery_seconds": 300.0,
            "required_scenarios": list(REQUIRED_CHAOS_SCENARIOS),
        },
        "capacity": {
            "required_symbols": ["BTCUSDT", "ETHUSDT"],
            "minimum_observations_per_symbol": 3,
            "maximum_total_execution_cost_bps": 50.0,
            "maximum_participation_ratio": 0.05,
            "maximum_leverage": 3.0,
            "minimum_liquidation_buffer_pct": 15.0,
        },
    }


def trades(strategy_id: str, *, pnl_scale: float = 1.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(30):
        pnl = (1.0 if index % 2 == 0 else -0.55) * pnl_scale
        rows.append(
            {
                "trade_id": f"{strategy_id}-{index}",
                "symbol": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
                "side": "long" if index % 3 else "short",
                "close_time_utc": f"2026-07-{(index % 28) + 1:02d}T12:00:00+00:00",
                "net_pnl": pnl,
                "notional": 1000.0,
                "fees": 0.40,
                "funding": 0.05,
            }
        )
    return rows


def valid_evidence() -> dict[str, Any]:
    testnet_runs = [
        {
            "run_id": f"testnet-{index}",
            "environment": "testnet",
            "endpoint_class": "testnet",
            "real_order": False,
            "active_runtime_touched": False,
            "stages": {stage: True for stage in REQUIRED_TESTNET_STAGES},
        }
        for index in range(3)
    ]
    chaos = [
        {
            "scenario_id": scenario,
            "status": "pass",
            "data_loss": False,
            "duplicate_orders": False,
            "active_runtime_touched": False,
            "recovery_seconds": 30.0,
        }
        for scenario in REQUIRED_CHAOS_SCENARIOS
    ]
    capacity = [
        {
            "observation_id": f"{symbol}-{index}",
            "symbol": symbol,
            "notional": 1000.0,
            "depth_usdt": 100000.0,
            "leverage": 2.0,
            "participation_ratio": 0.01,
            "spread_bps": 2.0,
            "slippage_bps": 3.0,
            "market_impact_bps": 1.0,
            "liquidation_buffer_pct": 30.0,
        }
        for symbol in ("BTCUSDT", "ETHUSDT")
        for index in range(3)
    ]
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "prerequisites": {"g00_status": "PASS"},
        "paper_ab": {
            "champion": {
                "strategy_id": "champion",
                "evaluation_window_id": "window-2026-07",
                "trades": trades("champion"),
            },
            "challengers": [
                {
                    "strategy_id": "challenger-a",
                    "evaluation_window_id": "window-2026-07",
                    "trades": trades("challenger-a", pnl_scale=1.1),
                }
            ],
        },
        "testnet_e2e": {"runs": testnet_runs},
        "chaos": {"scenarios": chaos},
        "capacity": {"observations": capacity},
        "incidents": [],
    }


class MemoryWriter:
    def __init__(self) -> None:
        self.json_payloads: dict[Path, dict[str, Any]] = {}
        self.text_payloads: dict[Path, str] = {}

    def write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        self.json_payloads[path] = dict(payload)

    def write_text(self, path: Path, text: str) -> None:
        self.text_payloads[path] = text


def build(tmp_path: Path, evidence: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return build_paper_ab_testnet_chaos_readiness_v2(
        project_root=tmp_path,
        evidence_payload=evidence,
        config_payload=config(),
        generated_at_utc="2026-08-03T12:00:00+00:00",
        **kwargs,
    )


def test_missing_evidence_is_blocked_and_no_write(tmp_path: Path) -> None:
    report = build(tmp_path)
    assert report["status"] == "blocked"
    assert report["decision"] == DECISION_BLOCKED
    assert "evidence_required" in report["blockers"]
    assert report["write_report_performed"] is False
    assert not (tmp_path / "data").exists()


def test_complete_evidence_is_ready_for_soak(tmp_path: Path) -> None:
    report = build(tmp_path, valid_evidence())
    assert report["status"] == "ok"
    assert report["decision"] == DECISION_READY
    assert report["ready_for_30_day_soak"] is True
    assert report["passed_gate_count"] == 6
    assert report["failed_gate_ids"] == []


def test_safety_flags_always_block_operational_authority(tmp_path: Path) -> None:
    report = build(tmp_path, valid_evidence())
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    for field in (
        "live_trading_enabled",
        "live_release_allowed",
        "canary_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "writes_runtime",
        "restarts_containers",
        "runs_training",
        "promotes_model",
        "automatic_promotion",
        "model_promotion_performed",
        "active_model_changed",
        "writes_active_registry",
        "writes_active_signals",
    ):
        assert report[field] is False
        assert report["safety_flags"][field] is False


def test_paper_ab_duplicate_trade_ids_block(tmp_path: Path) -> None:
    evidence = valid_evidence()
    champion = evidence["paper_ab"]["champion"]["trades"]
    champion[1]["trade_id"] = champion[0]["trade_id"]
    report = build(tmp_path, evidence)
    assert "paper_ab" in report["failed_gate_ids"]
    assert any("duplicate_trade_ids" in item for item in report["blockers"])


def test_paper_ab_recommendation_never_promotes(tmp_path: Path) -> None:
    report = build(tmp_path, valid_evidence())
    recommendation = report["paper_ab_recommendation"]
    assert recommendation["automatic_promotion"] is False
    assert recommendation["operational_authority"] is False
    assert report["gates"]["paper_ab"]["model_promotion_performed"] is False


def test_testnet_production_environment_is_blocked(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["testnet_e2e"]["runs"][0]["environment"] = "mainnet"
    evidence["testnet_e2e"]["runs"][0]["endpoint_class"] = "production"
    report = build(tmp_path, evidence)
    assert "testnet_e2e" in report["failed_gate_ids"]
    assert any("production_endpoint_forbidden" in item for item in report["blockers"])


def test_testnet_missing_stage_is_blocked(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["testnet_e2e"]["runs"][0]["stages"]["reconciliation_complete"] = False
    report = build(tmp_path, evidence)
    assert any("missing_stage:reconciliation_complete" in item for item in report["blockers"])


def test_missing_chaos_scenario_is_blocked(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["chaos"]["scenarios"] = evidence["chaos"]["scenarios"][:-1]
    report = build(tmp_path, evidence)
    assert "chaos" in report["failed_gate_ids"]
    assert any("scenario_missing" in item for item in report["blockers"])


def test_chaos_data_loss_is_blocked(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["chaos"]["scenarios"][0]["data_loss"] = True
    report = build(tmp_path, evidence)
    assert any("data_loss_must_be_false" in item for item in report["blockers"])


def test_capacity_cost_threshold_is_blocked(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["capacity"]["observations"][0]["slippage_bps"] = 100.0
    report = build(tmp_path, evidence)
    assert "capacity" in report["failed_gate_ids"]
    assert any("total_execution_cost_exceeds_limit" in item for item in report["blockers"])


def test_capacity_recommendations_are_advisory(tmp_path: Path) -> None:
    report = build(tmp_path, valid_evidence())
    assert report["capacity_recommendations"] == {"BTCUSDT": 5000.0, "ETHUSDT": 5000.0}
    assert report["gates"]["capacity"]["risk_configuration_changed"] is False


def test_unresolved_p1_blocks_soak(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["incidents"] = [{"incident_id": "INC-1", "severity": "P1", "status": "open"}]
    report = build(tmp_path, evidence)
    assert "incidents" in report["failed_gate_ids"]
    assert report["ready_for_30_day_soak"] is False


def test_g00_must_be_pass(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["prerequisites"]["g00_status"] = "BLOCKED"
    report = build(tmp_path, evidence)
    assert "prerequisites" in report["failed_gate_ids"]


def test_unsafe_write_path_is_blocked_before_writer(tmp_path: Path) -> None:
    writer = MemoryWriter()
    report = build(
        tmp_path,
        valid_evidence(),
        write_report=True,
        output_json_path="../outside.json",
        writer_backend=writer,
    )
    assert report["status"] == "blocked"
    assert "output_json_outside_data_reports" in report["blockers"]
    assert writer.json_payloads == {}
    assert writer.text_payloads == {}


def test_explicit_report_write_uses_injected_writer_only(tmp_path: Path) -> None:
    writer = MemoryWriter()
    report = build(tmp_path, valid_evidence(), write_report=True, writer_backend=writer)
    json_path = tmp_path / "data/reports/paper_ab_testnet_chaos_readiness_v2.json"
    markdown_path = tmp_path / "data/reports/paper_ab_testnet_chaos_readiness_v2.md"
    assert report["write_report_performed"] is True
    assert json_path in writer.json_payloads
    assert markdown_path in writer.text_payloads
    assert not json_path.exists()
    assert not markdown_path.exists()


def test_report_is_json_serializable(tmp_path: Path) -> None:
    encoded = json.dumps(build(tmp_path, valid_evidence()), sort_keys=True, ensure_ascii=False)
    assert "paper_ab_testnet_chaos_readiness_v2" in encoded


def test_invalid_config_schema_is_fail_closed(tmp_path: Path) -> None:
    bad_config = config()
    bad_config["schema_version"] = "invalid"
    report = build_paper_ab_testnet_chaos_readiness_v2(
        project_root=tmp_path,
        evidence_payload=valid_evidence(),
        config_payload=bad_config,
    )
    assert report["status"] == "blocked"
    assert "config_schema_version_invalid" in report["blockers"]


def test_paper_ab_requires_same_evaluation_window(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["paper_ab"]["challengers"][0]["evaluation_window_id"] = "other-window"
    report = build(tmp_path, evidence)
    assert any("evaluation_window_mismatch" in item for item in report["blockers"])


def test_config_cannot_remove_mandatory_chaos_scenario(tmp_path: Path) -> None:
    reduced = config()
    reduced["chaos"]["required_scenarios"] = list(REQUIRED_CHAOS_SCENARIOS[:-1])
    report = build_paper_ab_testnet_chaos_readiness_v2(
        project_root=tmp_path,
        evidence_payload=valid_evidence(),
        config_payload=reduced,
    )
    assert report["status"] == "blocked"
    assert any("config_missing_chaos_scenario" in item for item in report["blockers"])


def test_config_cannot_remove_mandatory_testnet_stage(tmp_path: Path) -> None:
    reduced = config()
    reduced["testnet_e2e"]["required_stages"] = list(REQUIRED_TESTNET_STAGES[:-1])
    report = build_paper_ab_testnet_chaos_readiness_v2(
        project_root=tmp_path,
        evidence_payload=valid_evidence(),
        config_payload=reduced,
    )
    assert report["status"] == "blocked"
    assert any("config_missing_testnet_stage" in item for item in report["blockers"])
