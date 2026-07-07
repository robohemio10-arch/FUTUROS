from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.paper_ai_qlib_autotrain_activation_closeout.activation_closeout import (
    build_paper_ai_qlib_autotrain_activation_closeout_v1,
)


SCRIPT = Path("scripts/build_paper_ai_qlib_autotrain_activation_closeout_v1.py")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def registry_payload(*, gate_status: str = "ok_research_review_only", eligible: int = 1) -> dict:
    return {
        "status": "ok" if gate_status == "ok_research_review_only" else "blocked",
        "registry_gate_status": gate_status,
        "candidate_count": 4,
        "eligible_candidate_count": eligible,
        "blockers": [] if eligible else ["blocked_drift_gate", "blocked_execution_cost_gate"],
    }


def producer_payload(*, status: str = "ok", actionable: int = 1) -> dict:
    return {
        "status": status,
        "reason": "signal_candidates_research_observation_only" if status == "ok" else "no_registry_eligible_candidates",
        "signal_candidate_count": 4,
        "actionable_signal_candidate_count": actionable,
        "blocked_signal_candidate_count": 4 - actionable,
        "blockers": [] if actionable else ["drift_gate_blocked", "execution_cost_gate_blocked"],
        "lineage_hashes": {"feature_contract_hash": "feature-hash"},
    }


def selector_payload(*, status: str = "ok", selector_status: str = "ok_dryrun_observation_only", selected: int = 1) -> dict:
    return {
        "status": status,
        "reason": "selector_dryrun_observe_only" if status == "ok" else "no_actionable_signal_candidates",
        "selector_dryrun_status": selector_status,
        "selected_signal_count": selected,
        "rejected_signal_count": 4 - selected,
        "blockers": [] if selected else ["all_selector_decisions_rejected"],
    }


def evidence_payloads() -> dict[str, dict]:
    return {
        "registry_gate": registry_payload(),
        "signal_producer": producer_payload(),
        "selector_dryrun": selector_payload(),
        "autotrain_feedback_loop": {"status": "ok", "decision": "MANTER_EM_RESEARCH"},
        "qlib_trainer": {"status": "ok", "trainer_status": "ok"},
        "ai_shadow_quality_veto": {"status": "ok", "trainer_status": "ok"},
        "drift_monitor": {"status": "ok", "blockers": []},
        "execution_cost_gate": {"status": "ok", "blockers": []},
        "monte_carlo_gate": {"status": "ok", "blockers": []},
        "readiness_snapshot": {"status": "ok", "blockers": []},
    }


def test_report_is_json_serializable(tmp_path: Path) -> None:
    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )

    encoded = json.dumps(report, sort_keys=True)
    assert "paper_ai_qlib_autotrain_activation_closeout_v1" in encoded


def test_safety_flags_disable_runtime_model_registry_orders_freqtrade_signal_scheduler_and_autotrain(
    tmp_path: Path,
) -> None:
    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    for key, value in report["safety_flags"].items():
        if key in {"research_only", "paper_only", "shadow_only", "dry_run_only", "read_only"}:
            assert value is True
        else:
            assert value is False
        assert report[key] == value


def test_domain_no_write_does_not_materialize_files(tmp_path: Path) -> None:
    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
        write=True,
    )

    assert report["write_requested"] is True
    assert report["write_performed"] is False
    assert not list(tmp_path.rglob("*active_freqtrade_signals.json"))
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.joblib"))


def test_cli_write_writes_only_json_and_markdown_under_data_reports(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--write", "--json"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "paper_ai_qlib_autotrain_activation_closeout_v1.json").is_file()
    assert (tmp_path / "data" / "reports" / "paper_ai_qlib_autotrain_activation_closeout_v1.md").is_file()
    assert not list(tmp_path.rglob("*active_freqtrade_signals.json"))
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))


