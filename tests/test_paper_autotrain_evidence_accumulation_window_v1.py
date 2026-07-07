from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd

from smartcrypto.learning.paper_autotrain_evidence_accumulation_window import (
    build_paper_autotrain_evidence_accumulation_window_v1,
)
from smartcrypto.learning.paper_autotrain_evidence_accumulation_window import accumulation as accumulation_module

RESEARCH_DIR = Path("data/research/paper_autotrain_daily_quarantine")
MICROBATCH_FILENAME = "incremental_training_microbatch.parquet"


def make_row(*, index: int, target: int, feature_count: int = 6, id_prefix: str = "o") -> dict:
    close_time = pd.Timestamp("2026-06-01T00:00:00Z") + pd.Timedelta(minutes=index)
    open_time = close_time - pd.Timedelta(hours=1)
    row: dict[str, object] = {
        "order_id": f"{id_prefix}-{index}",
        "close_time_utc": close_time,
        "open_time_utc": open_time,
        "symbol": "BTCUSDT",
        "side": "long" if target else "short",
        "pnl_fechado": 1.0 if target else -1.0,
        "target_profitable": target,
    }
    for feature_index in range(feature_count):
        row[f"feature_{feature_index}"] = float(index + feature_index)
    return row


def build_rows(
    *, positive_count: int, negative_count: int, feature_count: int = 6, id_prefix: str = "o", start_index: int = 0
) -> list[dict]:
    rows = []
    index = start_index
    for _ in range(positive_count):
        rows.append(make_row(index=index, target=1, feature_count=feature_count, id_prefix=id_prefix))
        index += 1
    for _ in range(negative_count):
        rows.append(make_row(index=index, target=0, feature_count=feature_count, id_prefix=id_prefix))
        index += 1
    return rows


def write_microbatch(root: Path, run_id: str, rows: list[dict]) -> Path:
    path = root / RESEARCH_DIR / run_id / MICROBATCH_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def test_no_sources_returns_blocked_missing_sources(tmp_path: Path) -> None:
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "missing_quarantine_microbatch_sources"
    assert report["decision"] == "AGUARDAR_MAIS_EVIDENCIA"
    assert report["accumulation_ready_for_candidate_recheck"] is False
    assert report["candidate_recheck_allowed"] is False
    assert report["source_file_count"] == 0


def test_single_source_26_rows_returns_blocked_insufficient_evidence(tmp_path: Path) -> None:
    rows = build_rows(positive_count=7, negative_count=19)
    write_microbatch(tmp_path, "run-1", rows)
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "insufficient_accumulated_evidence"
    assert report["decision"] == "AGUARDAR_MAIS_EVIDENCIA"
    assert report["accumulated_row_count"] == 26
    assert report["accumulation_ready_for_candidate_recheck"] is False
    assert report["candidate_recheck_allowed"] is False


def test_multiple_sources_100_plus_balanced_rows_returns_ready(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    write_microbatch(tmp_path, "run-2", build_rows(positive_count=30, negative_count=30, id_prefix="b"))
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["status"] == "ok"
    assert report["reason"] == "accumulated_evidence_ready_for_candidate_recheck"
    assert report["decision"] == "REAVALIACAO_DE_CANDIDATOS_PERMITIDA_EM_BRANCH_SEPARADA"
    assert report["accumulated_row_count"] == 120
    assert report["observed_class_positive_count"] == 60
    assert report["observed_class_negative_count"] == 60
    assert report["accumulation_ready_for_candidate_recheck"] is True
    assert report["candidate_recheck_allowed"] is True
    assert report["blockers"] == []


def test_positive_class_below_minimum_blocks(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=15, negative_count=90))
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "insufficient_accumulated_evidence"
    assert "min_class_positive_count_not_met" in report["blockers"]
    assert "min_class_negative_count_not_met" not in report["blockers"]
    assert "min_accumulated_rows_not_met" not in report["blockers"]


