from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from smartcrypto.research.paper_ab_edge_selector import (
    DECISION,
    REQUIRED_TREATMENT_GATES,
    SAFETY_FLAGS,
    ExperimentConfig,
    PaperABEdgeSelectorEngine,
    assign_candidate,
    build_paper_ab_edge_selector_v1,
    deterministic_bootstrap_delta_expectancy,
    treatment_eligibility,
)
from smartcrypto.research.paper_ab_edge_selector.engine import (
    _arm_metrics,
    _financial_evidence,
    _read_qlib_security_evidence,
)
from smartcrypto.research.paper_ab_edge_selector.persistence import (
    resolve_assignments_path,
    resolve_report_path,
    write_assignments_idempotent,
    write_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _config(**overrides: Any) -> ExperimentConfig:
    values: dict[str, Any] = {
        "experiment_id": "paper-ab-edge-selector-v1-test",
        "minimum_observations_per_arm": 2,
        "minimum_observation_days": 0,
        "bootstrap_iterations": 200,
        "bootstrap_seed": 77,
        "confidence_level": 0.95,
    }
    values.update(overrides)
    return ExperimentConfig(**values)


def _gates(value: bool = True) -> dict[str, bool]:
    return {gate: value for gate in REQUIRED_TREATMENT_GATES}


def _security_ok() -> dict[str, Any]:
    return {"gate_passed": True, "status": "ok", "reason": "approved"}


def _security_blocked(reason: str = "upstream_constraint_blocked") -> dict[str, Any]:
    return {"gate_passed": False, "status": "blocked", "reason": reason}


def _estimate(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "estimate_id": "estimate-1",
        "estimate_subject_id": "trade:1",
        "candidate_id": "candidate-1",
        "candidate_linkage_status": "LINKED",
        "observed_at_utc": "2026-08-01T00:00:00Z",
        "point_in_time_consumable": True,
        "branch2_compatible": True,
        "financial_estimate_trusted": True,
        "candidate_ev": 0.5,
        "candidate_ev_status": "AVAILABLE",
    }
    row.update(overrides)
    return row


def _closed_trade_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": 1,
                "is_open": 0,
                "pair": "BTC/USDT:USDT",
                "is_short": 0,
                "open_date": "2026-08-01T00:00:00Z",
                "close_date": "2026-08-01T01:00:00Z",
                "close_profit_abs": 1.25,
                "close_profit": 0.01,
                "stake_amount": 50.0,
                "open_rate": 100.0,
                "max_rate": 102.0,
                "min_rate": 99.0,
            },
            {
                "id": 2,
                "is_open": 1,
                "pair": "ETH/USDT:USDT",
                "is_short": 1,
                "open_date": "2026-08-02T00:00:00Z",
                "close_date": None,
                "close_profit_abs": None,
                "close_profit": None,
                "stake_amount": 50.0,
                "open_rate": 100.0,
                "max_rate": 100.0,
                "min_rate": 100.0,
            },
        ]
    )


def _financial_report(*, linked: int = 1, gates: dict[str, bool] | None = None) -> dict[str, Any]:
    active_gates = _gates(True) if gates is None else gates
    return {
        "status": "PARTIAL",
        "reason": "research",
        "decision": "MANTER_EM_RESEARCH",
        "blockers": [],
        "gates": active_gates,
        "dataset": {"candidate_linked_row_count": linked},
        "candidate_estimates": {
            "estimate_count": 1,
            "trusted_estimate_count": 1,
            "candidate_ev_generated_count": 1,
            "candidate_ev_blocked_count": 0,
        },
        "sources": {},
    }


def _patch_engine_sources(
    monkeypatch: pytest.MonkeyPatch,
    *,
    financial_report: dict[str, Any],
    estimates: list[dict[str, Any]],
    security: dict[str, Any],
) -> None:
    import smartcrypto.research.paper_ab_edge_selector.engine as engine_module

    monkeypatch.setattr(
        engine_module,
        "read_authoritative_paper_source",
        lambda _path: {
            "path": Path("paper.sqlite"),
            "sha256_before": "A" * 64,
            "sha256_after": "A" * 64,
            "sqlite_integrity_check": "ok",
            "trades": _closed_trade_frame(),
            "orders": pd.DataFrame(),
        },
    )
    monkeypatch.setattr(
        engine_module.FinancialAIResearchEngine,
        "run",
        lambda self, **kwargs: (financial_report, estimates),
    )
    monkeypatch.setattr(
        engine_module,
        "_read_qlib_security_evidence",
        lambda root, value: security,
    )


