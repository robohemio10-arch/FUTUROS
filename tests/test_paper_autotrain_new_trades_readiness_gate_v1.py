from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd

from scripts.build_paper_autotrain_new_trades_readiness_gate_v1 import main as cli_main
from smartcrypto.learning.paper_autotrain_incremental_watermark_fix import (
    build_paper_autotrain_incremental_watermark_fix_v1,
)
from smartcrypto.learning.paper_autotrain_new_trades_readiness_gate import (
    build_paper_autotrain_new_trades_readiness_gate_v1,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = Path("data/research/paper_autotrain_daily_quarantine")
WATERMARK_PATH = Path("data/research/paper_autotrain_daily_quarantine_watermark/watermark_v1.json")
REPORT_JSON = Path("data/reports/paper_autotrain_new_trades_readiness_gate_v1.json")
REPORT_MD = Path("data/reports/paper_autotrain_new_trades_readiness_gate_v1.md")


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


def test_missing_watermark_blocks_fail_closed(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 3))

    report = build_paper_autotrain_new_trades_readiness_gate_v1(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_watermark_state"
    assert report["decision"] == "RODAR_BOOTSTRAP_WATERMARK_RESEARCH_ONLY"
    assert report["ready_for_accumulation_recheck"] is False
    assert report["ready_for_training"] is False


def test_watermark_covering_all_records_waits_for_new_paper_trades(tmp_path: Path) -> None:
    for run_index in range(5):
        write_microbatch(tmp_path, f"run-{run_index}", rows(0, 26))
    bootstrap_watermark(tmp_path)

    report = build_paper_autotrain_new_trades_readiness_gate_v1(project_root=tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "no_new_closed_paper_trades_after_watermark"
    assert report["decision"] == "AGUARDAR_NOVOS_TRADES_PAPER"
    assert report["watermark_status"] == "ok"
    assert report["watermark_seen_record_count"] == 26
    assert report["source_file_count"] == 5
    assert report["source_row_count"] == 130
    assert report["source_unique_record_count"] == 26
    assert report["duplicate_record_count"] == 104
    assert report["duplicate_rate"] == 0.8
    assert report["new_closed_trade_record_count"] == 0
    assert report["new_unique_record_count"] == 0
    assert report["already_seen_record_count"] == 26
    assert report["ready_for_accumulation_recheck"] is False
    assert report["would_create_microbatch"] is False
    assert report["would_run_training"] is False


def test_new_record_allows_only_manual_accumulation_recheck(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)
    write_microbatch(tmp_path, "run-2", rows(0, 3))

    report = build_paper_autotrain_new_trades_readiness_gate_v1(project_root=tmp_path)

    assert report["status"] == "ok"
    assert report["reason"] == "new_closed_paper_trades_after_watermark_detected"
    assert report["decision"] == "NOVOS_TRADES_PAPER_DETECTADOS_RECHECK_MANUAL_PERMITIDO"
    assert report["new_unique_record_count"] == 1
    assert report["ready_for_accumulation_recheck"] is True
    assert report["ready_for_candidate_evaluation_recheck"] is False
    assert report["ready_for_training"] is False
    assert report["ready_for_promotion"] is False
    assert report["would_create_microbatch"] is False
    assert report["would_run_training"] is False
    assert report["would_evaluate_candidate"] is False
    assert report["would_promote_model"] is False


def test_feedback_events_absent_is_controlled_warning(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)

    report = build_paper_autotrain_new_trades_readiness_gate_v1(project_root=tmp_path)

    assert report["feedback_event_count"] == 0
    assert report["feedback_new_candidate_count"] == 0
    assert "optional_source_missing:feedback_events" in report["warnings"]


def test_default_no_write_creates_no_report_or_data_artifacts(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)

    build_paper_autotrain_new_trades_readiness_gate_v1(project_root=tmp_path)

    assert not (tmp_path / REPORT_JSON).exists()
    assert not (tmp_path / REPORT_MD).exists()
    assert not (tmp_path / "data" / "runtime").exists()
    assert not list((tmp_path / "data").rglob("*.sqlite"))


def test_write_report_creates_only_json_and_markdown(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)
    before_parquet = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.parquet"))

    report = build_paper_autotrain_new_trades_readiness_gate_v1(project_root=tmp_path, write_report=True)

    assert report["write_report_performed"] is True
    assert (tmp_path / REPORT_JSON).is_file()
    assert (tmp_path / REPORT_MD).is_file()
    after_parquet = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.parquet"))
    assert after_parquet == before_parquet
    assert not list((tmp_path / "data").rglob("*.sqlite"))
    assert not (tmp_path / "data" / "runtime").exists()


def test_safety_flags_remain_false_for_runtime_training_and_orders(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)

    report = build_paper_autotrain_new_trades_readiness_gate_v1(project_root=tmp_path)

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
        "trains_model",
        "training_allowed",
        "ready_for_training",
        "promotion_allowed",
        "ready_for_promotion",
        "runtime_allowed",
        "writes_runtime",
        "writes_sqlite",
        "writes_parquet",
        "writes_active_registry",
        "writes_quarantine_registry",
        "writes_signal_file",
        "writes_active_freqtrade_signals",
        "updates_freqtrade",
        "updates_risk_manager",
        "updates_qlib_runtime",
        "updates_ai_shadow_thresholds",
        "scheduler_registered",
    ):
        assert report[key] is False
        assert report["safety_flags"][key] is False


def test_json_serializable_and_markdown_contains_required_terms(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)

    report = build_paper_autotrain_new_trades_readiness_gate_v1(project_root=tmp_path, write_report=True)

    assert json.loads(json.dumps(report, sort_keys=True, default=str))["schema_version"]
    markdown = (tmp_path / REPORT_MD).read_text(encoding="utf-8")
    for token in ["Status", "Reason", "Decision", "Watermark status", "Watermark seen records", "New records"]:
        assert token in markdown
    assert "aguardando novos trades paper" in markdown or "recheck manual" in markdown


def test_cli_json_executes_without_subprocess(tmp_path: Path, capsys: object) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    bootstrap_watermark(tmp_path)

    exit_code = cli_main(["--project-root", str(tmp_path), "--json"])

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == "paper_autotrain_new_trades_readiness_gate_v1"
    assert payload["decision"] == "AGUARDAR_NOVOS_TRADES_PAPER"


def test_static_safety_no_prohibited_operational_imports_or_paths() -> None:
    files = [
        PROJECT_ROOT / "smartcrypto" / "learning" / "paper_autotrain_new_trades_readiness_gate" / "gate.py",
        PROJECT_ROOT / "scripts" / "build_paper_autotrain_new_trades_readiness_gate_v1.py",
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
