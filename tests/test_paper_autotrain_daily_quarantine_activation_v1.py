from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from smartcrypto.learning.paper_autotrain_daily_quarantine_activation.activation import (
    build_paper_autotrain_daily_quarantine_activation_v1,
)


SCRIPT = Path("scripts/run_paper_autotrain_daily_quarantine_activation_v1.py")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def closed_trades_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["1", "2", "3", "4"],
            "symbol": ["BTCUSDT", "BTCUSDT", "ETHUSDT", "ETHUSDT"],
            "side": ["long", "short", "long", "short"],
            "open_time_utc": pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC"),
            "close_time_utc": pd.date_range("2026-01-01T01:00:00Z", periods=4, freq="h"),
            "net_pnl": [1.0, -0.5, 0.8, -0.2],
        }
    )


def microbatch_frame(*, empty: bool = False, single_class: bool = False) -> pd.DataFrame:
    if empty:
        return pd.DataFrame()
    target = [1, 0, 1, 0] if not single_class else [1, 1, 1, 1]
    return pd.DataFrame(
        {
            "order_id": ["1", "2", "3", "4"],
            "symbol": ["BTCUSDT", "BTCUSDT", "ETHUSDT", "ETHUSDT"],
            "side": ["long", "short", "long", "short"],
            "target_profitable": target,
            "target_return": [0.1, -0.1, 0.2, -0.2],
            "feature_a": [1.0, 2.0, 3.0, 4.0],
            "feature_b": [0.1, 0.2, 0.3, 0.4],
        }
    )


def test_default_no_write_does_not_write_files(tmp_path: Path) -> None:
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )

    assert report["status"] == "planned"
    assert report["write_performed"] is False
    assert not (tmp_path / "data").exists()


def test_once_with_write_flags_writes_only_allowed_paths(tmp_path: Path) -> None:
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        write_feedback=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        write_report=True,
        fail_on_operational_write=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
        generated_at_utc="2026-01-01T00:00:00+00:00",
    )

    assert report["status"] == "ok"
    assert report["reason"] == "quarantine_cycle_executed"
    assert (tmp_path / "data" / "feedback" / "paper_autotrain_daily_quarantine_feedback_events_v1.jsonl").is_file()
    assert (tmp_path / "data" / "registries" / "quarantine" / "paper_autotrain_candidate_registry_v1.json").is_file()
    assert list((tmp_path / "data" / "models" / "quarantine" / "paper_autotrain").rglob("*_candidate_model.json"))
    assert not list(tmp_path.rglob("active_freqtrade_signals.json"))
    assert not (tmp_path / "data" / "runtime").exists()
    assert not (tmp_path / "data" / "registries" / "active").exists()


def test_report_is_json_serializable(tmp_path: Path) -> None:
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )

    assert "paper_autotrain_daily_quarantine_activation_v1" in json.dumps(report, sort_keys=True)


def test_missing_closed_trades_blocks_structurally(tmp_path: Path) -> None:
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        train_challenger=True,
        microbatch_frame=microbatch_frame(),
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_or_invalid_closed_trades"


def test_empty_microbatch_blocks_training_without_promotion(tmp_path: Path) -> None:
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        train_challenger=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(empty=True),
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_or_empty_microbatch"
    assert report["model_promotion_performed"] is False


def test_qlib_unavailable_is_structured_warning(monkeypatch, tmp_path: Path) -> None:
    import importlib.util

    original = importlib.util.find_spec

    def fake_find_spec(name: str):
        if name == "qlib":
            return None
        return original(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        write_feedback=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )

    assert report["status"] == "warning"
    assert report["qlib_challenger_train_status"] == "unavailable"
    assert "qlib_backend_unavailable" in report["warnings"]
    assert report["model_promotion_performed"] is False


def test_ai_shadow_unavailable_is_structured_warning(monkeypatch, tmp_path: Path) -> None:
    import smartcrypto.learning.paper_autotrain_daily_quarantine_activation.activation as activation

    monkeypatch.setattr(activation, "sklearn_available", lambda: False)
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        write_feedback=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )

    assert report["status"] == "warning"
    assert report["ai_shadow_challenger_train_status"] == "unavailable"
    assert "ai_shadow_backend_unavailable" in report["warnings"]