def test_deterministic_assignment() -> None:
    first = assign_candidate(_config(), _estimate(), global_gates=_gates(), qlib_security_evidence=_security_ok())
    second = assign_candidate(_config(), _estimate(), global_gates=_gates(), qlib_security_evidence=_security_ok())
    assert first == second
    assert first.status == "ASSIGNED"
    assert first.arm in {"CONTROL", "TREATMENT"}


def test_assignment_invariant_across_process_run() -> None:
    code = (
        "from smartcrypto.research.paper_ab_edge_selector import ExperimentConfig,assign_candidate;"
        "g={k:True for k in ('candidate_ev_ready','regression_quality_gate','classification_quality_gate',"
        "'calibration_gate','monotonicity_gate','drift_gate','qlib_lineage_gate','trader_master_linkage_gate')};"
        "e={'candidate_id':'candidate-1','candidate_linkage_status':'LINKED','observed_at_utc':'2026-08-01T00:00:00Z',"
        "'point_in_time_consumable':True,'branch2_compatible':True,'financial_estimate_trusted':True,"
        "'candidate_ev':0.5,'candidate_ev_status':'AVAILABLE'};"
        "s={'gate_passed':True,'reason':'ok'};"
        "r=assign_candidate(ExperimentConfig('paper-ab-edge-selector-v1-test'),e,global_gates=g,qlib_security_evidence=s);"
        "print(r.assignment_id+'|'+r.arm)"
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    one = subprocess.check_output([sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env, text=True).strip()
    two = subprocess.check_output([sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env, text=True).strip()
    assert one == two


def test_different_experiment_id_can_change_allocation() -> None:
    base = assign_candidate(_config(experiment_id="exp-0"), _estimate(), global_gates=_gates(), qlib_security_evidence=_security_ok())
    alternatives = [
        assign_candidate(_config(experiment_id=f"exp-{idx}"), _estimate(), global_gates=_gates(), qlib_security_evidence=_security_ok())
        for idx in range(1, 100)
    ]
    assert any(item.arm != base.arm for item in alternatives)


def test_missing_candidate_id_is_ineligible() -> None:
    record = assign_candidate(_config(), _estimate(candidate_id=None), global_gates=_gates(), qlib_security_evidence=_security_ok())
    assert record.status == "INELIGIBLE_CANDIDATE_ID_MISSING"
    assert record.assignment_id is None
    assert record.arm is None


def test_post_trade_outcome_data_does_not_affect_assignment() -> None:
    clean = _estimate()
    polluted = {**clean, "close_profit_abs": -999.0, "exit_reason": "stop_loss", "mfe": 10, "mae": -10}
    first = assign_candidate(_config(), clean, global_gates=_gates(), qlib_security_evidence=_security_ok())
    second = assign_candidate(_config(), polluted, global_gates=_gates(), qlib_security_evidence=_security_ok())
    assert (first.assignment_id, first.arm) == (second.assignment_id, second.arm)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("candidate_linkage_status", "CANDIDATE_UNLINKED", "CANDIDATE_NOT_LINKED"),
        ("point_in_time_consumable", False, "POINT_IN_TIME_NOT_CONSUMABLE"),
        ("branch2_compatible", False, "BRANCH2_INCOMPATIBLE"),
        ("financial_estimate_trusted", False, "FINANCIAL_ESTIMATE_NOT_TRUSTED"),
        ("candidate_ev", None, "CANDIDATE_EV_MISSING"),
    ],
)
def test_estimate_level_treatment_gates_block(field: str, value: Any, expected: str) -> None:
    eligible, blockers = treatment_eligibility(
        _estimate(**{field: value}),
        _gates(),
        _security_ok(),
    )
    assert not eligible
    assert expected in blockers


def test_candidate_ev_ready_false_blocks_whole_treatment() -> None:
    gates = _gates()
    gates["candidate_ev_ready"] = False
    eligible, blockers = treatment_eligibility(_estimate(), gates, _security_ok())
    assert not eligible
    assert "GLOBAL_GATE_FALSE:candidate_ev_ready" in blockers


def test_drift_gate_false_blocks_treatment() -> None:
    gates = _gates()
    gates["drift_gate"] = False
    eligible, blockers = treatment_eligibility(_estimate(), gates, _security_ok())
    assert not eligible
    assert "GLOBAL_GATE_FALSE:drift_gate" in blockers


def test_qlib_lineage_false_blocks_treatment() -> None:
    gates = _gates()
    gates["qlib_lineage_gate"] = False
    eligible, blockers = treatment_eligibility(_estimate(), gates, _security_ok())
    assert not eligible
    assert "GLOBAL_GATE_FALSE:qlib_lineage_gate" in blockers


def test_trader_master_linkage_false_blocks_treatment() -> None:
    gates = _gates()
    gates["trader_master_linkage_gate"] = False
    eligible, blockers = treatment_eligibility(_estimate(), gates, _security_ok())
    assert not eligible
    assert "GLOBAL_GATE_FALSE:trader_master_linkage_gate" in blockers


def test_qlib_dependency_security_missing_blocks_fail_closed(tmp_path: Path) -> None:
    evidence = _read_qlib_security_evidence(tmp_path, "data/reports/missing.json")
    assert evidence["gate_passed"] is False
    assert evidence["status"] == "SOURCE_MISSING"


def test_qlib_dependency_security_blocked_blocks_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "data/reports/security.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "schema_version": "qlib_dependency_security_audit_v1",
                "status": "blocked",
                "reason": "upstream_constraint_blocked",
                "approved_security_clean_resolution_found": False,
                "qlib_security_gate_passed": False,
            }
        ),
        encoding="utf-8",
    )
    evidence = _read_qlib_security_evidence(tmp_path, target)
    assert evidence["gate_passed"] is False
    assert evidence["reason"] == "upstream_constraint_blocked"


