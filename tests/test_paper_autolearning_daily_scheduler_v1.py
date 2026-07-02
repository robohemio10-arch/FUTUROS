from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.paper_autolearning.scheduler import (
    build_paper_autolearning_scheduler_report,
)


def foundation_report(**overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "ok",
        "reason": "paper_autolearning_foundation_loop_closed",
        "closed_trades_loaded_count": 2,
        "new_feedback_events_count": 2,
        "duplicate_feedback_events_count": 0,
        "microbatch_rows": 2,
        "qlib_challenger_smoke_ran": False,
        "ai_shadow_challenger_smoke_ran": False,
        "qlib_challenger_trained": False,
        "ai_shadow_challenger_trained": False,
        "master_update_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
    }
    report.update(overrides)
    return report


def fake_runner_factory(calls: list[dict[str, Any]], response: dict[str, Any] | None = None):
    def fake_runner(**kwargs: Any) -> dict[str, Any]:
        calls.append(dict(kwargs))
        return response or foundation_report()

    return fake_runner


def closed_trade(order_id: str, pnl: float) -> dict[str, object]:
    return {
        "order_id": order_id,
        "trade_id": f"trade_{order_id}",
        "moeda": "BTCUSDT" if pnl >= 0 else "ETHUSDT",
        "fechar_side": "long" if pnl >= 0 else "short",
        "horario_abertura": "2026-07-01T12:00:00Z",
        "horario_fechamento": "2026-07-01T12:05:00Z",
        "preco_abertura": 100.0,
        "preco_fechamento": 101.0 if pnl >= 0 else 99.0,
        "quantity": 1.0,
        "notional": 100.0,
        "pnl_fechado": pnl,
        "taxa_lucros_perdas_fechados_pct": pnl,
        "leverage": 10,
    }


def write_source(root: Path) -> Path:
    path = root / "data" / "trades" / "inbox" / "freqtrade_paper_closed_trades.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([closed_trade("1", 1.0), closed_trade("2", -1.0)]).to_csv(path, index=False)
    return path


