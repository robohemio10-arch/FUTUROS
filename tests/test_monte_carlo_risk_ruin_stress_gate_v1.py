from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.risk.monte_carlo_risk_ruin_stress_gate import (
    build_monte_carlo_risk_ruin_stress_gate_v1,
)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def create_target_store(root: Path, returns: list[float]) -> None:
    reports = root / "data" / "reports"
    write_json(
        reports / "financial_label_target_store_v1.json",
        {
            "schema_version": "financial_label_target_store_v1",
            "row_count": len(returns),
            "target_store_hash": "target-hash",
            "dataset_hash": "dataset-hash",
            "feature_contract_hash": "feature-hash",
            "target_records": [
                {
                    "trade_id": f"t{index}",
                    "symbol_norm": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
                    "side": "long" if index % 2 == 0 else "short",
                    "target_net_pnl": value,
                }
                for index, value in enumerate(returns)
            ],
        },
    )
    write_json(
        reports / "ai_qlib_drift_regime_monitor_v1.json",
        {"status": "blocked", "decision": "MANTER_EM_RESEARCH", "lineage_hashes": {"dataset_hash": "dataset-hash"}},
    )
    write_json(
        reports / "paper_autotrain_feedback_loop_v1.json",
        {"status": "ok", "decision": "MANTER_EM_RESEARCH", "lineage_hashes": {"dataset_hash": "dataset-hash"}},
    )


def test_no_data_source_returns_blocked_and_does_not_write(tmp_path: Path) -> None:
    report = build_monte_carlo_risk_ruin_stress_gate_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "no_valid_returns_source"
    assert report["gate_decision"] == "BLOCKED"
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "monte_carlo_risk_ruin_stress_gate_v1.json").exists()


def test_simulation_is_deterministic_with_fixed_seed(tmp_path: Path) -> None:
    create_target_store(tmp_path, [1.0, -0.5, 0.7, -0.2, 1.3, -0.4])
    first = build_monte_carlo_risk_ruin_stress_gate_v1(
        project_root=tmp_path,
        seed=42,
        simulation_count=200,
        sample_size=30,
    )
    second = build_monte_carlo_risk_ruin_stress_gate_v1(
        project_root=tmp_path,
        seed=42,
        simulation_count=200,
        sample_size=30,
    )
    assert first["stress_scenarios"] == second["stress_scenarios"]
    assert first["worst_scenario"] == second["worst_scenario"]


def test_baseline_passes_when_risk_is_low(tmp_path: Path) -> None:
    create_target_store(tmp_path, [1.0] * 30)
    report = build_monte_carlo_risk_ruin_stress_gate_v1(
        project_root=tmp_path,
        simulation_count=100,
        sample_size=20,
    )
    baseline = next(row for row in report["stress_scenarios"] if row["scenario"] == "baseline")
    assert baseline["gate_decision"] == "PASS"
    assert baseline["risk_of_ruin"] == 0.0


def test_adverse_scenario_blocks_when_ruin_or_capital_breach_exceeds_threshold(tmp_path: Path) -> None:
    create_target_store(tmp_path, [-5.0] * 30)
    report = build_monte_carlo_risk_ruin_stress_gate_v1(
        project_root=tmp_path,
        simulation_count=100,
        sample_size=20,
        initial_capital=100.0,
        capital_floor=70.0,
        ruin_floor=50.0,
    )
    assert report["status"] == "blocked"
    combined = next(row for row in report["stress_scenarios"] if row["scenario"] == "combined_adverse_stress")
    assert combined["gate_decision"] == "BLOCKED"
    assert any("risk_of_ruin" in reason or "capital_floor" in reason for reason in combined["gate_reasons"])


