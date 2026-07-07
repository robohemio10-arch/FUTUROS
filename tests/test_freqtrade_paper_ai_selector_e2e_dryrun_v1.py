from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.freqtrade_paper_ai_selector_e2e_dryrun.selector_dryrun import (
    build_freqtrade_paper_ai_selector_e2e_dryrun_v1,
)


SCRIPT = Path("scripts/build_freqtrade_paper_ai_selector_e2e_dryrun_v1.py")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def blocked_signal_candidate(candidate_id: str = "signal-1") -> dict:
    return {
        "signal_candidate_id": candidate_id,
        "source_candidate_id": "candidate-1",
        "symbol_scope": ["BTCUSDT"],
        "side_scope": ["long"],
        "regime_scope": ["normal"],
        "signal_direction": "long",
        "signal_confidence": 0.55,
        "signal_actionability": "blocked",
        "blocked_reasons": ["blocked_drift_gate", "blocked_execution_cost_gate"],
        "eligible_for_paper_selector": False,
        "eligible_for_freqtrade": False,
        "writes_runtime": False,
        "updates_freqtrade": False,
        "sends_orders": False,
    }


def producer_payload(*, actionable_count: int = 0, candidate: dict | None = None) -> dict:
    rows = [candidate or blocked_signal_candidate()]
    return {
        "schema_version": "paper_ai_signal_candidate_producer_v1",
        "status": "blocked" if actionable_count == 0 else "ok",
        "reason": "no_registry_eligible_candidates" if actionable_count == 0 else "signal_candidates_research_observation_only",
        "decision": "MANTER_EM_RESEARCH",
        "signal_candidate_count": len(rows),
        "actionable_signal_candidate_count": actionable_count,
        "blocked_signal_candidate_count": len(rows) - actionable_count,
        "signal_candidates": rows,
        "blockers": ["drift_gate_blocked", "execution_cost_gate_blocked"],
        "lineage_hashes": {"feature_contract_hash": "feature-hash"},
        "writes_signal_file": False,
        "sends_orders": False,
    }


def evidence_payloads(*, actionable_count: int = 0, candidate: dict | None = None) -> dict[str, dict]:
    return {
        "signal_candidate_report": producer_payload(actionable_count=actionable_count, candidate=candidate),
        "registry_gate": {"status": "blocked", "registry_gate_status": "blocked_no_eligible_candidates"},
    }


def test_report_is_json_serializable(tmp_path: Path) -> None:
    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )

    encoded = json.dumps(report, sort_keys=True)
    assert "freqtrade_paper_ai_selector_e2e_dryrun_v1" in encoded


def test_safety_flags_disable_runtime_model_registry_orders_freqtrade_and_signal_file(tmp_path: Path) -> None:
    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
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
    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
        write=True,
    )

    assert report["write_requested"] is True
    assert report["write_performed"] is False
    assert not list(tmp_path.rglob("*active_freqtrade_signals.json"))
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))
    assert not (tmp_path / "freqtrade" / "user_data").exists()


def test_cli_write_writes_only_json_and_markdown_under_data_reports(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--write", "--json"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    json_report = tmp_path / "data" / "reports" / "freqtrade_paper_ai_selector_e2e_dryrun_v1.json"
    markdown_report = tmp_path / "data" / "reports" / "freqtrade_paper_ai_selector_e2e_dryrun_v1.md"

    assert payload["write_performed"] is True
    assert json_report.is_file()
    assert markdown_report.is_file()
    assert not list(tmp_path.rglob("*active_freqtrade_signals.json"))
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not list(tmp_path.rglob("*.parquet"))
    assert not (tmp_path / "freqtrade" / "user_data").exists()


def test_missing_primary_source_blocks_without_exception(tmp_path: Path) -> None:
    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(project_root=tmp_path, evidence_payloads={})

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_signal_candidate_report"
    assert report["selector_dryrun_status"] == "blocked_missing_signal_candidate_report"
    assert report["selected_signal_count"] == 0


def test_producer_blocked_generates_blocked_no_actionable_candidates(tmp_path: Path) -> None:
    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "no_actionable_signal_candidates"
    assert report["selector_dryrun_status"] == "blocked_no_actionable_candidates"
    assert report["producer_status"] == "blocked"


def test_zero_actionable_candidates_returns_blocked(tmp_path: Path) -> None:
    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(actionable_count=0),
    )

    assert report["actionable_signal_candidate_count"] == 0
    assert report["status"] == "blocked"
    assert report["reason"] == "no_actionable_signal_candidates"


