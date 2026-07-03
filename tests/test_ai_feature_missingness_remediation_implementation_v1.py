from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.feature_missingness_remediation_implementation import (
    build_ai_feature_missingness_remediation_implementation_v1,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def create_environment(
    root: Path,
    dataset_rows: list[dict],
    *,
    dataset_filename: str = "implementation_dataset.json",
) -> Path:
    reports = root / "data" / "reports"
    dataset_path = root / "data" / "fixtures" / dataset_filename
    write_json(dataset_path, dataset_rows)
    write_json(
        reports / "ai_unified_feature_contract_v1.json",
        {
            "schema_version": "ai_unified_feature_contract_v1",
            "contract_hash": "feature-contract-hash",
            "feature_columns": ["feature_entry_price", "feature_notional", "feature_quantity"],
        },
    )
    write_json(
        reports / "ai_unified_dataset_manifest_v1.json",
        {
            "schema_version": "ai_unified_dataset_manifest_v1",
            "dataset_hash": "dataset-hash",
            "selected_training_dataset": str(dataset_path),
            "source_paths": [str(dataset_path)],
            "row_count": len(dataset_rows),
        },
    )
    write_json(
        reports / "ai_feature_missingness_remediation_design_v1.json",
        {
            "schema_version": "ai_feature_missingness_remediation_design_v1",
            "decision": "MANTER_EM_RESEARCH",
            "affected_features": ["feature_notional", "feature_quantity"],
        },
    )
    return dataset_path


def feature(report: dict, name: str) -> dict:
    return next(item for item in report["remediated_features"] if item["feature_name"] == name)


def test_default_does_not_write(tmp_path: Path) -> None:
    create_environment(tmp_path, [{"feature_quantity": 1.0, "feature_notional": 10.0}])
    report = build_ai_feature_missingness_remediation_implementation_v1(project_root=tmp_path)
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "ai_feature_missingness_remediation_implementation_v1.json").exists()
    assert not (tmp_path / "data" / "reports" / "ai_feature_missingness_remediation_implementation_v1.md").exists()


def test_write_report_writes_only_allowed_json_and_markdown(tmp_path: Path) -> None:
    rows = [{"feature_quantity": None, "qty": 0.5, "feature_entry_price": 100.0}]
    create_environment(tmp_path, rows)
    reports = tmp_path / "data" / "reports"
    before_names = sorted(path.name for path in reports.iterdir())

    report = build_ai_feature_missingness_remediation_implementation_v1(project_root=tmp_path, write_report=True)

    assert report["write_performed"] is True
    assert (reports / "ai_feature_missingness_remediation_implementation_v1.json").exists()
    assert (reports / "ai_feature_missingness_remediation_implementation_v1.md").exists()

    after_names = sorted(path.name for path in reports.iterdir())
    new_names = sorted(set(after_names) - set(before_names))
    assert new_names == [
        "ai_feature_missingness_remediation_implementation_v1.json",
        "ai_feature_missingness_remediation_implementation_v1.md",
    ]
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "data" / "models").exists()
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.sqlite"))
    assert not (tmp_path / "PROJECT_MANIFEST_CLEAN.json").exists()


def test_active_report_sources_and_dataset_are_never_modified(tmp_path: Path) -> None:
    rows = [{"feature_quantity": None, "qty": 0.5, "feature_entry_price": 100.0}]
    dataset_path = create_environment(tmp_path, rows)
    reports = tmp_path / "data" / "reports"
    tracked_files = [
        reports / "ai_unified_feature_contract_v1.json",
        reports / "ai_unified_dataset_manifest_v1.json",
        reports / "ai_feature_missingness_remediation_design_v1.json",
        dataset_path,
    ]
    before_bytes = {path: path.read_bytes() for path in tracked_files}

    build_ai_feature_missingness_remediation_implementation_v1(project_root=tmp_path, write_report=True)

    for path in tracked_files:
        assert path.read_bytes() == before_bytes[path]


def test_cli_no_write_precedence_over_write_report(tmp_path: Path) -> None:
    rows = [{"feature_quantity": None, "qty": 0.5, "feature_entry_price": 100.0}]
    create_environment(tmp_path, rows)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_ai_feature_missingness_remediation_implementation_v1.py",
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
    assert not (tmp_path / "data" / "reports" / "ai_feature_missingness_remediation_implementation_v1.json").exists()
    assert not (tmp_path / "data" / "reports" / "ai_feature_missingness_remediation_implementation_v1.md").exists()


def test_missing_required_report_sources_are_blocked_without_crash(tmp_path: Path) -> None:
    report = build_ai_feature_missingness_remediation_implementation_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert report["write_performed"] is False
    assert any(item.startswith("missing_required_source:") for item in report["blockers"])


def test_blocked_when_source_fields_insufficient(tmp_path: Path) -> None:
    rows = [
        {"feature_quantity": None, "feature_notional": None, "symbol_norm": "BTCUSDT"},
        {"feature_quantity": None, "feature_notional": None, "symbol_norm": "ETHUSDT"},
    ]
    create_environment(tmp_path, rows)
    report = build_ai_feature_missingness_remediation_implementation_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["decision"] == "MANTER_EM_RESEARCH"
    assert "insufficient_source_fields:feature_quantity" in report["blockers"]
    assert "insufficient_source_fields:feature_notional" in report["blockers"]

    quantity = feature(report, "feature_quantity")
    notional = feature(report, "feature_notional")
    assert quantity["blocked_reason"] == "insufficient_source_fields"
    assert notional["blocked_reason"] == "insufficient_source_fields"
    assert quantity["null_count_delta"] == 0
    assert notional["null_count_delta"] == 0


