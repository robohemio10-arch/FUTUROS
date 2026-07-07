from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.learning.paper_autotrain_microbatch_freshness_and_watermark import (
    build_paper_autotrain_microbatch_freshness_and_watermark_v1,
)
from smartcrypto.learning.paper_autotrain_microbatch_freshness_and_watermark import freshness as freshness_module

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "build_paper_autotrain_microbatch_freshness_and_watermark_v1.py"
RESEARCH_DIR = Path("data/research/paper_autotrain_daily_quarantine")
MICROBATCH = "incremental_training_microbatch.parquet"


def make_row(index: int, *, record_hash: str | None = None, order_id: str | None = None) -> dict[str, object]:
    close_time = pd.Timestamp("2026-06-01T00:00:00Z") + pd.Timedelta(minutes=index)
    return {
        "record_hash": record_hash if record_hash is not None else f"hash-{index}",
        "order_id": order_id if order_id is not None else f"order-{index}",
        "trade_id": f"trade-{index}",
        "symbol": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
        "side": "long" if index % 2 == 0 else "short",
        "open_time_utc": close_time - pd.Timedelta(minutes=10),
        "close_time_utc": close_time,
        "pnl_fechado": 1.0 if index % 3 == 0 else -1.0,
        "target_profitable": 1 if index % 3 == 0 else 0,
        "feature_a": float(index),
        "feature_b": float(index + 1),
    }


def rows(start: int, count: int) -> list[dict[str, object]]:
    return [make_row(index) for index in range(start, start + count)]


def write_microbatch(root: Path, run_id: str, data: list[dict[str, object]]) -> Path:
    path = root / RESEARCH_DIR / run_id / MICROBATCH
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(data).to_parquet(path, index=False)
    return path


def test_no_microbatches_blocks_safely(tmp_path: Path) -> None:
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "missing_quarantine_microbatch_sources"
    assert report["decision"] == "AGUARDAR_MICROBATCHES_DE_QUARENTENA"
    assert report["source_file_count"] == 0


def test_single_microbatch_all_records_are_new_without_stalled_reason(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 3))
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    assert report["reason"] != "microbatch_freshness_stalled"
    assert report["run_count"] == 1
    assert report["runs_with_new_records_count"] == 1
    assert report["per_run_freshness"][0]["new_unique_records_count"] == 3
    assert report["training_allowed"] is False
    assert report["promotion_allowed"] is False
    assert report["runtime_allowed"] is False


def test_five_identical_microbatches_detect_freshness_stalled(tmp_path: Path) -> None:
    base_rows = rows(0, 26)
    for run_index in range(5):
        write_microbatch(tmp_path, f"run-{run_index}", base_rows)
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "microbatch_freshness_stalled"
    assert report["decision"] == "CORRIGIR_WATERMARK_INCREMENTAL_ANTES_DE_NOVO_TREINO"
    assert report["run_count"] == 5
    assert report["runs_with_new_records_count"] == 1
    assert report["runs_without_new_records_count"] == 4
    assert report["all_runs_reobserve_same_records"] is True
    assert report["source_row_count"] == 130
    assert report["unique_record_count"] == 26
    assert report["duplicate_record_count"] == 104
    assert report["duplicate_rate"] == 0.8


