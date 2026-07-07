from __future__ import annotations

import ast
import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.paper_autotrain_quarantine_candidate_evaluation.evaluation import (
    DECISION_MANUAL_REVIEW,
    build_paper_autotrain_quarantine_candidate_evaluation_v1,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "build_paper_autotrain_quarantine_candidate_evaluation_v1.py"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def artifact_payload(
    *,
    backend_id: str = "qlib",
    run_id: str = "run_001",
    row_count: int = 26,
    feature_count: int = 10,
    class_balance: dict[str, int] | None = None,
) -> dict:
    balance = class_balance if class_balance is not None else {"0": 13, "1": 13}
    return {
        "candidate_id": f"{backend_id}_{run_id}",
        "backend_id": backend_id,
        "status": "trained_quarantine_only",
        "row_count": row_count,
        "feature_count": feature_count,
        "class_balance": balance,
        "mean_probability": 0.42,
        "promotion_eligible": False,
        "quarantine_only": True,
    }


def write_candidate(
    root: Path,
    *,
    backend_id: str = "qlib",
    run_id: str = "run_001",
    row_count: int = 26,
    feature_count: int = 10,
    class_balance: dict[str, int] | None = None,
    include_hash: bool = False,
    invalid_hash: bool = False,
) -> dict:
    payload = artifact_payload(
        backend_id=backend_id,
        run_id=run_id,
        row_count=row_count,
        feature_count=feature_count,
        class_balance=class_balance,
    )
    artifact = root / "data" / "models" / "quarantine" / "paper_autotrain" / run_id / f"{backend_id}_candidate_model.json"
    write_json(artifact, payload)
    registry_candidate = dict(payload)
    if include_hash:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        registry_candidate["artifact_hash"] = "0" * 64 if invalid_hash else digest
    return registry_candidate


def write_registry(root: Path, candidates: list[dict]) -> None:
    write_json(
        root / "data" / "registries" / "quarantine" / "paper_autotrain_candidate_registry_v1.json",
        {
            "schema_version": "paper_autotrain_quarantine_candidate_registry_v1",
            "candidate_count": len(candidates),
            "promoted_candidate_count": 0,
            "active_registry_changed": False,
            "quarantine_only": True,
            "candidates": candidates,
        },
    )


def write_gate(root: Path, relative: str, status: str = "ok", **extra: object) -> None:
    payload = {"status": status, "reason": f"{status}_fixture"}
    payload.update(extra)
    write_json(root / relative, payload)


def imported_modules() -> set[str]:
    source = (PROJECT_ROOT / "smartcrypto/learning/paper_autotrain_quarantine_candidate_evaluation/evaluation.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_missing_quarantine_registry_blocks(tmp_path: Path) -> None:
    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_quarantine_registry"
    assert report["decision"] == "MANTER_EM_QUARENTENA"
    assert report["eligible_candidate_count"] == 0
    assert report["promotes_model"] is False


def test_empty_registry_blocks(tmp_path: Path) -> None:
    write_registry(tmp_path, [])

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "no_quarantine_candidates"
    assert report["candidate_count"] == 0


def test_candidate_missing_artifact_is_blocked(tmp_path: Path) -> None:
    write_registry(tmp_path, [artifact_payload()])

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "quarantine_candidate_artifact_integrity_failed"
    assert report["candidates"][0]["artifact_exists"] is False


def test_candidate_invalid_hash_is_blocked(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, include_hash=True, invalid_hash=True)
    write_registry(tmp_path, [candidate])

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["artifact_integrity_status"] == "failed"
    assert "artifact_hash_mismatch" in report["candidates"][0]["blockers"]


def test_integral_qlib_candidate_is_evaluated(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, backend_id="qlib")
    write_registry(tmp_path, [candidate])

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["qlib_candidate_count"] == 1
    assert report["candidates"][0]["backend_id"] == "qlib"
    assert report["candidates"][0]["artifact_hash_validated"] is True


def test_integral_ai_shadow_candidate_is_evaluated(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, backend_id="ai_shadow")
    write_registry(tmp_path, [candidate])

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["ai_shadow_candidate_count"] == 1
    assert report["candidates"][0]["backend_id"] == "ai_shadow"
    assert report["candidates"][0]["artifact_hash_validated"] is True


def test_microbatch_rows_below_100_blocks_eligibility(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, row_count=26, class_balance={"0": 13, "1": 13})
    write_registry(tmp_path, [candidate])

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["observed_microbatch_rows"] == 26
    assert "min_microbatch_rows_not_met" in report["blockers"]
    assert report["eligible_candidate_count"] == 0


def test_class_balance_insufficient_blocks_eligibility(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, row_count=150, class_balance={"0": 145, "1": 5})
    write_registry(tmp_path, [candidate])

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert "min_class_positive_count_not_met" in report["blockers"]
    assert report["candidates"][0]["eligible_for_promotion"] is False


def test_drift_gate_blocked_blocks_eligibility(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, row_count=150, class_balance={"0": 75, "1": 75})
    write_registry(tmp_path, [candidate])
    write_gate(tmp_path, "data/reports/ai_qlib_drift_regime_monitor_v1.json", "blocked")

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["drift_gate_status"] == "blocked"
    assert "drift_gate_blocked" in report["blockers"]


def test_execution_cost_gate_blocked_blocks_eligibility(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, row_count=150, class_balance={"0": 75, "1": 75})
    write_registry(tmp_path, [candidate])
    write_gate(tmp_path, "data/reports/event_driven_backtest_execution_cost_gate_v1.json", "blocked")

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["execution_cost_gate_status"] == "blocked"
    assert "execution_cost_gate_blocked" in report["blockers"]


def test_monte_carlo_gate_blocked_blocks_eligibility(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, row_count=150, class_balance={"0": 75, "1": 75})
    write_registry(tmp_path, [candidate])
    write_gate(tmp_path, "data/reports/monte_carlo_risk_ruin_stress_gate_v1.json", "blocked")

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["monte_carlo_gate_status"] == "blocked"
    assert "monte_carlo_gate_blocked" in report["blockers"]


def test_readiness_blocked_blocks_eligibility(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, row_count=150, class_balance={"0": 75, "1": 75})
    write_registry(tmp_path, [candidate])
    write_gate(tmp_path, "data/reports/readiness_snapshot_v2.json", "ok", readiness_approved=False)

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert "readiness_blocked" in report["blockers"]
    assert report["eligible_candidate_count"] == 0


def test_no_candidate_is_promoted(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, row_count=150, class_balance={"0": 75, "1": 75})
    write_registry(tmp_path, [candidate])

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["writes_active_registry"] is False
    assert report["candidates"][0]["eligible_for_runtime"] is False


def test_active_registry_is_not_written(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, row_count=150, class_balance={"0": 75, "1": 75})
    write_registry(tmp_path, [candidate])
    active_registry = tmp_path / "data" / "registries" / "active" / "registry.json"
    write_json(active_registry, {"active": True})

    build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path, write_report=True)

    assert json.loads(active_registry.read_text(encoding="utf-8")) == {"active": True}


def test_active_model_is_not_written(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path, row_count=150, class_balance={"0": 75, "1": 75})
    write_registry(tmp_path, [candidate])
    active_model = tmp_path / "data" / "models" / "active" / "model.json"
    write_json(active_model, {"active": True})

    build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path, write_report=True)

    assert json.loads(active_model.read_text(encoding="utf-8")) == {"active": True}


