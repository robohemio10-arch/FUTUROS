from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.feature_missingness_remediation_design import (
    build_ai_feature_missingness_remediation_design_v1,
)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def create_reports(
    root: Path,
    *,
    include_quantity_source: bool = True,
    include_entry_price_source: bool = True,
    include_notional_source: bool = False,
) -> Path:
    reports = root / "data" / "reports"
    source = root / "data" / "fixtures" / "source_schema.json"
    source_payload: dict[str, object] = {
        "trade_id": "t1",
        "symbol_norm": "BTCUSDT",
        "net_pnl": 1.25,
        "target_win_loss": 1,
        "label_sign": 1,
    }
    if include_quantity_source:
        source_payload["qty"] = 0.01
    if include_entry_price_source:
        source_payload["entry_price"] = 100_000.0
    if include_notional_source:
        source_payload["notional"] = 1_000.0
    write_json(source, source_payload)
    write_json(
        reports / "ai_unified_feature_contract_v1.json",
        {
            "schema_version": "ai_unified_feature_contract_v1",
            "contract_hash": "feature-contract-hash",
            "feature_columns": [
                "feature_entry_price",
                "feature_notional",
                "feature_quantity",
            ],
            "feature_roles": {
                "feature_entry_price": "feature",
                "feature_notional": "feature",
                "feature_quantity": "feature",
                "net_pnl": "outcome",
                "target_win_loss": "label",
            },
            "validation_errors": [],
        },
    )
    write_json(
        reports / "ai_unified_dataset_manifest_v1.json",
        {
            "schema_version": "ai_unified_dataset_manifest_v1",
            "dataset_hash": "dataset-hash",
            "feature_contract_hash": "feature-contract-hash",
            "row_count": 10,
            "selected_training_dataset_rows": 10,
            "selected_training_dataset": str(source),
            "source_paths": [str(source)],
            "null_counts": {
                "feature_entry_price": 0,
                "feature_notional": 10,
                "feature_quantity": 10,
            },
            "validation_errors": [],
        },
    )
    write_json(
        reports / "financial_label_target_store_v1.json",
        {
            "schema_version": "financial_label_target_store_v1",
            "row_count": 10,
            "target_store_hash": "target-store-hash",
            "target_columns": ["target_win_loss", "target_net_pnl"],
        },
    )
    write_json(
        reports / "ai_qlib_drift_regime_monitor_v1.json",
        {
            "schema_version": "ai_qlib_drift_regime_monitor_v1",
            "status": "blocked",
            "reason": "critical_drift_or_missing_required_sources",
            "feature_drift_section": {
                "blockers": ["feature_missingness_critical"],
                "feature_missingness": [
                    {"feature": "feature_entry_price", "null_count": 0, "null_rate": 0.0},
                    {"feature": "feature_notional", "null_count": 10, "null_rate": 1.0},
                    {"feature": "feature_quantity", "null_count": 10, "null_rate": 1.0},
                ],
            },
            "blockers": ["feature_missingness_critical"],
            "lineage_hashes": {"dataset_hash": "dataset-hash", "feature_contract_hash": "feature-contract-hash"},
        },
    )
    write_json(
        reports / "daily_evidence_readiness_executive_pack_v1.json",
        {
            "schema_version": "daily_evidence_readiness_executive_pack_v1",
            "status": "blocked",
            "reason": "executive_pack_blockers_present",
            "lineage_hashes": {"dataset_hash": "dataset-hash"},
        },
    )
    return source


def test_default_does_not_write(tmp_path: Path) -> None:
    create_reports(tmp_path)
    report = build_ai_feature_missingness_remediation_design_v1(project_root=tmp_path)
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "ai_feature_missingness_remediation_design_v1.json").exists()


def test_write_report_writes_only_allowed_json_and_markdown(tmp_path: Path) -> None:
    create_reports(tmp_path)
    report = build_ai_feature_missingness_remediation_design_v1(project_root=tmp_path, write_report=True)
    assert report["write_performed"] is True
    reports = tmp_path / "data" / "reports"
    assert (reports / "ai_feature_missingness_remediation_design_v1.json").exists()
    assert (reports / "ai_feature_missingness_remediation_design_v1.md").exists()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "data" / "models").exists()
    assert not list(tmp_path.rglob("*.parquet"))