def test_incremental_microbatches_detect_progress(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    write_microbatch(tmp_path, "run-2", rows(0, 3))
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    assert report["all_runs_reobserve_same_records"] is False
    assert report["runs_with_new_records_count"] == 2
    assert report["per_run_freshness"][1]["new_unique_records_count"] == 1
    assert report["status"] in {"ok", "warning"}


def test_duplicate_within_run_count_is_reported(tmp_path: Path) -> None:
    duplicated = [make_row(1), make_row(1), make_row(2)]
    write_microbatch(tmp_path, "run-1", duplicated)
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    assert report["per_run_freshness"][0]["duplicate_within_run_count"] == 1
    assert report["per_run_freshness"][0]["unique_record_count_in_run"] == 2


def test_temporal_watermarks_are_reported(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    assert report["first_close_time_utc"] == "2026-06-01T00:00:00+00:00"
    assert report["last_close_time_utc"] == "2026-06-01T00:01:00+00:00"
    assert report["watermark_close_time_utc"] == "2026-06-01T00:01:00+00:00"


def test_dedup_uses_record_hash_first(tmp_path: Path) -> None:
    first = make_row(1, record_hash="same-hash", order_id="order-a")
    second = make_row(2, record_hash="same-hash", order_id="order-b")
    write_microbatch(tmp_path, "run-1", [first])
    write_microbatch(tmp_path, "run-2", [second])
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    assert report["unique_record_count"] == 1
    assert report["per_run_freshness"][1]["new_unique_records_count"] == 0


def test_dedup_falls_back_to_order_id_and_close_time(tmp_path: Path) -> None:
    first = make_row(1, record_hash="", order_id="order-x")
    second = {**make_row(1, record_hash="", order_id="order-x"), "trade_id": "different-trade"}
    write_microbatch(tmp_path, "run-1", [first])
    write_microbatch(tmp_path, "run-2", [second])
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    assert report["unique_record_count"] == 1
    assert report["per_run_freshness"][1]["reobserved_records_count"] == 1


def test_dedup_falls_back_to_normalized_row_hash(tmp_path: Path) -> None:
    minimal = [{"feature_a": 1.0, "target_profitable": 1}, {"feature_a": 1.0, "target_profitable": 1}]
    write_microbatch(tmp_path, "run-1", minimal)
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    assert report["unique_record_count"] == 1
    assert report["per_run_freshness"][0]["duplicate_within_run_count"] == 1


def test_default_does_not_write_reports(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    assert not (tmp_path / "data" / "reports").exists()


def test_write_report_writes_only_json_and_markdown_under_data_reports(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    parquet_before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.parquet"))
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path, write_report=True)
    reports = tmp_path / "data" / "reports"
    assert report["write_report_performed"] is True
    assert (reports / "paper_autotrain_microbatch_freshness_and_watermark_v1.json").is_file()
    assert (reports / "paper_autotrain_microbatch_freshness_and_watermark_v1.md").is_file()
    assert not list(tmp_path.rglob("*.sqlite"))
    parquet_after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.parquet"))
    assert parquet_after == parquet_before


def test_safety_flags_never_enable_runtime_or_training(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
    assert report["training_allowed"] is False
    assert report["promotion_allowed"] is False
    assert report["runtime_allowed"] is False
    assert report["writes_runtime"] is False
    assert report["writes_sqlite"] is False
    assert report["writes_parquet"] is False
    assert report["safety_flags"]["research_only"] is True


def test_fail_on_stale_blocks_partial_staleness(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    write_microbatch(tmp_path, "run-2", rows(0, 3))
    write_microbatch(tmp_path, "run-3", rows(0, 3))
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(
        project_root=tmp_path, fail_on_stale=True
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "microbatch_stale_runs_detected"


def test_fail_on_no_new_records_blocks_partial_staleness(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    write_microbatch(tmp_path, "run-2", rows(0, 3))
    write_microbatch(tmp_path, "run-3", rows(0, 3))
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(
        project_root=tmp_path, fail_on_no_new_records=True
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "microbatch_runs_without_new_records"


def test_json_is_serializable(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    report = build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path)
    payload = json.loads(json.dumps(report, sort_keys=True, default=str))
    assert payload["schema_version"] == "paper_autotrain_microbatch_freshness_and_watermark_v1"


def test_markdown_contains_required_metrics(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    build_paper_autotrain_microbatch_freshness_and_watermark_v1(project_root=tmp_path, write_report=True)
    text = (tmp_path / "data" / "reports" / "paper_autotrain_microbatch_freshness_and_watermark_v1.md").read_text(
        encoding="utf-8"
    )
    for token in ["Status", "Reason", "Decision", "Run count", "Source row count", "Duplicate rate"]:
        assert token in text
    assert "nao possui autoridade operacional" in text


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_static_safety_no_prohibited_operational_imports() -> None:
    modules = _imported_modules(Path(freshness_module.__file__))
    prohibited = {"freqtrade", "ccxt", "docker", "subprocess"}
    assert prohibited.isdisjoint({module.split(".")[0] for module in modules})


def test_static_safety_no_operational_paths_in_domain() -> None:
    source = Path(freshness_module.__file__).read_text(encoding="utf-8")
    assert ".env" not in source
    assert "data/runtime" not in source
    assert "freqtrade/user_data" not in source


def test_cli_json_executes(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == "paper_autotrain_microbatch_freshness_and_watermark_v1"
    assert payload["write_performed"] is False
