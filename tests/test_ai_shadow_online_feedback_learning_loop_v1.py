from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.ai_shadow_online_feedback_learning_loop import (
    SAFETY_FLAGS,
    AIShadowFeedbackLoopConfig,
    build_feedback_events,
    collect_feedback_evidence,
    evaluate_learning_gate,
    resolve_paths,
    run_ai_shadow_online_feedback_learning_loop,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_ai_shadow_online_feedback_learning_loop.py"
MODULE = ROOT / "smartcrypto" / "research" / "ai_shadow_online_feedback_learning_loop.py"


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def populate_evidence(project_root: Path) -> None:
    paths = resolve_paths(project_root)
    write_json(
        paths.training_summary_path,
        {
            "status": "warning",
            "reason": "selector_does_not_beat_all_test_baseline",
            "decision": "MANTER_EM_RESEARCH",
            "aggregate_metrics": {
                "selected_net_pnl": 227.07,
                "all_test_net_pnl": 503.16,
            },
            "paper_only": True,
            "shadow_only": True,
            "sends_orders": False,
            "changes_risk": False,
        },
    )
    write_json(
        paths.executive_pack_path,
        {
            "status": "warning",
            "reason": "evidence_consolidated_no_promotion",
            "decision": "MANTER_EM_RESEARCH",
            "paper_only": True,
            "shadow_only": True,
            "live_trading_enabled": False,
        },
    )
    write_json(
        paths.shadow_candidate_report_path,
        {
            "status": "warning",
            "reason": "candidate_registered_research_only",
            "decision": "MANTER_EM_RESEARCH",
            "promotion_status": "blocked",
            "promotion_eligible": False,
            "paper_only": True,
            "shadow_only": True,
            "registers_model": False,
        },
    )
    write_json(
        paths.shadow_candidate_registry_path,
        {
            "registry_scope": "research_shadow_only",
            "champion_model_id": None,
            "paper_only": True,
            "shadow_only": True,
        },
    )
    write_json(
        paths.outcome_attribution_report_path,
        {
            "status": "ok",
            "reason": "outcomes_attributed",
            "paper_only": True,
            "shadow_only": True,
        },
    )
    write_json(
        paths.financial_threshold_report_path,
        {
            "status": "ok",
            "reason": "thresholds_evaluated",
            "paper_only": True,
            "shadow_only": True,
        },
    )
    write_json(
        paths.drift_monitor_report_path,
        {
            "status": "ok",
            "reason": "drift_observed",
            "paper_only": True,
            "shadow_only": True,
        },
    )
    write_json(
        paths.incremental_trainer_report_path,
        {
            "status": "ok",
            "reason": "challenger_trained_shadow_only",
            "promotion_status": "pending",
            "sample_warning": True,
            "paper_only": True,
            "shadow_only": True,
            "runs_training": False,
        },
    )


def run_loop(project_root: Path, *, write: bool = False, strict: bool = False):
    populate_evidence(project_root)
    return run_ai_shadow_online_feedback_learning_loop(
        resolve_paths(project_root),
        AIShadowFeedbackLoopConfig(strict=strict),
        write=write,
        analysis_date_utc="2026-06-23T12:00:00Z",
    )


def test_no_write_is_default_contract_and_materializes_nothing(tmp_path: Path) -> None:
    result = run_loop(tmp_path)
    paths = resolve_paths(tmp_path)
    assert result.report["write_requested"] is False
    assert result.report["write_performed"] is False
    assert not paths.report_output_path.exists()
    assert not paths.events_output_path.exists()


def test_current_evidence_keeps_feedback_record_only(tmp_path: Path) -> None:
    report = run_loop(tmp_path).report
    assert report["status"] == "warning"
    assert report["reason"] == "feedback_recorded_without_training"
    assert report["loop_status"] == "research_feedback_only"
    assert report["learning_action"] == "record_only"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["promotion_status"] == "blocked"
    assert report["training_allowed"] is False
    assert report["promotion_allowed"] is False


def test_gate_records_all_expected_blockers(tmp_path: Path) -> None:
    populate_evidence(tmp_path)
    gate = evaluate_learning_gate(
        collect_feedback_evidence(resolve_paths(tmp_path)),
        AIShadowFeedbackLoopConfig(),
    )
    expected = {
        "branch04_kept_in_research",
        "branch04_selected_not_above_all_test",
        "branch05_kept_in_research",
        "branch06_promotion_blocked",
        "branch06_not_promotion_eligible",
        "ai_shadow_trainer_pending_not_approved",
        "missing_ai_shadow_threshold_readiness_report",
        "missing_ai_shadow_decision_logger_report",
        "missing_ai_shadow_outcome_tracker_report",
        "research_feedback_scope_forbids_training",
    }
    assert expected <= set(gate["learning_blockers"])


def test_feedback_events_have_stable_ids_and_record_only_action(tmp_path: Path) -> None:
    populate_evidence(tmp_path)
    evidence = collect_feedback_evidence(resolve_paths(tmp_path))
    gate = evaluate_learning_gate(evidence, AIShadowFeedbackLoopConfig())
    first = build_feedback_events(evidence, gate, "2026-06-23T12:00:00Z")
    second = build_feedback_events(evidence, gate, "2026-06-24T12:00:00Z")
    assert [event["event_id"] for event in first] == [event["event_id"] for event in second]
    expected_types = {
        "branch04_supervised_result_observed",
        "branch05_executive_pack_observed",
        "branch06_shadow_candidate_registered",
        "ai_shadow_outcome_attribution_observed",
        "ai_shadow_financial_thresholds_observed",
        "ai_shadow_drift_monitor_observed",
        "ai_shadow_incremental_trainer_observed",
        "learning_gate_blocked",
        "recommended_next_actions_recorded",
    }
    assert expected_types <= {event["event_type"] for event in first}
    assert all(event["action_taken"] == "record_only" for event in first)
    assert all(event["sends_orders"] is False for event in first)
    assert all(event["changes_risk"] is False for event in first)
    assert all(event["runs_training"] is False for event in first)


def test_write_materializes_only_report_and_idempotent_events(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)
    first = run_loop(tmp_path, write=True)
    second = run_loop(tmp_path, write=True)
    report = json.loads(paths.report_output_path.read_text(encoding="utf-8"))
    event_lines = paths.events_output_path.read_text(encoding="utf-8").splitlines()
    assert first.report["write_performed"] is True
    assert first.report["new_events_written"] == len(first.events)
    assert second.report["new_events_written"] == 0
    assert len(event_lines) == len(first.events)
    assert report["write_performed"] is True


def test_missing_optional_sources_are_auditable_without_false_ok(tmp_path: Path) -> None:
    report = run_loop(tmp_path).report
    assert report["status"] == "warning"
    assert set(report["missing_sources"]) == {
        "threshold_readiness_report",
        "decision_logger_report",
        "outcome_tracker_report",
    }
    assert "missing_source:threshold_readiness_report" in report["warnings"]


def test_missing_critical_source_blocks_loop(tmp_path: Path) -> None:
    populate_evidence(tmp_path)
    resolve_paths(tmp_path).training_summary_path.unlink()
    result = run_ai_shadow_online_feedback_learning_loop(
        resolve_paths(tmp_path),
        AIShadowFeedbackLoopConfig(),
        analysis_date_utc="2026-06-23T12:00:00Z",
    )
    assert result.report["status"] == "blocked"
    assert result.report["critical_missing_sources"] == ["branch04_training_summary"]


def test_strict_mode_blocks_even_with_noncritical_missing_sources(tmp_path: Path) -> None:
    assert run_loop(tmp_path, strict=True).report["status"] == "blocked"


def test_unsafe_source_flag_is_never_accepted(tmp_path: Path) -> None:
    populate_evidence(tmp_path)
    paths = resolve_paths(tmp_path)
    payload = json.loads(paths.outcome_attribution_report_path.read_text(encoding="utf-8"))
    payload["sends_orders"] = True
    write_json(paths.outcome_attribution_report_path, payload)
    result = run_ai_shadow_online_feedback_learning_loop(
        paths,
        AIShadowFeedbackLoopConfig(),
        analysis_date_utc="2026-06-23T12:00:00Z",
    )
    assert any(
        blocker == "unsafe_safety_flag:outcome_attribution_report:sends_orders=true"
        for blocker in result.report["learning_blockers"]
    )
    assert result.report["sends_orders"] is False


def test_invalid_optional_json_is_reported_without_traceback(tmp_path: Path) -> None:
    populate_evidence(tmp_path)
    paths = resolve_paths(tmp_path)
    paths.threshold_readiness_report_path.parent.mkdir(parents=True, exist_ok=True)
    paths.threshold_readiness_report_path.write_text("{broken", encoding="utf-8")
    result = run_ai_shadow_online_feedback_learning_loop(
        paths,
        AIShadowFeedbackLoopConfig(),
        analysis_date_utc="2026-06-23T12:00:00Z",
    )
    assert result.report["status"] == "warning"
    assert result.report["load_errors"] == [
        "threshold_readiness_report:JSONDecodeError"
    ]
    assert "missing_ai_shadow_threshold_readiness_report" in result.report["learning_blockers"]


def test_cli_runs_no_write_with_controlled_json(tmp_path: Path) -> None:
    populate_evidence(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert payload["status"] == "warning"
    assert payload["write_performed"] is False
    assert not resolve_paths(tmp_path).report_output_path.exists()


def test_module_and_cli_do_not_import_or_invoke_operational_components() -> None:
    forbidden_imports = (
        "train_ai_shadow_incremental_model",
        "smartcrypto.ml.ai_shadow",
        "sqlite3",
        "freqtrade",
        "ccxt",
        "subprocess",
    )
    for source in (MODULE, SCRIPT):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        assert all(
            not any(name.startswith(fragment) for fragment in forbidden_imports)
            for name in imports
        )


def test_protected_runtime_artifacts_are_unchanged(tmp_path: Path) -> None:
    protected = [
        tmp_path / "data" / "trades" / "trades_master.xlsx",
        tmp_path / "data" / "features" / "training_dataset.parquet",
        tmp_path / "data" / "runtime" / "ai_shadow_filter_decisions.sqlite",
        tmp_path / "data" / "models" / "production_model.joblib",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"sentinel")
    run_loop(tmp_path, write=True)
    assert all(path.read_bytes() == b"sentinel" for path in protected)


def test_all_declared_safety_flags_are_preserved(tmp_path: Path) -> None:
    report = run_loop(tmp_path).report
    for name, expected in SAFETY_FLAGS.items():
        assert report[name] is expected