def test_quantity_remediation_reduces_missingness_via_raw_alias(tmp_path: Path) -> None:
    rows = [
        {"feature_quantity": None, "qty": 0.5, "feature_entry_price": 100.0},
        {"feature_quantity": 1.2, "feature_entry_price": 100.0},
        {"feature_quantity": None, "feature_entry_price": 100.0},
    ]
    create_environment(tmp_path, rows)
    report = build_ai_feature_missingness_remediation_implementation_v1(project_root=tmp_path)
    quantity = feature(report, "feature_quantity")
    assert quantity["row_count_before"] == quantity["row_count_after"] == 3
    assert quantity["before_null_count"] == 2
    assert quantity["after_null_count"] == 1
    assert quantity["null_count_delta"] == -1
    assert quantity["null_rate_delta"] < 0
    assert quantity["source_fields_used"] == ["qty"]
    assert quantity["derivation_possible"] is True
    assert quantity["blocked_reason"] is None


def test_notional_remediation_via_raw_notional_field(tmp_path: Path) -> None:
    rows = [
        {"feature_notional": None, "notional": 500.0},
        {"feature_notional": None},
        {"feature_notional": 200.0},
    ]
    create_environment(tmp_path, rows)
    report = build_ai_feature_missingness_remediation_implementation_v1(project_root=tmp_path)
    notional = feature(report, "feature_notional")
    assert notional["row_count_before"] == notional["row_count_after"] == 3
    assert notional["before_null_count"] == 2
    assert notional["after_null_count"] == 1
    assert notional["null_count_delta"] == -1
    assert notional["source_fields_used"] == ["notional"]
    assert notional["derivation_method"] == "raw_notional_field"
    assert notional["derivation_possible"] is True
    assert notional["blocked_reason"] is None


def test_notional_remediation_via_derived_abs_quantity_times_entry_price(tmp_path: Path) -> None:
    rows = [
        {"feature_notional": None, "quantity": 2.0, "entry_price": 100.0},
        {"feature_notional": None},
    ]
    create_environment(tmp_path, rows)
    report = build_ai_feature_missingness_remediation_implementation_v1(project_root=tmp_path)
    notional = feature(report, "feature_notional")
    assert notional["row_count_before"] == notional["row_count_after"] == 2
    assert notional["before_null_count"] == 2
    assert notional["after_null_count"] == 1
    assert notional["null_count_delta"] == -1
    assert set(notional["source_fields_used"]) == {"quantity", "entry_price"}
    assert notional["derivation_method"] == "derived_abs_quantity_times_entry_price"
    assert notional["derivation_possible"] is True
    assert notional["blocked_reason"] is None


def test_forbidden_fields_are_never_used_as_derivation_source(tmp_path: Path) -> None:
    rows = [
        {
            "feature_quantity": None,
            "qty": 0.5,
            "feature_entry_price": 100.0,
            "net_pnl": 999.0,
            "target_win_loss": 1,
            "label_sign": 1,
            "exit_reason": "roi",
            "close_reason": "manual",
            "future_ret_1h": 0.05,
            "win_flag": True,
            "loss_flag": False,
            "profit_ratio": 0.02,
        }
    ]
    create_environment(tmp_path, rows)
    report = build_ai_feature_missingness_remediation_implementation_v1(project_root=tmp_path)

    assert report["forbidden_fields_used"] == []
    for forbidden_name in (
        "net_pnl",
        "target_win_loss",
        "label_sign",
        "exit_reason",
        "close_reason",
        "future_ret_1h",
        "win_flag",
        "loss_flag",
        "profit_ratio",
    ):
        assert forbidden_name in report["forbidden_fields_present"]

    quantity = feature(report, "feature_quantity")
    assert quantity["source_fields_used"] == ["qty"]
    assert quantity["after_null_count"] == 0
    for remediated in report["remediated_features"]:
        assert set(remediated["source_fields_used"]).isdisjoint(
            {
                "net_pnl",
                "target_win_loss",
                "label_sign",
                "exit_reason",
                "close_reason",
                "future_ret_1h",
                "win_flag",
                "loss_flag",
                "profit_ratio",
            }
        )


def test_decision_and_safety_flags_remain_research_only(tmp_path: Path) -> None:
    rows = [{"feature_quantity": None, "qty": 0.5, "feature_entry_price": 100.0}]
    create_environment(tmp_path, rows)
    report = build_ai_feature_missingness_remediation_implementation_v1(project_root=tmp_path)

    assert report["decision"] == "MANTER_EM_RESEARCH"
    research_only_true_keys = {"paper_only", "shadow_only", "research_only", "read_only"}
    for key, value in report["safety_flags"].items():
        if key in research_only_true_keys:
            assert value is True
        else:
            assert value is False
        assert report[key] == value
    assert report["forbidden_fields_used"] == []
    assert report["no_join_sources_used"] is True


def test_output_json_is_serializable(tmp_path: Path) -> None:
    rows = [{"feature_quantity": None, "qty": 0.5, "feature_entry_price": 100.0}]
    create_environment(tmp_path, rows)
    report = build_ai_feature_missingness_remediation_implementation_v1(project_root=tmp_path)
    payload = json.loads(json.dumps(report, sort_keys=True, default=str))
    assert payload["schema_version"] == "ai_feature_missingness_remediation_implementation_v1"