def test_worst_scenario_governs_aggregate_decision(tmp_path: Path) -> None:
    create_target_store(tmp_path, [2.0, 2.0, 2.0, -8.0])
    report = build_monte_carlo_risk_ruin_stress_gate_v1(
        project_root=tmp_path,
        simulation_count=100,
        sample_size=25,
        ruin_floor=80.0,
    )
    decisions = {row["gate_decision"] for row in report["stress_scenarios"]}
    assert "BLOCKED" in decisions
    assert report["gate_decision"] == "BLOCKED"
    assert report["worst_scenario"]["gate_decision"] == "BLOCKED"


def test_domain_builder_never_writes_report_files(tmp_path: Path) -> None:
    create_target_store(tmp_path, [1.0] * 20)

    report = build_monte_carlo_risk_ruin_stress_gate_v1(project_root=tmp_path, write=True)

    reports = tmp_path / "data" / "reports"

    assert report["write_requested"] is True
    assert report["write_performed"] is False
    assert not (reports / "monte_carlo_risk_ruin_stress_gate_v1.json").exists()
    assert not (reports / "monte_carlo_risk_ruin_stress_gate_v1.md").exists()
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False


def test_cli_no_write_and_write_modes(tmp_path: Path) -> None:
    create_target_store(tmp_path, [1.0] * 20)
    no_write = subprocess.run(
        [
            sys.executable,
            "scripts/build_monte_carlo_risk_ruin_stress_gate_v1.py",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    no_write_payload = json.loads(no_write.stdout)
    assert no_write_payload["write_performed"] is False

    write = subprocess.run(
        [
            sys.executable,
            "scripts/build_monte_carlo_risk_ruin_stress_gate_v1.py",
            "--project-root",
            str(tmp_path),
            "--write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    write_payload = json.loads(write.stdout)
    assert write_payload["write_performed"] is True

def test_cli_write_creates_only_report_files(tmp_path: Path) -> None:
    create_target_store(tmp_path, [1.0] * 20)

    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "build_monte_carlo_risk_ruin_stress_gate_v1.py"

    files_before = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(tmp_path),
            "--write",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(completed.stdout)
    reports = tmp_path / "data" / "reports"

    files_after = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    created_files = sorted(files_after - files_before)

    assert report["write_requested"] is True
    assert report["write_performed"] is True

    assert created_files == [
        "data/reports/monte_carlo_risk_ruin_stress_gate_v1.json",
        "data/reports/monte_carlo_risk_ruin_stress_gate_v1.md",
    ]

    assert (reports / "monte_carlo_risk_ruin_stress_gate_v1.json").is_file()
    assert (reports / "monte_carlo_risk_ruin_stress_gate_v1.md").is_file()

    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "models").exists()
    assert not (tmp_path / "registry").exists()

    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False


def test_safety_flags_preserved(tmp_path: Path) -> None:
    create_target_store(tmp_path, [1.0, -0.1, 0.5])
    report = build_monte_carlo_risk_ruin_stress_gate_v1(project_root=tmp_path)
    for key, value in report["safety_flags"].items():
        if key in {"paper_only", "shadow_only", "research_only", "read_only"}:
            assert value is True
        else:
            assert value is False
    assert report["operational_authority"] is False
    assert report["can_change_risk_limits"] is False
    assert report["can_stop_bot"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False


def test_output_json_is_serializable(tmp_path: Path) -> None:
    create_target_store(tmp_path, [1.0, -0.2, 0.4])
    report = build_monte_carlo_risk_ruin_stress_gate_v1(project_root=tmp_path)
    payload = json.loads(json.dumps(report, sort_keys=True, default=str))
    assert payload["schema_version"] == "monte_carlo_risk_ruin_stress_gate_v1"


def test_operational_paths_are_not_modified(tmp_path: Path) -> None:
    create_target_store(tmp_path, [1.0] * 20)
    build_monte_carlo_risk_ruin_stress_gate_v1(project_root=tmp_path, write=True)
    forbidden_paths = [
        tmp_path / "freqtrade",
        tmp_path / "config" / "risk_limits.yml",
        tmp_path / "data" / "runtime",
        tmp_path / "data" / "models",
        tmp_path / "data" / "registry",
    ]
    assert all(not path.exists() for path in forbidden_paths)