def test_scheduler_default_is_dry_run(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    report = build_paper_autolearning_scheduler_report(
        project_root=tmp_path,
        foundation_runner=fake_runner_factory(calls),
        now_utc=datetime(2026, 7, 2, 2, 0, tzinfo=UTC),
    )

    assert report["status"] == "ok"
    assert report["scheduler_status"] == "dry_run_ready"
    assert report["scheduler_mode"] == "dry_run"
    assert report["executed_once"] is False
    assert report["foundation_runner_invoked"] is False
    assert calls == []


def test_scheduler_does_not_register_cron_by_default(tmp_path: Path) -> None:
    report = build_paper_autolearning_scheduler_report(project_root=tmp_path)

    assert report["scheduler_registration_requested"] is False
    assert report["scheduler_registration_performed"] is False
    assert report["creates_cron"] is False


def test_scheduler_does_not_create_systemd_timer(tmp_path: Path) -> None:
    report = build_paper_autolearning_scheduler_report(project_root=tmp_path)

    assert report["creates_systemd_timer"] is False


def test_scheduler_does_not_create_windows_task(tmp_path: Path) -> None:
    report = build_paper_autolearning_scheduler_report(project_root=tmp_path)

    assert report["creates_windows_task"] is False


def test_scheduler_does_not_create_service(tmp_path: Path) -> None:
    report = build_paper_autolearning_scheduler_report(project_root=tmp_path)

    assert report["creates_service"] is False


def test_scheduler_once_invokes_foundation_runner(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    report = build_paper_autolearning_scheduler_report(
        project_root=tmp_path,
        once=True,
        foundation_runner=fake_runner_factory(calls),
    )

    assert report["executed_once"] is True
    assert report["foundation_runner_invoked"] is True
    assert report["foundation_runner_status"] == "ok"
    assert report["closed_trades_loaded_count"] == 2
    assert len(calls) == 1


def test_scheduler_passes_write_feedback_flag(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    build_paper_autolearning_scheduler_report(
        project_root=tmp_path,
        once=True,
        write_feedback=True,
        foundation_runner=fake_runner_factory(calls),
    )

    assert calls[0]["write_feedback"] is True


def test_scheduler_passes_train_smoke_flag(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    build_paper_autolearning_scheduler_report(
        project_root=tmp_path,
        once=True,
        train_smoke=True,
        foundation_runner=fake_runner_factory(calls),
    )

    assert calls[0]["train_smoke"] is True


def test_scheduler_preserves_master_update_false(tmp_path: Path) -> None:
    report = build_paper_autolearning_scheduler_report(
        project_root=tmp_path,
        once=True,
        foundation_runner=fake_runner_factory([], foundation_report(master_update_performed=True)),
    )

    assert report["master_update_requested"] is False
    assert report["master_update_performed"] is False


def test_scheduler_preserves_no_model_promotion(tmp_path: Path) -> None:
    report = build_paper_autolearning_scheduler_report(
        project_root=tmp_path,
        once=True,
        foundation_runner=fake_runner_factory([], foundation_report(model_promotion_performed=True, active_model_changed=True)),
    )

    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["qlib_challenger_trained"] is False
    assert report["ai_shadow_challenger_trained"] is False


def test_scheduler_never_sends_orders(tmp_path: Path) -> None:
    report = build_paper_autolearning_scheduler_report(project_root=tmp_path, once=True, foundation_runner=fake_runner_factory([]))

    assert report["sends_orders"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False


def test_scheduler_never_accesses_exchange_private(tmp_path: Path) -> None:
    report = build_paper_autolearning_scheduler_report(project_root=tmp_path, once=True, foundation_runner=fake_runner_factory([]))

    assert report["exchange_private_access"] is False


def test_scheduler_json_contains_next_planned_run(tmp_path: Path) -> None:
    report = build_paper_autolearning_scheduler_report(
        project_root=tmp_path,
        now_utc=datetime(2026, 7, 2, 4, 0, tzinfo=UTC),
    )

    assert report["next_planned_run_utc"] == "2026-07-03T03:00:00+00:00"
    assert report["schedule_cadence"] == "daily"
    assert isinstance(report["would_run_command"], list)
    assert "--write-feedback" in report["would_run_command"]
    assert "--train-smoke" in report["would_run_command"]


def test_register_scheduler_is_blocked(tmp_path: Path) -> None:
    report = build_paper_autolearning_scheduler_report(project_root=tmp_path, register_scheduler=True)

    assert report["status"] == "blocked"
    assert report["reason"] == "scheduler_registration_deferred_to_deployment_branch"
    assert report["scheduler_registration_requested"] is True
    assert report["scheduler_registration_status"] == "blocked"
    assert report["scheduler_registration_performed"] is False


def test_cli_dry_run_json_executes() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/run_paper_autolearning_scheduler_v1.py", "--project-root", ".", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["scheduler_status"] == "dry_run_ready"
    assert payload["foundation_runner_invoked"] is False
    assert payload["creates_cron"] is False


def test_cli_once_no_write_json_executes(tmp_path: Path) -> None:
    source = write_source(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_paper_autolearning_scheduler_v1.py",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--once",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["foundation_runner_invoked"] is True
    assert payload["closed_trades_loaded_count"] == 2
    assert payload["microbatch_rows"] == 2
    assert payload["write_performed"] if "write_performed" in payload else True
    assert payload["sends_orders"] is False


def test_cli_once_write_feedback_train_smoke_json_executes(tmp_path: Path) -> None:
    source = write_source(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_paper_autolearning_scheduler_v1.py",
            "--project-root",
            str(tmp_path),
            "--source",
            str(source),
            "--once",
            "--write-feedback",
            "--train-smoke",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["foundation_runner_invoked"] is True
    assert payload["closed_trades_loaded_count"] == 2
    assert payload["microbatch_rows"] == 2
    assert payload["qlib_challenger_smoke_ran"] is True
    assert payload["ai_shadow_challenger_smoke_ran"] is True
    assert payload["model_promotion_performed"] is False
    assert payload["active_model_changed"] is False