def test_active_freqtrade_signals_not_created(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path)
    write_registry(tmp_path, [candidate])

    build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path, write_report=True)

    assert not list(tmp_path.rglob("active_freqtrade_signals.json"))


def test_data_runtime_is_not_created(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path)
    write_registry(tmp_path, [candidate])

    build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path, write_report=True)

    assert not (tmp_path / "data" / "runtime").exists()


def test_cli_default_does_not_write_report(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path)
    write_registry(tmp_path, [candidate])

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "paper_autotrain_quarantine_candidate_evaluation_v1.json").exists()


def test_cli_write_report_writes_only_data_reports(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path)
    write_registry(tmp_path, [candidate])

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--write-report", "--json"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "paper_autotrain_quarantine_candidate_evaluation_v1.json").is_file()
    assert (tmp_path / "data" / "reports" / "paper_autotrain_quarantine_candidate_evaluation_v1.md").is_file()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "data" / "registries" / "active").exists()


def test_report_json_is_serializable(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path)
    write_registry(tmp_path, [candidate])

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert "paper_autotrain_quarantine_candidate_evaluation_v1" in json.dumps(report, sort_keys=True)


def test_domain_does_not_import_freqtrade() -> None:
    modules = imported_modules()
    assert not any(module.startswith("freqtrade") for module in modules)


def test_domain_does_not_import_ccxt() -> None:
    sys.modules.pop("ccxt", None)
    importlib.import_module("smartcrypto.learning.paper_autotrain_quarantine_candidate_evaluation.evaluation")

    assert "ccxt" not in sys.modules


def test_domain_does_not_import_risk_manager() -> None:
    modules = imported_modules()
    assert not any(module.startswith("smartcrypto.risk") for module in modules)


def test_domain_does_not_call_docker_or_operational_subprocess() -> None:
    modules = imported_modules()
    assert "subprocess" not in modules
    assert not any(module.startswith("docker") for module in modules)


def test_safety_flags_remain_false_for_runtime_orders_and_risk(tmp_path: Path) -> None:
    candidate = write_candidate(tmp_path)
    write_registry(tmp_path, [candidate])

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["research_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    for key in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "trains_model",
        "runs_training",
        "promotes_model",
        "model_promotion_performed",
        "active_model_changed",
        "writes_runtime",
        "writes_sqlite",
        "writes_parquet",
    ):
        assert report[key] is False


def test_good_candidate_reaches_manual_review_only_without_promotion(tmp_path: Path) -> None:
    candidate = write_candidate(
        tmp_path,
        row_count=150,
        feature_count=9,
        class_balance={"0": 80, "1": 70},
    )
    write_registry(tmp_path, [candidate])

    report = build_paper_autotrain_quarantine_candidate_evaluation_v1(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["decision"] == DECISION_MANUAL_REVIEW
    assert report["eligible_candidate_count"] == 1
    assert report["candidates"][0]["eligible_for_manual_review"] is True
    assert report["candidates"][0]["eligible_for_promotion"] is False
    assert report["model_promotion_performed"] is False