def test_no_candidate_linkage_yields_zero_eligible_treatment(monkeypatch: pytest.MonkeyPatch) -> None:
    gates = _gates(False)
    report = _financial_report(linked=0, gates=gates)
    estimate = _estimate(
        candidate_id=None,
        candidate_linkage_status="CANDIDATE_UNLINKED",
        point_in_time_consumable=False,
        branch2_compatible=False,
        financial_estimate_trusted=False,
        candidate_ev=None,
        candidate_ev_status="BLOCKED_DRIFT",
    )
    _patch_engine_sources(monkeypatch, financial_report=report, estimates=[estimate], security=_security_blocked())
    result, _ = PaperABEdgeSelectorEngine(_config()).run(project_root=".", paper_db="paper.sqlite")
    assert result["candidate_linked_rows"] == 0
    assert result["eligible_treatment_count"] == 0


def test_no_valid_treatment_financial_evidence_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    gates = _gates(False)
    _patch_engine_sources(
        monkeypatch,
        financial_report=_financial_report(linked=0, gates=gates),
        estimates=[_estimate(candidate_id=None, financial_estimate_trusted=False, candidate_ev=None)],
        security=_security_blocked(),
    )
    result, _ = PaperABEdgeSelectorEngine(_config()).run(project_root=".", paper_db="paper.sqlite")
    assert result["financial_evidence"]["status"] == "EVIDENCE_BLOCKED"
    assert result["treatment_evaluable"] is False
    assert result["decision"] == DECISION


def test_software_dod_can_pass_while_financial_evidence_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    gates = _gates(False)
    _patch_engine_sources(
        monkeypatch,
        financial_report=_financial_report(linked=0, gates=gates),
        estimates=[],
        security=_security_blocked(),
    )
    result, _ = PaperABEdgeSelectorEngine(_config()).run(project_root=".", paper_db="paper.sqlite")
    assert result["software_dod"]["status"] == "PASS"
    assert result["financial_evidence"]["status"] == "EVIDENCE_BLOCKED"


def test_open_trades_excluded_and_authoritative_close_profit_abs_retained(monkeypatch: pytest.MonkeyPatch) -> None:
    import smartcrypto.research.paper_ab_edge_selector.engine as engine_module

    monkeypatch.setattr(
        engine_module,
        "read_authoritative_paper_source",
        lambda _path: {
            "path": Path("paper.sqlite"),
            "sha256_before": "A" * 64,
            "sha256_after": "A" * 64,
            "sqlite_integrity_check": "ok",
            "trades": _closed_trade_frame(),
            "orders": pd.DataFrame(),
        },
    )
    monkeypatch.setattr(
        engine_module.FinancialAIResearchEngine,
        "run",
        lambda self, **kwargs: (_financial_report(linked=0, gates=_gates(False)), []),
    )
    monkeypatch.setattr(engine_module, "_read_qlib_security_evidence", lambda root, value: _security_blocked())
    result, _ = PaperABEdgeSelectorEngine(_config()).run(project_root=".", paper_db="paper.sqlite")
    assert result["paper_baseline"]["closed_trade_count"] == 1
    assert result["paper_baseline"]["metrics"]["trade_count"] == 1
    assert result["paper_baseline"]["metrics"]["net_pnl"] == pytest.approx(1.25)
    assert result["paper_baseline"]["pnl_authority"] == "FREQTRADE_CLOSE_PROFIT_ABS"


