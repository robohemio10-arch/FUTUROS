from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.research.qlib_ocr_v11_shadow_candidate_registry import (
    SAFETY_FLAGS,
    ShadowCandidateRegistryConfig,
    build_candidate_identity,
    evaluate_candidate_gate,
    resolve_paths,
    run_qlib_ocr_v11_shadow_candidate_registry,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "register_qlib_ocr_v11_shadow_candidate.py"
MODULE = ROOT / "smartcrypto" / "research" / "qlib_ocr_v11_shadow_candidate_registry.py"
EXISTING_REGISTRY_MODULE = ROOT / "smartcrypto" / "ml" / "model_registry.py"


def training_summary(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "warning",
        "reason": "selector_does_not_beat_all_test_baseline",
        "decision": "MANTER_EM_RESEARCH",
        "training_rows": 2826,
        "prediction_rows": 2355,
        "feature_count": 14,
        "model_family_effective": "lightgbm",
        "suspicious_perfect_metrics": False,
        "model_exported": True,
        "registers_model": False,
        "production_enabled": False,
        "updates_qlib_runtime": False,
        "paper_only": True,
        "shadow_only": True,
        "sends_orders": False,
        "changes_risk": False,
        "exchange_private_access": False,
        "aggregate_metrics": {
            "valid_folds": 5,
            "mean_accuracy": 0.5910828025477708,
            "mean_f1": 0.7058819403886705,
            "mean_roc_auc": 0.5227010867442013,
            "all_test_net_pnl": 503.1625767999999,
            "selected_net_pnl": 227.0726756,
            "selected_rows": 771,
        },
    }
    payload.update(overrides)
    return payload


def executive_pack(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "warning",
        "reason": "evidence_consolidated_no_promotion",
        "decision": "MANTER_EM_RESEARCH",
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "updates_qlib_runtime": False,
        "registers_model": False,
        "auto_promote": False,
        "production_enabled": False,
    }
    payload.update(overrides)
    return payload


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_project(tmp_path: Path, *, include_model: bool = True) -> tuple[Path, Path, Path]:
    paths = resolve_paths(tmp_path)
    write_json(paths.training_summary_path, training_summary())
    write_json(paths.executive_pack_path, executive_pack())
    if include_model:
        paths.model_path.parent.mkdir(parents=True, exist_ok=True)
        paths.model_path.write_bytes(b"opaque-model-artifact\x00not-deserialized")
    return paths.training_summary_path, paths.executive_pack_path, paths.model_path


def test_registers_research_only_candidate_from_warning_inputs(tmp_path: Path) -> None:
    write_project(tmp_path)
    result = run_qlib_ocr_v11_shadow_candidate_registry(
        resolve_paths(tmp_path),
        ShadowCandidateRegistryConfig(),
    )
    assert result.report["status"] == "warning"
    assert result.report["candidate_registry_status"] == "registered_research_only"
    assert result.report["promotion_status"] == "blocked"
    assert result.report["decision"] == "MANTER_EM_RESEARCH"
    assert len(result.registry["candidates"]) == 1


def test_blocks_promotion_when_branch04_warning_and_research_decision() -> None:
    gate = evaluate_candidate_gate(
        training_summary(),
        executive_pack(status="ok", decision="CANDIDATO_RESEARCH_ONLY"),
        True,
        ShadowCandidateRegistryConfig(),
    )
    assert "branch04_status_not_ok:warning" in gate["promotion_blockers"]
    assert (
        "branch04_decision_not_approved:MANTER_EM_RESEARCH"
        in gate["promotion_blockers"]
    )
    assert "branch04_selector_does_not_beat_all_test_baseline" in gate["promotion_blockers"]
    assert any(
        item.startswith("selected_net_pnl_not_above_all_test_net_pnl:")
        for item in gate["promotion_blockers"]
    )


def test_blocks_promotion_when_branch05_keeps_research() -> None:
    approved04 = training_summary(
        status="ok",
        reason="candidate_research_only",
        decision="CANDIDATO_RESEARCH_ONLY",
    )
    gate = evaluate_candidate_gate(
        approved04,
        executive_pack(),
        True,
        ShadowCandidateRegistryConfig(),
    )
    assert "branch05_status_not_ok:warning" in gate["promotion_blockers"]
    assert (
        "branch05_decision_requires_research:MANTER_EM_RESEARCH"
        in gate["promotion_blockers"]
    )


def test_hashes_model_artifact_without_loading_joblib(tmp_path: Path) -> None:
    model = tmp_path / "candidate.joblib"
    content = b"\x80untrusted-pickle-like-bytes\x00"
    model.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert sha256_file(model) == expected
    identity = build_candidate_identity(training_summary(), model)
    assert identity["model_artifact_sha256"] == expected
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
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
    assert "joblib" not in imports
    assert "pickle" not in imports


def test_missing_model_artifact_blocks_promotion(tmp_path: Path) -> None:
    write_project(tmp_path, include_model=False)
    result = run_qlib_ocr_v11_shadow_candidate_registry(
        resolve_paths(tmp_path),
        ShadowCandidateRegistryConfig(),
    )
    assert "model_artifact_missing" in result.report["promotion_blockers"]
    assert result.report["model_artifact_sha256"] is None
    assert result.report["promotion_status"] == "blocked"


def test_unsafe_safety_flags_block_registry() -> None:
    unsafe = executive_pack(sends_orders=True, changes_risk=True)
    gate = evaluate_candidate_gate(
        training_summary(),
        unsafe,
        True,
        ShadowCandidateRegistryConfig(),
    )
    assert "unsafe_safety_flag:branch05:sends_orders=true" in gate["promotion_blockers"]
    assert "unsafe_safety_flag:branch05:changes_risk=true" in gate["promotion_blockers"]


def test_no_write_does_not_materialize_outputs(tmp_path: Path) -> None:
    write_project(tmp_path)
    paths = resolve_paths(tmp_path)
    result = run_qlib_ocr_v11_shadow_candidate_registry(
        paths,
        ShadowCandidateRegistryConfig(),
        write=False,
        analysis_date_utc="2026-06-23T20:00:00Z",
    )
    assert result.report["write_performed"] is False
    assert not paths.registry_output_path.exists()
    assert not paths.report_output_path.exists()


def test_write_materializes_registry_and_report(tmp_path: Path) -> None:
    write_project(tmp_path)
    paths = resolve_paths(tmp_path)
    result = run_qlib_ocr_v11_shadow_candidate_registry(
        paths,
        ShadowCandidateRegistryConfig(),
        write=True,
        analysis_date_utc="2026-06-23T20:00:00Z",
    )
    assert result.report["write_performed"] is True
    registry = json.loads(paths.registry_output_path.read_text(encoding="utf-8"))
    report = json.loads(paths.report_output_path.read_text(encoding="utf-8"))
    assert registry["registry_scope"] == "qlib_ocr_v11_research_shadow_only"
    assert registry["champion_model_id"] is None
    assert report["promotion_status"] == "blocked"
    for name, expected in SAFETY_FLAGS.items():
        assert registry[name] is expected
        assert report[name] is expected


def test_idempotent_upsert_does_not_duplicate_candidate(tmp_path: Path) -> None:
    write_project(tmp_path)
    paths = resolve_paths(tmp_path)
    for _ in range(2):
        run_qlib_ocr_v11_shadow_candidate_registry(
            paths,
            ShadowCandidateRegistryConfig(),
            write=True,
            analysis_date_utc="2026-06-23T20:00:00Z",
        )
    registry = json.loads(paths.registry_output_path.read_text(encoding="utf-8"))
    assert len(registry["candidates"]) == 1
    assert len(registry["registration_events"]) == 1
    assert len(registry["rejected_promotions"]) == 1


def test_preserves_existing_champion_without_creating_one(tmp_path: Path) -> None:
    write_project(tmp_path)
    paths = resolve_paths(tmp_path)
    write_json(
        paths.registry_output_path,
        {
            "champion_model_id": "historical-champion",
            "champion_model_version": "v7",
            "candidates": [],
            "registration_events": [],
            "rejected_promotions": [],
        },
    )
    result = run_qlib_ocr_v11_shadow_candidate_registry(
        paths,
        ShadowCandidateRegistryConfig(),
    )
    assert result.registry["champion_model_id"] == "historical-champion"
    assert result.registry["champion_model_version"] == "v7"


def test_cli_json_no_write(tmp_path: Path) -> None:
    write_project(tmp_path)
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
    assert completed.returncode == 0, completed.stderr
    assert payload["status"] == "warning"
    assert payload["candidate_registry_status"] == "registered_research_only"
    assert payload["write_performed"] is False


def test_runtime_outputs_are_not_expected_to_be_versioned(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)
    assert paths.registry_output_path.is_relative_to(tmp_path / "data")
    assert paths.report_output_path.is_relative_to(tmp_path / "data")
    assert "data/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_existing_model_registry_module_is_not_required_or_modified(tmp_path: Path) -> None:
    before = EXISTING_REGISTRY_MODULE.read_bytes()
    write_project(tmp_path)
    run_qlib_ocr_v11_shadow_candidate_registry(
        resolve_paths(tmp_path),
        ShadowCandidateRegistryConfig(),
    )
    assert EXISTING_REGISTRY_MODULE.read_bytes() == before
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "smartcrypto.ml.model_registry" not in imported_modules
