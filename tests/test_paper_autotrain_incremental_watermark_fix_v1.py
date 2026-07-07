from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.paper_autotrain_daily_quarantine_activation.activation import (
    build_paper_autotrain_daily_quarantine_activation_v1,
)
from smartcrypto.learning.paper_autotrain_incremental_watermark_fix import (
    build_paper_autotrain_incremental_watermark_fix_v1,
)
from smartcrypto.learning.paper_autotrain_incremental_watermark_fix import watermark as watermark_module

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "build_paper_autotrain_incremental_watermark_fix_v1.py"
RESEARCH_DIR = Path("data/research/paper_autotrain_daily_quarantine")
WATERMARK_PATH = Path("data/research/paper_autotrain_daily_quarantine_watermark/watermark_v1.json")


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
    path = root / RESEARCH_DIR / run_id / "incremental_training_microbatch.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(data).to_parquet(path, index=False)
    return path


def fake_closed_trades_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["1", "2", "3"],
            "symbol": ["BTCUSDT", "BTCUSDT", "ETHUSDT"],
            "side": ["long", "short", "long"],
            "open_time_utc": pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"),
            "close_time_utc": pd.date_range("2026-01-01T01:00:00Z", periods=3, freq="h"),
            "net_pnl": [1.0, -0.5, 0.8],
        }
    )


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def train_challenger(
        self,
        *,
        root: Path,
        run_id: str,
        backend_id: str,
        microbatch: pd.DataFrame,
        paths: Any,
        write_artifact: bool,
    ) -> dict[str, Any]:
        del root, paths, write_artifact
        self.calls.append({"run_id": run_id, "backend_id": backend_id, "rows": int(len(microbatch))})
        candidate = {
            "candidate_id": f"{backend_id}_{run_id}",
            "backend_id": backend_id,
            "status": "trained_quarantine_only",
            "row_count": int(len(microbatch)),
            "promotion_eligible": False,
            "quarantine_only": True,
        }
        return {
            "backend_id": backend_id,
            "status": "trained_quarantine_only",
            "reason": "trained_quarantine_only",
            "artifact_path": None,
            "artifact_hash": None,
            "artifact_written": False,
            "candidate": candidate,
            "blockers": [],
            "warnings": [],
        }


def test_no_microbatches_blocks_without_writes(tmp_path: Path) -> None:
    report = build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "missing_quarantine_microbatch_sources"
    assert not (tmp_path / "data").exists()


def test_existing_microbatches_without_watermark_require_bootstrap(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 3))
    report = build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "watermark_bootstrap_required"
    assert report["bootstrap_required"] is True
    assert report["would_initialize_watermark"] is True
    assert report["bootstrap_unique_record_count"] == 3


def test_write_watermark_state_creates_only_research_watermark(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 3))
    report = build_paper_autotrain_incremental_watermark_fix_v1(
        project_root=tmp_path, write_watermark_state_requested=True
    )
    path = tmp_path / WATERMARK_PATH
    assert report["write_watermark_performed"] is True
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "paper_autotrain_incremental_watermark_state_v1"
    assert payload["seen_record_key_count"] == 3
    assert sorted(payload["seen_record_keys"]) == payload["seen_record_keys"]
    assert not list(tmp_path.rglob("*.parquet.tmp"))
    assert not list(tmp_path.rglob("*.sqlite"))


def test_second_run_same_universe_is_blocked_by_watermark(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 3))
    build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path, write_watermark_state_requested=True)
    report = build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "no_new_incremental_records_after_watermark"
    assert report["new_unique_records_count"] == 0
    assert report["already_seen_record_count"] == 3
    assert report["stale_duplicate_microbatch_prevented"] is True
    assert report["would_write_microbatch"] is False
    assert report["would_run_training"] is False
    assert report["training_prevented_by_watermark"] is True


def test_future_new_records_are_allowed_as_quarantine_microbatch(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path, write_watermark_state_requested=True)
    write_microbatch(tmp_path, "run-2", rows(0, 3))
    report = build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path)
    assert report["status"] == "ok"
    assert report["reason"] == "incremental_records_available"
    assert report["new_unique_records_count"] == 1
    assert report["already_seen_record_count"] == 2
    assert report["would_write_microbatch"] is True