def _bootstrap_frame(delta: float) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for day in range(30):
        ts = f"2026-07-{day + 1:02d}T00:00:00Z"
        rows.append({"arm": "CONTROL", "effective_arm_pnl_usdt": 0.0, "observed_at_utc": ts})
        rows.append({"arm": "TREATMENT", "effective_arm_pnl_usdt": delta, "observed_at_utc": ts})
    return pd.DataFrame(rows)


def test_deterministic_bootstrap() -> None:
    frame = _bootstrap_frame(1.0)
    first = deterministic_bootstrap_delta_expectancy(frame, iterations=300, seed=99, confidence_level=0.95)
    second = deterministic_bootstrap_delta_expectancy(frame, iterations=300, seed=99, confidence_level=0.95)
    assert first == second
    assert first["method"] == "temporal_cluster_day_bootstrap"


def test_bootstrap_ci_behavior_on_synthetic_positive_edge() -> None:
    result = deterministic_bootstrap_delta_expectancy(
        _bootstrap_frame(1.0), iterations=300, seed=11, confidence_level=0.95
    )
    assert result["ci_lower"] is not None
    assert result["ci_lower"] > 0


def test_synthetic_no_edge_does_not_pass_ci_gate() -> None:
    result = deterministic_bootstrap_delta_expectancy(
        _bootstrap_frame(0.0), iterations=300, seed=11, confidence_level=0.95
    )
    assert result["ci_lower"] == pytest.approx(0.0)


def test_minimum_sample_gate() -> None:
    frame = pd.DataFrame(
        [
            {"arm": "CONTROL", "effective_arm_pnl_usdt": 1.0, "observed_at_utc": "2026-08-01T00:00:00Z", "treatment_action": "ACCEPT"},
            {"arm": "TREATMENT", "effective_arm_pnl_usdt": 1.0, "observed_at_utc": "2026-08-02T00:00:00Z", "treatment_action": "ACCEPT"},
        ]
    )
    c, cr = _arm_metrics(frame.loc[frame["arm"].eq("CONTROL")], "CONTROL")
    t, tr = _arm_metrics(frame.loc[frame["arm"].eq("TREATMENT")], "TREATMENT")
    evidence = _financial_evidence(
        frame,
        control_metrics=c,
        control_raw=cr,
        treatment_metrics=t,
        treatment_raw=tr,
        config=_config(minimum_observations_per_arm=200, minimum_observation_days=0),
        global_blockers=[],
    )
    assert evidence.status == "INSUFFICIENT_SAMPLE"
    assert evidence.sample_gate_passed is False


def test_path_escape_outside_data_reports_blocked(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="output_must_be_under_data_reports"):
        resolve_report_path(tmp_path, tmp_path / "outside.json")
    with pytest.raises(ValueError, match="output_must_be_under_data_reports"):
        resolve_assignments_path(tmp_path, tmp_path / "outside.jsonl")


def test_default_no_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import smartcrypto.research.paper_ab_edge_selector.engine as engine_module

    monkeypatch.setattr(
        engine_module.PaperABEdgeSelectorEngine,
        "run",
        lambda self, **kwargs: (
            {
                "schema_version": "paper_ab_edge_selector_v1",
                "status": "BLOCKED",
                "reason": "test",
                "decision": DECISION,
                "financial_evidence": {"status": "EVIDENCE_BLOCKED"},
                **SAFETY_FLAGS,
                "write_requested": False,
                "write_performed": False,
                "write_report_performed": False,
                "write_assignments_performed": False,
                "assignments_appended": 0,
            },
            [],
        ),
    )
    result = build_paper_ab_edge_selector_v1(
        project_root=tmp_path,
        paper_db="ignored.sqlite",
        experiment_id="exp",
        bootstrap_iterations=100,
    )
    assert result["write_performed"] is False
    assert not (tmp_path / "data/reports/paper_ab_edge_selector_v1.json").exists()


def test_explicit_write_only_under_data_reports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import smartcrypto.research.paper_ab_edge_selector.engine as engine_module

    monkeypatch.setattr(
        engine_module.PaperABEdgeSelectorEngine,
        "run",
        lambda self, **kwargs: (
            {
                "schema_version": "paper_ab_edge_selector_v1",
                "status": "BLOCKED",
                "reason": "test",
                "decision": DECISION,
                "financial_evidence": {"status": "EVIDENCE_BLOCKED"},
                **SAFETY_FLAGS,
                "write_requested": False,
                "write_performed": False,
                "write_report_performed": False,
                "write_assignments_performed": False,
                "assignments_appended": 0,
            },
            [],
        ),
    )
    result = build_paper_ab_edge_selector_v1(
        project_root=tmp_path,
        paper_db="ignored.sqlite",
        experiment_id="exp",
        bootstrap_iterations=100,
        write_report_requested=True,
    )
    assert result["write_performed"] is True
    assert (tmp_path / "data/reports/paper_ab_edge_selector_v1.json").exists()