def test_blocked_signal_candidate_never_becomes_eligible_for_freqtrade(tmp_path: Path) -> None:
    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )
    decision = report["selector_decisions"][0]

    assert decision["selector_action"] == "reject"
    assert decision["eligible_for_paper_selector"] is False
    assert decision["eligible_for_freqtrade"] is False
    assert decision["would_write_active_signal"] is False


def test_selector_decisions_reject_all_blocked_candidates(tmp_path: Path) -> None:
    first = blocked_signal_candidate("signal-1")
    second = blocked_signal_candidate("signal-2")
    payload = producer_payload()
    payload["signal_candidate_count"] = 2
    payload["blocked_signal_candidate_count"] = 2
    payload["signal_candidates"] = [first, second]

    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads={"signal_candidate_report": payload},
    )

    assert report["selected_signal_count"] == 0
    assert report["rejected_signal_count"] == 2
    assert all(decision["selector_action"] == "reject" for decision in report["selector_decisions"])


def test_drift_blocked_blocks_dryrun_decision(tmp_path: Path) -> None:
    candidate = blocked_signal_candidate()
    candidate["blocked_reasons"] = ["drift_gate_blocked"]

    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(candidate=candidate),
    )

    assert "drift_gate_blocked" in report["blockers"]
    assert "drift_gate_blocked" in report["selector_decisions"][0]["blocked_reasons"]


def test_execution_cost_blocked_blocks_dryrun_decision(tmp_path: Path) -> None:
    candidate = blocked_signal_candidate()
    candidate["blocked_reasons"] = ["execution_cost_gate_blocked"]

    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(candidate=candidate),
    )

    assert "execution_cost_gate_blocked" in report["blockers"]
    assert "execution_cost_gate_blocked" in report["selector_decisions"][0]["blocked_reasons"]


def test_forbidden_outcome_fields_are_not_used_for_operational_selection(tmp_path: Path) -> None:
    candidate = blocked_signal_candidate()
    candidate["target_label"] = 1
    candidate["realized_pnl"] = 10.0
    candidate["expected_value_realized"] = 0.2

    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(candidate=candidate),
    )

    decision = report["selector_decisions"][0]
    assert decision["selector_action"] == "reject"
    assert "forbidden_operational_field_present" in decision["blocked_reasons"]
    assert decision["active_signal_payload"] is None


def test_decision_always_manter_em_research(tmp_path: Path) -> None:
    report = build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )

    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["release_allowed"] is False
    assert report["selector_operational_authority"] is False


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
    assert payload["writes_signal_file"] is False
    assert payload["writes_active_freqtrade_signals"] is False
    assert payload["active_signal_file_written"] is False
    assert payload["sends_orders"] is False
    assert payload["exchange_private_access"] is False


def test_active_freqtrade_signals_not_created(tmp_path: Path) -> None:
    build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
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

    build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )

    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert sorted(path.name for path in user_data.iterdir()) == ["sentinel.txt"]


def test_domain_does_not_import_runtime_engines(tmp_path: Path) -> None:
    forbidden_modules = ("freqtrade", "ccxt")
    for module_name in forbidden_modules:
        sys.modules.pop(module_name, None)

    module = importlib.import_module("smartcrypto.learning.freqtrade_paper_ai_selector_e2e_dryrun.selector_dryrun")
    module.build_freqtrade_paper_ai_selector_e2e_dryrun_v1(
        project_root=tmp_path,
        evidence_payloads=evidence_payloads(),
    )

    for module_name in forbidden_modules:
        assert module_name not in sys.modules