def test_cli_no_write_precedence_over_write_report(tmp_path: Path) -> None:
    create_reports(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_ai_feature_missingness_remediation_design_v1.py",
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
    assert not (tmp_path / "data" / "reports" / "ai_feature_missingness_remediation_design_v1.json").exists()


def test_affected_features_include_notional_and_quantity_when_critical(tmp_path: Path) -> None:
    create_reports(tmp_path)
    report = build_ai_feature_missingness_remediation_design_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert {"feature_notional", "feature_quantity"}.issubset(set(report["affected_features"]))
    findings = {item["feature_name"]: item for item in report["missingness_findings"]}
    assert findings["feature_notional"]["null_rate"] == 1.0
    assert findings["feature_quantity"]["null_count"] == 10


def test_notional_derivation_requires_quantity_and_entry_price_or_raw_notional(tmp_path: Path) -> None:
    create_reports(tmp_path, include_quantity_source=True, include_entry_price_source=True)
    report = build_ai_feature_missingness_remediation_design_v1(project_root=tmp_path)
    candidates = {item["feature_name"]: item for item in report["derivation_candidates"]}
    assert candidates["feature_notional"]["candidate_method"] == "derive_abs_quantity_times_entry_price"
    assert candidates["feature_notional"]["derivation_possible"] is True

    root_without_entry = tmp_path / "without_entry"
    create_reports(root_without_entry, include_quantity_source=True, include_entry_price_source=False)
    blocked = build_ai_feature_missingness_remediation_design_v1(project_root=root_without_entry)
    blocked_candidates = {item["feature_name"]: item for item in blocked["derivation_candidates"]}
    assert blocked_candidates["feature_notional"]["blocked_reason"] == "insufficient_source_fields"


def test_quantity_derivation_requires_raw_quantity_qty_or_amount(tmp_path: Path) -> None:
    create_reports(tmp_path, include_quantity_source=True)
    report = build_ai_feature_missingness_remediation_design_v1(project_root=tmp_path)
    candidates = {item["feature_name"]: item for item in report["derivation_candidates"]}
    assert candidates["feature_quantity"]["candidate_method"] == "prefer_raw_quantity"
    assert candidates["feature_quantity"]["source_fields"] == ["qty"]

    root_without_quantity = tmp_path / "without_quantity"
    create_reports(root_without_quantity, include_quantity_source=False)
    blocked = build_ai_feature_missingness_remediation_design_v1(project_root=root_without_quantity)
    blocked_candidates = {item["feature_name"]: item for item in blocked["derivation_candidates"]}
    assert blocked_candidates["feature_quantity"]["blocked_reason"] == "insufficient_source_fields"


def test_forbidden_outcome_target_fields_are_not_candidate_feature_sources(tmp_path: Path) -> None:
    create_reports(tmp_path)
    report = build_ai_feature_missingness_remediation_design_v1(project_root=tmp_path)
    for candidate in report["derivation_candidates"]:
        assert candidate["forbidden_fields_used_by_candidate"] == []
        assert candidate["anti_leakage_validated"] is True
        forbidden_names = {"net_pnl", "target_win_loss", "label_sign"}
        assert not forbidden_names.intersection(set(candidate["source_fields"]))


def test_missing_sources_generate_explicit_blocker(tmp_path: Path) -> None:
    report = build_ai_feature_missingness_remediation_design_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert "missing_required_source:data/reports/ai_unified_feature_contract_v1.json" in report["blockers"]
    assert report["decision"] == "MANTER_EM_RESEARCH"


def test_design_only_never_changes_contract_or_manifest(tmp_path: Path) -> None:
    create_reports(tmp_path)
    before_contract = (tmp_path / "data" / "reports" / "ai_unified_feature_contract_v1.json").read_text(
        encoding="utf-8"
    )
    before_manifest = (tmp_path / "data" / "reports" / "ai_unified_dataset_manifest_v1.json").read_text(
        encoding="utf-8"
    )
    report = build_ai_feature_missingness_remediation_design_v1(project_root=tmp_path, write_report=True)
    assert report["changes_feature_contract"] is False
    assert report["changes_dataset_manifest"] is False
    assert (tmp_path / "data" / "reports" / "ai_unified_feature_contract_v1.json").read_text(
        encoding="utf-8"
    ) == before_contract
    assert (tmp_path / "data" / "reports" / "ai_unified_dataset_manifest_v1.json").read_text(
        encoding="utf-8"
    ) == before_manifest


def test_decision_release_and_operational_flags_remain_safe(tmp_path: Path) -> None:
    create_reports(tmp_path)
    report = build_ai_feature_missingness_remediation_design_v1(project_root=tmp_path)
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["release_allowed"] is False
    for key, value in report["safety_flags"].items():
        if key in {"paper_only", "shadow_only", "research_only", "read_only", "design_only", "informational_only"}:
            assert value is True
        else:
            assert value is False


def test_output_json_is_serializable(tmp_path: Path) -> None:
    create_reports(tmp_path)
    report = build_ai_feature_missingness_remediation_design_v1(project_root=tmp_path)
    payload = json.loads(json.dumps(report, sort_keys=True, default=str))
    assert payload["schema_version"] == "ai_feature_missingness_remediation_design_v1"