def test_corrupted_watermark_blocks_and_is_not_overwritten(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    path = tmp_path / WATERMARK_PATH
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    report = build_paper_autotrain_incremental_watermark_fix_v1(
        project_root=tmp_path,
        write_watermark_state_requested=True,
        fail_on_watermark_corruption=True,
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "watermark_state_invalid"
    assert path.read_text(encoding="utf-8") == "{broken"


def test_dedup_uses_record_hash_first(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", [make_row(1, record_hash="same", order_id="a")])
    write_microbatch(tmp_path, "run-2", [make_row(2, record_hash="same", order_id="b")])
    report = build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path)
    assert report["unique_record_count"] == 1
    assert report["record_key_strategy_counts"]["record_hash"] == 2


def test_dedup_falls_back_to_order_id_close_time(tmp_path: Path) -> None:
    first = make_row(1, record_hash="", order_id="order-x")
    second = {**make_row(1, record_hash="", order_id="order-x"), "trade_id": "other"}
    write_microbatch(tmp_path, "run-1", [first, second])
    report = build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path)
    assert report["unique_record_count"] == 1
    assert report["record_key_strategy_counts"]["order_id_close_time"] == 2


def test_dedup_falls_back_to_row_hash(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", [{"feature_a": 1.0}, {"feature_a": 1.0}])
    report = build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path)
    assert report["unique_record_count"] == 1
    assert report["record_key_strategy_counts"]["normalized_row_hash"] == 2


def test_activation_first_run_writes_microbatch_and_watermark(tmp_path: Path) -> None:
    backend = FakeBackend()
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        write_feedback=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        closed_trades_frame=fake_closed_trades_frame(),
        microbatch_frame=pd.DataFrame(rows(0, 3)),
        trainer_backend=backend,
        generated_at_utc="2026-01-01T00:00:00+00:00",
    )
    assert report["status"] == "ok"
    assert report["new_unique_records_count"] == 3
    assert report["microbatch_rows"] == 3
    assert len(backend.calls) == 2
    assert (tmp_path / WATERMARK_PATH).is_file()


def test_activation_second_run_same_records_does_not_train_or_duplicate_candidate(tmp_path: Path) -> None:
    backend = FakeBackend()
    common = dict(
        project_root=tmp_path,
        once=True,
        write_feedback=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        closed_trades_frame=fake_closed_trades_frame(),
        microbatch_frame=pd.DataFrame(rows(0, 3)),
        trainer_backend=backend,
    )
    build_paper_autotrain_daily_quarantine_activation_v1(
        **common,
        generated_at_utc="2026-01-01T00:00:00+00:00",
    )
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        **common,
        generated_at_utc="2026-01-02T00:00:00+00:00",
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "no_new_incremental_records_after_watermark"
    assert report["microbatch_rows"] == 0
    assert report["training_prevented_by_watermark"] is True
    assert len(backend.calls) == 2
    registries = list((tmp_path / "data" / "registries" / "quarantine").glob("*.json"))
    assert len(registries) == 1


def test_default_no_write_creates_no_report_or_watermark(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path)
    assert not (tmp_path / "data" / "reports").exists()
    assert not (tmp_path / WATERMARK_PATH).exists()


def test_write_report_creates_only_reports(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    before_parquet = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.parquet"))
    report = build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path, write_report=True)
    assert report["write_report_performed"] is True
    assert (tmp_path / "data" / "reports" / "paper_autotrain_incremental_watermark_fix_v1.json").is_file()
    assert (tmp_path / "data" / "reports" / "paper_autotrain_incremental_watermark_fix_v1.md").is_file()
    assert not (tmp_path / WATERMARK_PATH).exists()
    after_parquet = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.parquet"))
    assert after_parquet == before_parquet


def test_safety_flags_remain_false_for_runtime_and_orders(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    report = build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path)
    for key in (
        "sends_orders",
        "changes_risk",
        "training_allowed",
        "promotion_allowed",
        "runtime_allowed",
        "writes_runtime",
        "writes_sqlite",
        "writes_parquet",
        "writes_active_registry",
        "writes_signal_file",
    ):
        assert report[key] is False
        assert report["safety_flags"][key] is False


def test_static_safety_no_prohibited_operational_imports_or_paths() -> None:
    source_path = Path(watermark_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    prohibited = {"freqtrade", "ccxt", "docker", "subprocess"}
    assert prohibited.isdisjoint({module.split(".")[0] for module in modules})
    source = source_path.read_text(encoding="utf-8")
    assert ".env" not in source
    assert "data/runtime" not in source


def test_json_serializable_and_cli_executes(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    report = build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path)
    assert json.loads(json.dumps(report, sort_keys=True, default=str))["schema_version"]
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == "paper_autotrain_incremental_watermark_fix_v1"


def test_markdown_report_contains_required_sections(tmp_path: Path) -> None:
    write_microbatch(tmp_path, "run-1", rows(0, 2))
    build_paper_autotrain_incremental_watermark_fix_v1(project_root=tmp_path, write_report=True)
    text = (tmp_path / "data" / "reports" / "paper_autotrain_incremental_watermark_fix_v1.md").read_text(
        encoding="utf-8"
    )
    for token in ["Status", "Reason", "Decision", "Watermark status", "Source rows", "Duplicate rate"]:
        assert token in text
    assert "nao tem autoridade operacional" in text
