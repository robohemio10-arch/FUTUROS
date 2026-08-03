from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from smartcrypto.research.paper_ab_testnet_chaos_readiness import (
    CONFIG_SCHEMA_VERSION,
    DECISION_BLOCKED,
    DECISION_READY,
    EVIDENCE_SCHEMA_VERSION,
    MANDATORY_SOAK_METRICS,
    REQUIRED_CHAOS_SCENARIOS,
    REQUIRED_TESTNET_STAGES,
    InMemoryTestnetGateway,
    TestnetSignal as B06Signal,
    build_initial_soak_state,
    build_paper_ab_testnet_chaos_readiness_v2,
    build_soak_plan,
    run_isolated_chaos_suite,
    run_isolated_testnet_e2e,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def config() -> dict[str, Any]:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "paper_ab": {
            "minimum_trades_per_strategy": 30,
            "minimum_expectancy_delta": 0.0,
            "minimum_profit_factor_delta": 0.0,
            "maximum_drawdown_regression_ratio": 0.10,
            "maximum_total_cost_bps": 50.0,
            "minimum_stability_periods": 4,
            "minimum_positive_period_ratio": 0.50,
        },
        "testnet_e2e": {
            "minimum_runs": 3,
            "accepted_evidence_classes": ["exchange_testnet"],
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
        "soak": {
            "required_days": 30,
            "required_metrics": list(MANDATORY_SOAK_METRICS),
        },
    }


def trades(
    strategy_id: str,
    *,
    pnl_scale: float = 1.0,
    single_week: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(30):
        pnl = (1.0 if index % 2 == 0 else -0.55) * pnl_scale
        day = 1 if single_week else (index % 28) + 1
        rows.append(
            {
                "trade_id": f"{strategy_id}-{index}",
                "symbol": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
                "side": "long" if index % 3 else "short",
                "close_time_utc": f"2026-07-{day:02d}T12:00:00+00:00",
                "net_pnl": pnl,
                "notional": 1000.0,
                "fees": 0.40,
                "funding": 0.05,
            }
        )
    return rows


def valid_testnet_runs() -> list[dict[str, Any]]:
    return [
        {
            "run_id": f"testnet-{index}",
            "environment": "testnet",
            "endpoint_class": "testnet",
            "real_order": False,
            "evidence_class": "exchange_testnet",
            "testnet_order_submitted": True,
            "active_runtime_touched": False,
            "stages": {stage: True for stage in REQUIRED_TESTNET_STAGES},
        }
        for index in range(3)
    ]


def valid_chaos() -> list[dict[str, Any]]:
    return [
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


def valid_capacity() -> list[dict[str, Any]]:
    return [
        {
            "observation_id": f"{symbol}-{index}",
            "symbol": symbol,
            "stake": 500.0,
            "notional": 1000.0,
            "depth_usdt": 100_000.0,
            "leverage": 2.0,
            "participation_ratio": 0.01,
            "frequency_per_hour": 2.0,
            "turnover_per_day": 5_000.0,
            "spread_bps": 2.0,
            "slippage_bps": 3.0,
            "market_impact_bps": 1.0,
            "liquidation_buffer_pct": 30.0,
        }
        for symbol in ("BTCUSDT", "ETHUSDT")
        for index in range(3)
    ]


def valid_evidence() -> dict[str, Any]:
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
        "testnet_e2e": {"runs": valid_testnet_runs()},
        "chaos": {"scenarios": valid_chaos()},
        "capacity": {"observations": valid_capacity()},
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


class RejectAllRiskGate:
    def approve(self, _signal: B06Signal) -> bool:
        return False


class ProductionGateway(InMemoryTestnetGateway):
    environment = "mainnet"
    endpoint_class = "production"
    real_order = True


def build(
    tmp_path: Path,
    evidence: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
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


def test_safety_flags_block_operational_authority(tmp_path: Path) -> None:
    report = build(tmp_path, valid_evidence())
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    for field in (
        "operational_authority",
        "live_trading_enabled",
        "live_release_allowed",
        "canary_release_allowed",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "testnet_order_submission_enabled",
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
        "starts_soak",
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
    assert recommendation["action"] == "QUARANTINE_CHALLENGER_FOR_SOAK"
    assert recommendation["automatic_promotion"] is False
    assert recommendation["operational_authority"] is False
    assert report["gates"]["paper_ab"]["model_promotion_performed"] is False


def test_paper_ab_stability_metrics_are_present(tmp_path: Path) -> None:
    report = build(tmp_path, valid_evidence())
    stability = report["gates"]["paper_ab"]["champion"]["stability"]
    assert stability["period_count"] >= 4
    assert stability["positive_period_ratio"] >= 0.5
    assert stability["period_expectancy_stddev"] is not None


def test_paper_ab_insufficient_stability_periods_block(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["paper_ab"]["champion"]["trades"] = trades(
        "champion", single_week=True
    )
    report = build(tmp_path, evidence)
    assert any("insufficient_stability_period_count" in item for item in report["blockers"])


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


def test_capacity_stake_notional_mismatch_is_blocked(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["capacity"]["observations"][0]["stake"] = 100.0
    report = build(tmp_path, evidence)
    assert any("stake_notional_leverage_mismatch" in item for item in report["blockers"])


def test_capacity_envelope_covers_all_dimensions(tmp_path: Path) -> None:
    report = build(tmp_path, valid_evidence())
    envelope = report["capacity_recommendations"]
    assert envelope["BTCUSDT"]["safe_notional_abs"] == 5000.0
    assert envelope["ETHUSDT"]["safe_stake_at_max_leverage_abs"] == 1666.6666666667
    assert report["gates"]["capacity"]["risk_configuration_changed"] is False
    assert "turnover" in report["gates"]["capacity"]["dimensions_measured"]


def test_unresolved_p1_blocks_soak(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["incidents"] = [
        {"incident_id": "INC-1", "severity": "P1", "status": "open"}
    ]
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
    assert writer.json_payloads[json_path]["write_report_performed"] is True
    assert markdown_path in writer.text_payloads
    assert not json_path.exists()


def test_report_is_json_serializable(tmp_path: Path) -> None:
    encoded = json.dumps(build(tmp_path, valid_evidence()), sort_keys=True)
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
    evidence["paper_ab"]["challengers"][0]["evaluation_window_id"] = "other"
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
    assert any("config_missing_chaos_scenario" in item for item in report["blockers"])


def test_config_cannot_remove_mandatory_testnet_stage(tmp_path: Path) -> None:
    reduced = config()
    reduced["testnet_e2e"]["required_stages"] = list(REQUIRED_TESTNET_STAGES[:-1])
    report = build_paper_ab_testnet_chaos_readiness_v2(
        project_root=tmp_path,
        evidence_payload=valid_evidence(),
        config_payload=reduced,
    )
    assert any("config_missing_testnet_stage" in item for item in report["blockers"])


def test_config_cannot_remove_mandatory_soak_metric(tmp_path: Path) -> None:
    reduced = config()
    reduced["soak"]["required_metrics"] = list(MANDATORY_SOAK_METRICS[:-1])
    report = build_paper_ab_testnet_chaos_readiness_v2(
        project_root=tmp_path,
        evidence_payload=valid_evidence(),
        config_payload=reduced,
    )
    assert any("config_missing_soak_metric" in item for item in report["blockers"])


def test_soak_cannot_be_shorter_than_thirty_days(tmp_path: Path) -> None:
    reduced = config()
    reduced["soak"]["required_days"] = 29
    report = build_paper_ab_testnet_chaos_readiness_v2(
        project_root=tmp_path,
        evidence_payload=valid_evidence(),
        config_payload=reduced,
    )
    assert "config_soak_required_days_below_thirty" in report["blockers"]


def test_isolated_testnet_harness_is_smoke_only_not_final_evidence(
    tmp_path: Path,
) -> None:
    evidence = valid_evidence()
    evidence["testnet_e2e"] = {"runs": []}
    report = build(tmp_path, evidence, run_isolated_testnet=True)
    gate = report["gates"]["testnet_e2e"]
    assert gate["passed"] is False
    assert gate["isolated_harness_run_count"] == 3
    assert report["isolated_testnet_harness_ran"] is True
    assert report["sends_orders"] is False
    assert any("evidence_class_not_accepted" in item for item in report["blockers"])


def test_isolated_chaos_harness_can_supply_chaos_evidence(tmp_path: Path) -> None:
    evidence = valid_evidence()
    evidence["chaos"] = {"scenarios": []}
    report = build(tmp_path, evidence, run_isolated_chaos=True)
    assert report["gates"]["chaos"]["passed"] is True
    assert report["isolated_chaos_harness_ran"] is True
    assert report["restarts_containers"] is False


def test_soak_initialization_uses_advisory_writer(tmp_path: Path) -> None:
    writer = MemoryWriter()
    report = build(
        tmp_path,
        valid_evidence(),
        initialize_soak=True,
        writer_backend=writer,
    )
    path = tmp_path / "data/reports/soak/paper_shadow_soak_state_v2.json"
    assert report["soak_initialization_performed"] is True
    state = writer.json_payloads[path]
    assert state["required_days"] == 30
    assert state["required_metrics"] == list(MANDATORY_SOAK_METRICS)
    assert state["starts_service"] is False
    assert not path.exists()


def test_soak_initialization_is_blocked_when_readiness_fails(tmp_path: Path) -> None:
    writer = MemoryWriter()
    evidence = valid_evidence()
    evidence["prerequisites"]["g00_status"] = "BLOCKED"
    report = build(
        tmp_path,
        evidence,
        initialize_soak=True,
        writer_backend=writer,
    )
    assert report["soak_initialization_performed"] is False
    assert writer.json_payloads == {}


def test_testnet_harness_executes_complete_lifecycle() -> None:
    report = run_isolated_testnet_e2e(
        run_id="run-1",
        signal=B06Signal("signal-1", "BTCUSDT", "long", 0.01, 50_000.0),
    )
    assert report["status"] == "pass"
    assert all(report["stages"].values())
    assert report["real_order"] is False
    assert report["evidence_class"] == "isolated_harness"
    assert report["testnet_order_submitted"] is False
    assert report["production_endpoint_accessed"] is False


def test_testnet_harness_rejects_before_order() -> None:
    report = run_isolated_testnet_e2e(
        run_id="run-rejected",
        signal=B06Signal("signal-2", "BTCUSDT", "long", 0.01, 50_000.0),
        risk_gate=RejectAllRiskGate(),
    )
    assert report["status"] == "blocked"
    assert report["stages"]["risk_approved"] is False
    assert report["stages"]["order_submitted_testnet"] is False


def test_testnet_harness_blocks_production_gateway() -> None:
    report = run_isolated_testnet_e2e(
        run_id="run-production",
        signal=B06Signal("signal-3", "BTCUSDT", "long", 0.01, 50_000.0),
        gateway=ProductionGateway(),
    )
    assert report["status"] == "blocked"
    assert "real_order_gateway_forbidden" in report["blockers"]


def test_in_memory_gateway_is_idempotent_by_signal() -> None:
    gateway = InMemoryTestnetGateway()
    signal = B06Signal("same", "ETHUSDT", "short", 0.10, 3_000.0)
    first = gateway.submit(signal)
    second = gateway.submit(signal)
    assert first.order_id == second.order_id
    assert gateway.reconcile()["order_count"] == 1


def test_isolated_chaos_suite_passes_all_mandatory_scenarios() -> None:
    rows = run_isolated_chaos_suite()
    assert {row["scenario_id"] for row in rows} == set(REQUIRED_CHAOS_SCENARIOS)
    assert all(row["status"] == "pass" for row in rows)
    assert all(row["data_loss"] is False for row in rows)
    assert all(row["duplicate_orders"] is False for row in rows)
    assert all(row["active_runtime_touched"] is False for row in rows)


def test_sqlite_lock_scenario_proves_recovery() -> None:
    rows = {row["scenario_id"]: row for row in run_isolated_chaos_suite()}
    assert rows["sqlite_locked"]["details"]["lock_detected"] is True
    assert rows["sqlite_locked"]["details"]["committed_event_ids"] == ["recovered"]


def test_disk_full_scenario_preserves_previous_report() -> None:
    rows = {row["scenario_id"]: row for row in run_isolated_chaos_suite()}
    details = rows["disk_full"]["details"]
    assert details["enospc_detected"] is True
    assert details["previous_report_preserved"] is True


def test_restart_loop_scenario_opens_circuit_breaker() -> None:
    rows = {row["scenario_id"]: row for row in run_isolated_chaos_suite()}
    details = rows["restart_loop"]["details"]
    assert details["restart_attempts"] == 3
    assert details["circuit_breaker_open"] is True


def test_soak_plan_requires_all_metrics() -> None:
    plan = build_soak_plan(config())
    assert plan["required_days"] == 30
    assert plan["missing_mandatory_metrics"] == []
    assert plan["scheduler_created"] is False


def test_initial_soak_state_spans_thirty_days(tmp_path: Path) -> None:
    report = build(tmp_path, valid_evidence())
    state = build_initial_soak_state(
        readiness_report=report,
        config=config(),
        started_at_utc="2026-08-03T12:00:00+00:00",
    )
    assert state["status"] == "running"
    assert state["target_end_at_utc"] == "2026-09-02T12:00:00+00:00"
    assert state["order_submission_enabled"] is False
    assert state["writes_runtime"] is False


def test_cli_default_is_fail_closed_without_evidence() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_paper_ab_testnet_chaos_readiness_v2.py",
            "--project-root",
            ".",
            "--json",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert payload["write_report_performed"] is False


def test_b06_sources_have_no_operational_integrations() -> None:
    package = PROJECT_ROOT / "smartcrypto/research/paper_ab_testnet_chaos_readiness"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(package.glob("*.py"))
    ).lower()
    forbidden = (
        "import ccxt",
        "from ccxt",
        "import freqtrade",
        "from freqtrade",
        "docker compose",
        "create_order(",
        "cancel_order(",
        "fetch_balance(",
        "import requests",
        "import httpx",
    )
    assert not any(token in source for token in forbidden)
