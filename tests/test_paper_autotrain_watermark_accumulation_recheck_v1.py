from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd

from scripts.build_paper_autotrain_watermark_accumulation_recheck_v1 import main as cli_main
from smartcrypto.learning.paper_autotrain_incremental_watermark_fix import (
    build_paper_autotrain_incremental_watermark_fix_v1,
)
from smartcrypto.learning.paper_autotrain_watermark_accumulation_recheck import (
    build_paper_autotrain_watermark_accumulation_recheck_v1,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = Path("data/research/paper_autotrain_daily_quarantine")
WATERMARK_PATH = Path("data/research/paper_autotrain_daily_quarantine_watermark/watermark_v1.json")
REPORT_JSON = Path("data/reports/paper_autotrain_watermark_accumulation_recheck_v1.json")
REPORT_MD = Path("data/reports/paper_autotrain_watermark_accumulation_recheck_v1.md")


def make_row(index: int, *, record_hash: str | None = None) -> dict[str, object]:
    close_time = pd.Timestamp("2026-06-01T00:00:00Z") + pd.Timedelta(minutes=index)
    return {
        "record_hash": record_hash if record_hash is not None else f"hash-{index}",
        "order_id": f"order-{index}",
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
    path = root / RESEARCH_DIR / run_id / "incremental_training_microbatch.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(data).to_parquet(path, index=False)
    return path


def bootstrap_watermark(root: Path) -> dict[str, object]:
    return build_paper_autotrain_incremental_watermark_fix_v1(
        project_root=root,
        write_watermark_state_requested=True,
        generated_at_utc="2026-06-02T00:00:00+00:00",
    )


def test_recheck_blocks_when_all_unique_records_are_seen_by_watermark(tmp_path: Path) -> None:
    for run_index in range(5):
        write_microbatch(tmp_path, f"run-{run_index}", rows(0, 26))
    bootstrap_watermark(tmp_path)

    report = build_paper_autotrain_watermark_accumulation_recheck_v1(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "no_new_incremental_records_after_watermark"
    assert report["decision"] == "AGUARDAR_NOVOS_TRADES_PAPER"
    assert report["source_file_count"] == 5
    assert report["source_row_count"] == 130
    assert report["unique_record_count"] == 26
    assert report["duplicate_record_count"] == 104
    assert report["duplicate_rate"] == 0.8
    assert report["already_seen_record_count"] == 26
    assert report["new_unique_records_count"] == 0
    assert report["watermark_prevents_reaccumulation"] is True
    assert report["raw_rows_should_not_count_as_new_evidence"] is True
    assert report["stale_duplicate_microbatch_prevented"] is True
    assert report["would_write_microbatch"] is False
    assert report["would_run_training"] is False


def test_missing_microbatches_return_structured_blocked(tmp_path: Path) -> None:
    report = build_paper_autotrain_watermark_accumulation_recheck_v1(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_quarantine_microbatch_sources"
    assert report["decision"] == "AGUARDAR_MICROBATCHES_DE_QUARENTENA"
    assert report["source_row_count"] == 0


def test_missing_watermark_blocks_with_bootstrap_decision(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 3))

    report = build_paper_autotrain_watermark_accumulation_recheck_v1(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_watermark_state"
    assert report["decision"] == "BOOTSTRAP_WATERMARK_RESEARCH_ONLY_ANTES_DE_NOVO_TREINO"
    assert report["watermark_exists"] is False


def test_corrupted_watermark_blocks_without_overwrite(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 3))
    path = tmp_path / WATERMARK_PATH
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    report = build_paper_autotrain_watermark_accumulation_recheck_v1(project_root=tmp_path, write_report=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "watermark_state_invalid"
    assert path.read_text(encoding="utf-8") == "{broken"
    assert report["writes_runtime"] is False
    assert report["writes_parquet"] is False


def test_new_records_after_watermark_are_reported_without_training(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)
    write_microbatch(tmp_path, "run-2", rows(0, 3))

    report = build_paper_autotrain_watermark_accumulation_recheck_v1(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["reason"] == "incremental_records_available_after_watermark"
    assert report["new_unique_records_count"] == 1
    assert report["already_seen_record_count"] == 2
    assert report["would_write_microbatch"] is True
    assert report["would_run_training"] is False
    assert report["training_allowed"] is False


def test_default_no_write_creates_no_report(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)

    build_paper_autotrain_watermark_accumulation_recheck_v1(project_root=tmp_path)

    assert not (tmp_path / REPORT_JSON).exists()
    assert not (tmp_path / REPORT_MD).exists()


def test_write_report_writes_only_reports(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)
    before_parquet = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.parquet"))

    report = build_paper_autotrain_watermark_accumulation_recheck_v1(project_root=tmp_path, write_report=True)

    assert report["write_report_performed"] is True
    assert (tmp_path / REPORT_JSON).is_file()
    assert (tmp_path / REPORT_MD).is_file()
    after_parquet = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.parquet"))
    assert after_parquet == before_parquet
    assert not list((tmp_path / "data").rglob("*.sqlite"))
    assert not (tmp_path / "data" / "runtime").exists()


def test_optional_sources_missing_are_warnings_not_crash(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)

    report = build_paper_autotrain_watermark_accumulation_recheck_v1(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert any(item.startswith("optional_source_missing:") for item in report["warnings"])
    assert report["optional_source_status"]["watermark_fix_report"]["status"] == "missing_optional"


def test_safety_flags_preserve_research_only_boundaries(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)

    report = build_paper_autotrain_watermark_accumulation_recheck_v1(project_root=tmp_path)

    assert report["research_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    for key in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "runs_training",
        "training_allowed",
        "promotion_allowed",
        "runtime_allowed",
        "writes_runtime",
        "writes_sqlite",
        "writes_parquet",
        "writes_active_registry",
        "registry_write_performed",
        "writes_signal_file",
        "updates_freqtrade",
        "updates_risk_manager",
        "updates_qlib_runtime",
        "updates_ai_shadow_runtime",
        "scheduler_registered",
    ):
        assert report[key] is False
        assert report["safety_flags"][key] is False


def test_report_json_serializable_and_markdown_renderable(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)

    report = build_paper_autotrain_watermark_accumulation_recheck_v1(project_root=tmp_path, write_report=True)

    assert json.loads(json.dumps(report, sort_keys=True, default=str))["schema_version"]
    markdown = (tmp_path / REPORT_MD).read_text(encoding="utf-8")
    assert "Paper Autotrain Watermark Accumulation Recheck V1" in markdown
    assert "nao autoriza treino" in markdown


def test_cli_json_executes_without_subprocess(tmp_path: Path, capsys: object) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)

    exit_code = cli_main(["--project-root", str(tmp_path), "--json"])

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == "paper_autotrain_watermark_accumulation_recheck_v1"
    assert payload["decision"] == "AGUARDAR_NOVOS_TRADES_PAPER"


def test_static_safety_no_prohibited_operational_imports_or_paths() -> None:
    files = [
        PROJECT_ROOT / "smartcrypto" / "learning" / "paper_autotrain_watermark_accumulation_recheck" / "recheck.py",
        PROJECT_ROOT / "scripts" / "build_paper_autotrain_watermark_accumulation_recheck_v1.py",
    ]
    prohibited = {"freqtrade", "ccxt", "docker", "subprocess"}
    for source_path in files:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        assert prohibited.isdisjoint({module.split(".")[0] for module in modules})
        source = source_path.read_text(encoding="utf-8")
        assert ".env" not in source
        assert "data/runtime" not in source
        assert "active_freqtrade_signals.json" not in source
