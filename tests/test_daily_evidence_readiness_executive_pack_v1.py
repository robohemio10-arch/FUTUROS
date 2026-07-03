from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.ops.daily_evidence_readiness_executive_pack import build_daily_evidence_readiness_executive_pack_v1


def safe_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "read_only": True,
        "informational_only": True,
        "operational_authority": False,
        "readiness_release_authority": False,
        "release_allowed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "changes_model": False,
        "model_promotion_performed": False,
        "registry_write_performed": False,
        "active_model_changed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "creates_scheduler": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }


def component_payloads(
    *,
    daily_status: str = "ok",
    daily_readiness_status: str = "ok",
    drift_status: str = "ok",
    drift_regime: str = "stable",
    drift_blockers: list[str] | None = None,
) -> dict[str, dict[str, object]]:
    flags = safe_flags()
    return {
        "qlib_environment_lock": {
            "status": "ok",
            "reason": "qlib_backend_available",
            "qlib_importable": True,
            "qlib_backend_status": "available",
            "qlib_version": "0.9.7",
            "environment_lock_status": "locked",
            **flags,
        },
        "qlib_backend_gate": {
            "status": "ok",
            "reason": "qlib_backend_available",
            "qlib_importable": True,
            "qlib_backend_status": "available",
            "qlib_version": "0.9.7",
            "dependency_contract_hash": "dependency-hash",
            **flags,
        },
        "paper_autotrain": {
            "schema_version": "paper_autotrain_feedback_loop_v1",
            "status": "ok",
            "reason": "research_candidate_not_promoted",
            "decision": "MANTER_EM_RESEARCH",
            "blockers": [],
            "warnings": [],
            "lineage_hashes": {"dataset_hash": "dataset-hash"},
            "write_performed": False,
            "run_qlib_train_requested": False,
            "run_ai_shadow_train_requested": False,
            **flags,
        },
        "daily_learning_readiness": {
            "schema_version": "daily_learning_evidence_readiness_integration_v1",
            "status": daily_status,
            "reason": "daily_learning_evidence_readiness_integration_blocked_informational_only",
            "decision": "MANTER_EM_RESEARCH",
            "readiness_status": daily_readiness_status,
            "readiness_summary": {"release_allowed": False},
            "gate_summary": {"critical_failed_gate_ids": []},
            "validation_errors": [],
            **flags,
        },
        "ai_qlib_drift_regime": {
            "schema_version": "ai_qlib_drift_regime_monitor_v1",
            "status": drift_status,
            "reason": "drift_regime_stable_research_only",
            "decision": "MANTER_EM_RESEARCH",
            "blockers": drift_blockers or [],
            "warnings": [],
            "lineage_hashes": {"feature_contract_hash": "feature-hash", "dataset_hash": "dataset-hash"},
            "regime_summary": {"overall_regime": drift_regime},
            "drift_summary": {"critical_drift_detected": drift_status == "blocked"},
            "write_performed": False,
            **flags,
        },
    }


def test_default_does_not_write(tmp_path: Path) -> None:
    report = build_daily_evidence_readiness_executive_pack_v1(
        project_root=tmp_path,
        component_payloads=component_payloads(),
    )
    assert report["status"] == "ok"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "daily_evidence_readiness_executive_pack_v1.json").exists()


def test_write_report_writes_only_allowed_files(tmp_path: Path) -> None:
    report = build_daily_evidence_readiness_executive_pack_v1(
        project_root=tmp_path,
        write_report=True,
        component_payloads=component_payloads(),
    )
    assert report["write_performed"] is True
    reports = tmp_path / "data" / "reports"
    assert (reports / "daily_evidence_readiness_executive_pack_v1.json").exists()
    assert (reports / "daily_evidence_readiness_executive_pack_v1.md").exists()
    assert (reports / "daily_evidence_readiness_executive_pack_v1.html").exists()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "data" / "models").exists()


def test_cli_no_write_precedence_over_write_report(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_daily_evidence_readiness_executive_pack_v1.py",
            "--project-root",
            str(tmp_path),
            "--write-report",
            "--no-write",
            "--json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "daily_evidence_readiness_executive_pack_v1.json").exists()


def test_missing_existing_reports_are_readonly_inputs_not_runtime_writes(tmp_path: Path) -> None:
    report = build_daily_evidence_readiness_executive_pack_v1(
        project_root=tmp_path,
        component_payloads=component_payloads(),
    )
    assert all({"exists", "source_id", "relative_path"}.issubset(source) for source in report["input_sources"])
    assert {source["source"] for source in report["input_sources"]} == {"internal_no_write_builder"}
    assert report["write_performed"] is False


def test_drift_blocked_propagates_status_blocked(tmp_path: Path) -> None:
    report = build_daily_evidence_readiness_executive_pack_v1(
        project_root=tmp_path,
        component_payloads=component_payloads(
            drift_status="blocked",
            drift_regime="unstable",
            drift_blockers=["qlib_rank_ic_drift_critical"],
        ),
    )
    assert report["status"] == "blocked"
    assert "ai_qlib_drift_regime_section:blocked" in report["blockers"]
    assert "ai_qlib_drift_regime_section:qlib_rank_ic_drift_critical" in report["blockers"]
    assert report["decision"] == "MANTER_EM_RESEARCH"


def test_readiness_blocked_propagates_status_blocked(tmp_path: Path) -> None:
    report = build_daily_evidence_readiness_executive_pack_v1(
        project_root=tmp_path,
        component_payloads=component_payloads(daily_status="blocked", daily_readiness_status="blocked"),
    )
    assert report["status"] == "blocked"
    assert "daily_learning_readiness_section:daily_learning_readiness_blocked" in report["blockers"]
    assert report["executive_summary"]["daily_readiness_status"] == "blocked"


def test_decision_and_release_authority_are_always_research_only(tmp_path: Path) -> None:
    report = build_daily_evidence_readiness_executive_pack_v1(
        project_root=tmp_path,
        component_payloads=component_payloads(),
    )
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["release_allowed"] is False
    assert report["readiness_release_authority"] is False
    assert report["operational_authority"] is False
    assert report["executive_summary"]["manual_go_no_go_required"] is True


def test_no_operational_safety_flag_is_true(tmp_path: Path) -> None:
    report = build_daily_evidence_readiness_executive_pack_v1(
        project_root=tmp_path,
        component_payloads=component_payloads(),
    )
    for key, value in report["safety_flags"].items():
        if key in {"paper_only", "shadow_only", "research_only", "read_only", "informational_only"}:
            assert value is True
        else:
            assert value is False


def test_html_has_no_remote_script_or_http(tmp_path: Path) -> None:
    build_daily_evidence_readiness_executive_pack_v1(
        project_root=tmp_path,
        write_report=True,
        component_payloads=component_payloads(),
    )
    html = (tmp_path / "data" / "reports" / "daily_evidence_readiness_executive_pack_v1.html").read_text(
        encoding="utf-8"
    )
    assert "Informational only" in html
    assert "<script" not in html.lower()
    assert "http://" not in html.lower()
    assert "https://" not in html.lower()


def test_output_json_is_serializable(tmp_path: Path) -> None:
    report = build_daily_evidence_readiness_executive_pack_v1(
        project_root=tmp_path,
        component_payloads=component_payloads(),
    )
    payload = json.loads(json.dumps(report, sort_keys=True, default=str))
    assert payload["schema_version"] == "daily_evidence_readiness_executive_pack_v1"