def test_negative_class_below_minimum_blocks(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=90, negative_count=15))
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "insufficient_accumulated_evidence"
    assert "min_class_negative_count_not_met" in report["blockers"]
    assert "min_class_positive_count_not_met" not in report["blockers"]


def test_feature_count_below_minimum_blocks(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=60, negative_count=60, feature_count=3))
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "insufficient_accumulated_evidence"
    assert "min_feature_count_not_met" in report["blockers"]
    assert report["observed_feature_count"] == 3
    assert "min_class_positive_count_not_met" not in report["blockers"]
    assert "min_accumulated_rows_not_met" not in report["blockers"]


def test_deduplication_removes_duplicate_rows(tmp_path: Path) -> None:
    rows_a = build_rows(positive_count=15, negative_count=15, id_prefix="shared", start_index=0)
    rows_b = build_rows(positive_count=15, negative_count=15, id_prefix="shared", start_index=20)
    write_microbatch(tmp_path, "run-1", rows_a)
    write_microbatch(tmp_path, "run-2", rows_b)
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["source_row_count"] == 60
    assert report["accumulated_row_count"] < report["source_row_count"]
    assert report["duplicate_rows_removed"] > 0


def test_duplicate_rate_above_threshold_blocks(tmp_path: Path) -> None:
    unique_rows = build_rows(positive_count=55, negative_count=55, feature_count=6, id_prefix="dup")
    write_microbatch(tmp_path, "run-1", unique_rows)
    write_microbatch(tmp_path, "run-2", unique_rows)
    write_microbatch(tmp_path, "run-3", unique_rows)
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["source_row_count"] == 330
    assert report["accumulated_row_count"] == 110
    assert report["duplicate_rate"] > 0.05
    assert report["blockers"] == ["max_duplicate_rate_exceeded"]
    assert report["status"] == "blocked"
    assert report["reason"] == "insufficient_accumulated_evidence"