def test_assignment_jsonl_idempotent(tmp_path: Path) -> None:
    target = resolve_assignments_path(tmp_path)
    row = {"assignment_id": "ab-1", "arm": "CONTROL", "candidate_id": "c1"}
    assert write_assignments_idempotent(tmp_path, target, [row]) == 1
    assert write_assignments_idempotent(tmp_path, target, [row]) == 0
    assert len(target.read_text(encoding="utf-8").splitlines()) == 1


def test_assignment_jsonl_semantic_conflict_blocks(tmp_path: Path) -> None:
    target = resolve_assignments_path(tmp_path)
    write_assignments_idempotent(
        tmp_path, target, [{"assignment_id": "ab-1", "arm": "CONTROL", "candidate_id": "c1"}]
    )
    with pytest.raises(ValueError, match="assignment_id_conflict"):
        write_assignments_idempotent(
            tmp_path, target, [{"assignment_id": "ab-1", "arm": "TREATMENT", "candidate_id": "c1"}]
        )


def test_safety_flags_all_preserved() -> None:
    required_false = {
        "operational_authority",
        "writes_sqlite",
        "writes_runtime",
        "writes_active_signals",
        "writes_active_model",
        "writes_active_registry",
        "trains_active_model",
        "promotes_model",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "changes_strategy",
        "changes_risk",
        "changes_stake",
        "changes_leverage",
        "changes_max_open_trades",
        "sends_orders",
        "real_order_submission_enabled",
        "exchange_private_access",
        "live_release_allowed",
        "canary_release_allowed",
        "treatment_release_allowed",
    }
    assert SAFETY_FLAGS["paper_only"] is True
    assert SAFETY_FLAGS["shadow_only"] is True
    assert SAFETY_FLAGS["research_only"] is True
    assert SAFETY_FLAGS["read_only"] is True
    assert all(SAFETY_FLAGS[key] is False for key in required_false)


def test_no_freqtrade_riskmanager_or_order_adapter_imports() -> None:
    package_root = PROJECT_ROOT / "smartcrypto/research/paper_ab_edge_selector"
    forbidden = ("freqtrade", "riskmanager", "risk_manager", "order_adapter", "ccxt")
    for path in package_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append((node.module or "").lower())
        assert not any(any(token in item for token in forbidden) for item in imports), path


def test_cli_standalone_works_without_pythonpath() -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    process = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts/build_paper_ab_edge_selector_v1.py"), "--help"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0
    assert "--experiment-id" in process.stdout


def test_current_blocked_style_fixture_returns_manter_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    gates = _gates(False)
    estimates = [
        _estimate(
            candidate_id=None,
            candidate_linkage_status="CANDIDATE_UNLINKED",
            point_in_time_consumable=False,
            branch2_compatible=False,
            financial_estimate_trusted=False,
            candidate_ev=None,
            candidate_ev_status="BLOCKED_DRIFT",
        )
        for _ in range(216)
    ]
    report = _financial_report(linked=0, gates=gates)
    report["reason"] = "BLOCKED_DRIFT"
    report["blockers"] = [
        "BLOCKED_DRIFT",
        "REGRESSION_QUALITY_FAILED",
        "CLASSIFICATION_QUALITY_FAILED",
        "CALIBRATION_FAILED",
        "NON_MONOTONIC",
    ]
    report["candidate_estimates"] = {
        "estimate_count": 216,
        "trusted_estimate_count": 0,
        "candidate_ev_generated_count": 0,
        "candidate_ev_blocked_count": 216,
    }
    _patch_engine_sources(
        monkeypatch,
        financial_report=report,
        estimates=estimates,
        security=_security_blocked("upstream_constraint_blocked"),
    )
    result, assignments = PaperABEdgeSelectorEngine(_config()).run(
        project_root=".", paper_db="paper.sqlite"
    )
    assert result["candidate_linked_rows"] == 0
    assert result["eligible_treatment_count"] == 0
    assert result["financial_evidence"]["status"] == "EVIDENCE_BLOCKED"
    assert result["decision"] == "MANTER_BASELINE"
    assert result["treatment_release_allowed"] is False
    assert result["operational_authority"] is False
    assert result["sends_orders"] is False
    assert result["changes_risk"] is False
    assert result["write_performed"] is False
    assert assignments == []