def test_missing_branch_59_source_blocks(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads.pop("registry_gate")

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_required_activation_sources"
    assert any("paper_model_candidate_registry_gate_v1.json" in item for item in report["blockers"])


def test_missing_branch_60_source_blocks(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads.pop("signal_producer")

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_required_activation_sources"
    assert any("paper_ai_signal_candidate_producer_v1.json" in item for item in report["blockers"])


def test_missing_branch_61_source_blocks(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads.pop("selector_dryrun")

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_required_activation_sources"
    assert any("freqtrade_paper_ai_selector_e2e_dryrun_v1.json" in item for item in report["blockers"])


def test_registry_gate_blocked_blocks_activation(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads["registry_gate"] = registry_payload(gate_status="blocked_no_eligible_candidates", eligible=0)

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert report["reason"] == "blocked_no_actionable_ai_signal_path"
    assert "registry_gate_not_ok_research_review_only" in report["blockers"]


def test_zero_eligible_candidates_blocks_activation(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads["registry_gate"] = registry_payload(eligible=0)

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert "no_model_candidate_eligible" in report["blockers"]
    assert report["activation_allowed"] is False


def test_signal_producer_blocked_blocks_activation(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads["signal_producer"] = producer_payload(status="blocked", actionable=0)

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert any(item.startswith("signal_producer_status_not_ok") for item in report["blockers"])
    assert report["reason"] == "blocked_no_actionable_ai_signal_path"


def test_zero_actionable_signal_candidates_blocks_activation(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads["signal_producer"] = producer_payload(actionable=0)

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert "no_actionable_signal_candidates" in report["blockers"]
    assert report["activation_allowed"] is False


def test_selector_dryrun_blocked_blocks_activation(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads["selector_dryrun"] = selector_payload(status="blocked", selector_status="blocked_no_actionable_candidates", selected=0)

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert any(item.startswith("selector_dryrun_not_ok") for item in report["blockers"])
    assert report["activation_allowed"] is False


def test_zero_selected_signals_blocks_activation(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads["selector_dryrun"] = selector_payload(selected=0)

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert "no_selected_signals" in report["blockers"]
    assert report["activation_allowed"] is False


def test_drift_blocked_blocks_activation(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads["drift_monitor"] = {"status": "blocked", "blockers": ["drift_gate_blocked"]}

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert "drift_gate_blocked" in report["blockers"]
    assert report["activation_allowed"] is False


def test_execution_cost_blocked_blocks_activation(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads["execution_cost_gate"] = {"status": "blocked", "blockers": ["execution_cost_gate_blocked"]}

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert "execution_cost_gate_blocked" in report["blockers"]
    assert report["activation_allowed"] is False


def test_missing_monte_carlo_or_readiness_does_not_release_activation(tmp_path: Path) -> None:
    payloads = evidence_payloads()
    payloads.pop("monte_carlo_gate")
    payloads.pop("readiness_snapshot")

    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(project_root=tmp_path, evidence_payloads=payloads)

    assert "missing_monte_carlo_gate" in report["blockers"]
    assert "missing_readiness_snapshot" in report["blockers"]
    assert report["activation_allowed"] is False


def test_decision_always_manter_em_research(tmp_path: Path) -> None:
    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["operational_authority"] is False
    assert report["autotrain_operational_activation"] is False


def test_cli_real_project_no_write_executes_without_runtime_mutation() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", ".", "--json"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["write_performed"] is False
    assert payload["autotrain_operational_activation"] is False
    assert payload["paper_selector_runtime_enabled"] is False
    assert payload["active_signal_file_written"] is False
    assert payload["writes_active_freqtrade_signals"] is False
    assert payload["sends_orders"] is False


def test_active_freqtrade_signals_not_created(tmp_path: Path) -> None:
    build_paper_ai_qlib_autotrain_activation_closeout_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
        write=True,
    )

    assert not list(tmp_path.rglob("active_freqtrade_signals.json"))


def test_freqtrade_user_data_not_altered(tmp_path: Path) -> None:
    user_data = tmp_path / "freqtrade" / "user_data"
    user_data.mkdir(parents=True)
    sentinel = user_data / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")

    build_paper_ai_qlib_autotrain_activation_closeout_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )

    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert sorted(path.name for path in user_data.iterdir()) == ["sentinel.txt"]


def test_runtime_engines_are_not_imported_by_domain(tmp_path: Path) -> None:
    forbidden_modules = ("freqtrade", "ccxt")
    for module_name in forbidden_modules:
        sys.modules.pop(module_name, None)

    module = importlib.import_module("smartcrypto.learning.paper_ai_qlib_autotrain_activation_closeout.activation_closeout")
    module.build_paper_ai_qlib_autotrain_activation_closeout_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )

    for module_name in forbidden_modules:
        assert module_name not in sys.modules


def test_no_scheduler_or_training_is_executed(tmp_path: Path) -> None:
    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )

    assert report["runs_training"] is False
    assert report["runs_autotrain"] is False
    assert report["scheduler_registered"] is False
    assert report["creates_cron"] is False
    assert report["creates_systemd_timer"] is False
    assert report["creates_windows_task"] is False
    assert report["creates_service"] is False
    assert report["starts_service"] is False


def test_no_registry_or_model_artifact_is_written(tmp_path: Path) -> None:
    report = build_paper_ai_qlib_autotrain_activation_closeout_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
        write=True,
    )

    assert report["registry_write_performed"] is False
    assert report["writes_model_artifact"] is False
    assert not list(tmp_path.rglob("*.joblib"))