def test_default_does_not_write_anything(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    reports_dir = tmp_path / "data" / "reports"
    before = set(reports_dir.rglob("*")) if reports_dir.exists() else set()
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["write_report_performed"] is False
    assert report["write_dataset_performed"] is False
    assert report["write_performed"] is False
    after = set(reports_dir.rglob("*")) if reports_dir.exists() else set()
    assert after == before
    assert not (tmp_path / "data" / "research" / "paper_autotrain_evidence_accumulation_window").exists()


def test_write_report_writes_only_data_reports(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path, write_report=True)
    assert report["write_report_performed"] is True
    reports_dir = tmp_path / "data" / "reports"
    assert (reports_dir / "paper_autotrain_evidence_accumulation_window_v1.json").exists()
    assert (reports_dir / "paper_autotrain_evidence_accumulation_window_v1.md").exists()
    assert not (tmp_path / "data" / "research" / "paper_autotrain_evidence_accumulation_window").exists()


def test_write_accumulated_dataset_writes_only_research_window_dir(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    report = build_paper_autotrain_evidence_accumulation_window_v1(
        project_root=tmp_path, write_accumulated_dataset=True
    )
    assert report["write_dataset_performed"] is True
    assert report["write_report_performed"] is False
    dataset_dir = tmp_path / "data" / "research" / "paper_autotrain_evidence_accumulation_window"
    assert (dataset_dir / "accumulated_microbatch.parquet").exists()
    assert (dataset_dir / "accumulated_microbatch_manifest.json").exists()
    assert not (tmp_path / "data" / "reports" / "paper_autotrain_evidence_accumulation_window_v1.json").exists()
    written_frame = pd.read_parquet(dataset_dir / "accumulated_microbatch.parquet")
    assert len(written_frame) == 60
    assert not any(str(column).startswith("__accumulator_") for column in written_frame.columns)


def test_does_not_create_data_runtime(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    build_paper_autotrain_evidence_accumulation_window_v1(
        project_root=tmp_path, write_report=True, write_accumulated_dataset=True
    )
    assert not (tmp_path / "data" / "runtime").exists()


def test_does_not_create_active_freqtrade_signals(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    build_paper_autotrain_evidence_accumulation_window_v1(
        project_root=tmp_path, write_report=True, write_accumulated_dataset=True
    )
    matches = list(tmp_path.rglob("active_freqtrade_signals.json"))
    assert matches == []


def test_does_not_write_active_registry(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    registries_dir = tmp_path / "data" / "registries"
    before = set(registries_dir.rglob("*")) if registries_dir.exists() else set()
    build_paper_autotrain_evidence_accumulation_window_v1(
        project_root=tmp_path, write_report=True, write_accumulated_dataset=True
    )
    after = set(registries_dir.rglob("*")) if registries_dir.exists() else set()
    assert after == before


def test_does_not_write_active_model(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    build_paper_autotrain_evidence_accumulation_window_v1(
        project_root=tmp_path, write_report=True, write_accumulated_dataset=True
    )
    assert not (tmp_path / "data" / "models").exists()


def test_does_not_alter_quarantine_registry(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    registry_path = tmp_path / "data" / "registries" / "quarantine" / "paper_autotrain_candidate_registry_v1.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps({"schema_version": "paper_autotrain_quarantine_candidate_registry_v1"}), encoding="utf-8")
    before_bytes = registry_path.read_bytes()
    build_paper_autotrain_evidence_accumulation_window_v1(
        project_root=tmp_path, write_report=True, write_accumulated_dataset=True
    )
    assert registry_path.read_bytes() == before_bytes


def test_output_json_is_serializable(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    payload = json.loads(json.dumps(report, sort_keys=True, default=str))
    assert payload["schema_version"] == "paper_autotrain_evidence_accumulation_window_v1"


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_does_not_import_freqtrade() -> None:
    source = Path(accumulation_module.__file__).read_text(encoding="utf-8")
    modules = _imported_module_names(source)
    assert not any(module == "freqtrade" or module.startswith("freqtrade.") for module in modules)


def test_domain_does_not_import_ccxt() -> None:
    source = Path(accumulation_module.__file__).read_text(encoding="utf-8")
    modules = _imported_module_names(source)
    assert not any(module == "ccxt" or module.startswith("ccxt.") for module in modules)


def test_domain_does_not_import_docker() -> None:
    source = Path(accumulation_module.__file__).read_text(encoding="utf-8")
    modules = _imported_module_names(source)
    assert not any(module == "docker" or module.startswith("docker.") for module in modules)


def test_domain_does_not_import_risk_manager() -> None:
    source = Path(accumulation_module.__file__).read_text(encoding="utf-8")
    modules = _imported_module_names(source)
    assert not any("risk_manager" in module or "smartcrypto.risk" in module for module in modules)


def test_domain_does_not_call_operational_subprocess() -> None:
    source = Path(accumulation_module.__file__).read_text(encoding="utf-8")
    modules = _imported_module_names(source)
    assert "subprocess" not in modules
    assert "subprocess" not in source


def test_trains_model_and_promotes_model_are_always_false(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["trains_model"] is False
    assert report["promotes_model"] is False
    assert report["runs_training"] is False
    assert report["model_promotion_performed"] is False


def test_runtime_allowed_is_always_false(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["runtime_allowed"] is False
    assert report["writes_runtime"] is False


def test_sends_orders_and_changes_risk_are_always_false(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False


def test_ready_scenario_only_allows_candidate_recheck_never_promotion_or_runtime(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", build_rows(positive_count=30, negative_count=30, id_prefix="a"))
    write_microbatch(tmp_path, "run-2", build_rows(positive_count=30, negative_count=30, id_prefix="b"))
    report = build_paper_autotrain_evidence_accumulation_window_v1(project_root=tmp_path)
    assert report["status"] == "ok"
    assert report["candidate_recheck_allowed"] is True
    assert report["training_allowed"] is False
    assert report["promotion_allowed"] is False
    assert report["runtime_allowed"] is False
    assert report["trains_model"] is False
    assert report["promotes_model"] is False