def test_successful_challenger_training_generates_quarantine_artifacts_not_active(tmp_path: Path) -> None:
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        write_feedback=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )

    assert report["qlib_challenger_train_status"] in {"trained_quarantine_only", "unavailable"}
    assert report["ai_shadow_challenger_train_status"] == "trained_quarantine_only"
    assert report["active_model_changed"] is False
    assert not (tmp_path / "data" / "models" / "active").exists()


def test_quarantine_registry_written_only_with_flag(tmp_path: Path) -> None:
    build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        train_challenger=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )
    assert not (tmp_path / "data" / "registries" / "quarantine").exists()

    build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )
    assert (tmp_path / "data" / "registries" / "quarantine" / "paper_autotrain_candidate_registry_v1.json").is_file()


def test_active_registry_and_active_model_are_not_altered(tmp_path: Path) -> None:
    active_registry = tmp_path / "data" / "registries" / "active" / "registry.json"
    active_model = tmp_path / "data" / "models" / "active" / "model.json"
    active_registry.parent.mkdir(parents=True)
    active_model.parent.mkdir(parents=True)
    active_registry.write_text("active-registry", encoding="utf-8")
    active_model.write_text("active-model", encoding="utf-8")

    build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        write_feedback=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )

    assert active_registry.read_text(encoding="utf-8") == "active-registry"
    assert active_model.read_text(encoding="utf-8") == "active-model"


def test_active_freqtrade_signals_and_user_data_are_not_touched(tmp_path: Path) -> None:
    user_data = tmp_path / "freqtrade" / "user_data"
    user_data.mkdir(parents=True)
    sentinel = user_data / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")

    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        write_feedback=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )

    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert not list(tmp_path.rglob("active_freqtrade_signals.json"))
    assert report["writes_active_freqtrade_signals"] is False


def test_runtime_engines_env_and_docker_are_not_used(tmp_path: Path) -> None:
    forbidden_modules = ("freqtrade", "ccxt")
    for module_name in forbidden_modules:
        sys.modules.pop(module_name, None)

    module = importlib.import_module("smartcrypto.learning.paper_autotrain_daily_quarantine_activation.activation")
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert ".env" not in source
    assert "subprocess" not in source
    assert "docker compose" not in source
    assert "import RiskManager" not in source
    assert "from smartcrypto.risk" not in source

    module.build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )
    for module_name in forbidden_modules:
        assert module_name not in sys.modules


def test_scheduler_check_does_not_create_scheduler(tmp_path: Path) -> None:
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        scheduler_check=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )

    assert report["scheduler_registered"] is False
    assert report["creates_cron"] is False
    assert report["creates_systemd_timer"] is False
    assert report["creates_windows_task"] is False
    assert report["starts_service"] is False


def test_safety_flags_operational_fields_remain_false(tmp_path: Path) -> None:
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        write_feedback=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )

    assert report["research_only"] is True
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["quarantine_only"] is True
    for key in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "writes_runtime",
        "writes_active_freqtrade_signals",
        "active_signal_file_written",
    ):
        assert report[key] is False


def test_cli_real_project_runs_against_project_root() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", ".", "--json"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema_version"] == "paper_autotrain_daily_quarantine_activation_v1"
    assert payload["decision"] == "QUARANTINE_ONLY"
    assert payload["sends_orders"] is False


def test_cli_write_report_only_writes_report_paths(tmp_path: Path) -> None:
    closed = tmp_path / "data" / "feedback"
    closed.mkdir(parents=True)
    closed_trades_frame().to_parquet(closed / "paper_closed_trades_incremental.parquet", index=False)
    features = tmp_path / "data" / "features"
    features.mkdir(parents=True)
    microbatch_frame().to_parquet(features / "incremental_training_microbatch.parquet", index=False)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(tmp_path), "--write-report", "--json"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["write_performed"] is True
    assert (tmp_path / "data" / "reports" / "paper_autotrain_daily_quarantine_activation_v1.json").is_file()
    assert not (tmp_path / "data" / "registries" / "quarantine").exists()
    assert not (tmp_path / "data" / "models" / "quarantine").exists()


def test_git_ignored_runtime_outputs_are_under_data(tmp_path: Path) -> None:
    report = build_paper_autotrain_daily_quarantine_activation_v1(
        project_root=tmp_path,
        once=True,
        write_feedback=True,
        train_challenger=True,
        write_quarantine_artifacts=True,
        write_report=True,
        closed_trades_frame=closed_trades_frame(),
        microbatch_frame=microbatch_frame(),
    )

    for value in report["output_paths"].values():
        if value:
            assert str(Path(value)).startswith(str(tmp_path / "data"))
