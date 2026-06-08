from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from smartcrypto.ops.freqtrade_paper_db_authority import resolve_freqtrade_paper_db_authority
from smartcrypto.ops.paper_shadow_soak_report import build_paper_shadow_soak_report


NOW = datetime(2026, 6, 8, 13, 0, tzinfo=timezone.utc)
FIRST_TRADE = "2026-06-01 20:15:07.045313"
LAST_ACTIVITY = "2026-06-08 12:05:33.723298"
STALE_LAST_ACTIVITY = "2026-05-29 15:36:16"


def safe_flags() -> dict[str, object]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows),
        encoding="utf-8",
    )
    return path


def source_paths(tmp_path: Path) -> dict[str, Path]:
    reports = tmp_path / "reports"
    return {
        "financial_event_log": reports / "financial_event_log.jsonl",
        "critical_alerting_report": reports / "critical_alerting_report.json",
        "risk_recovery_report": reports / "risk_recovery_mode_audit_report.json",
        "market_health_report": reports / "market_data_health_audit_report.json",
        "state_reconciliation_report": reports / "state_reconciliation_audit_report.json",
        "ledger_report": reports / "order_intent_capital_ledger_audit_report.json",
        "ai_governance_report": reports / "ai_governance_dashboard_sources_report.json",
        "risk_readiness_report": reports / "risk_readiness_soak_dashboard_sources_report.json",
        "drift_report": reports / "ai_shadow_drift_monitor_report.json",
        "financial_threshold_report": reports / "ai_shadow_financial_threshold_evaluation_report.json",
        "anti_leakage_report": reports / "phase23_anti_leakage_report.json",
        "monte_carlo_report": reports / "monte_carlo_risk_simulation_report.json",
        "monte_carlo_risk_budget_policy_report": reports
        / "monte_carlo_risk_budget_policy_report.json",
        "event_backtest_report": reports / "event_driven_backtest_report.json",
        "data_quality_report": reports / "data_quality_report.json",
        "dataset_manifest": reports / "dataset_manifest.json",
        "paper_soak_report": reports / "paper_soak_report.json",
        "db_authority_report": reports / "freqtrade_paper_db_authority_report.json",
    }


def write_soak_sources(tmp_path: Path, *, no_trade_policy: bool = False) -> dict[str, Path]:
    paths = source_paths(tmp_path)
    write_jsonl(
        paths["financial_event_log"],
        [
            {
                "event_type": "signal_generated",
                "occurred_at_utc": "2026-06-01T12:00:00Z",
                "runtime_mode": "paper",
                "paper_only": True,
            }
        ],
    )
    payloads: dict[str, dict[str, object]] = {
        "critical_alerting_report": {"status": "ok", "critical_alerts": 0, **safe_flags()},
        "risk_recovery_report": {
            "status": "ok",
            "recommended_mode": "NORMAL",
            "blocking_findings": [],
            **safe_flags(),
        },
        "market_health_report": {"status": "ok", "stale_data_count": 0, **safe_flags()},
        "state_reconciliation_report": {
            "status": "ok",
            "reconciliation_required": False,
            "state_divergence_count": 0,
            **safe_flags(),
        },
        "ledger_report": {
            "status": "ok",
            "duplicate_idempotency_key_count": 0,
            "duplicate_client_order_id_count": 0,
            "dispatch_unknown_count": 0,
            **safe_flags(),
        },
        "ai_governance_report": {"status": "ok", **safe_flags()},
        "risk_readiness_report": {
            "status": "ok",
            "clean_streak_days": 6,
            "p0_incidents": 0,
            "p1_incidents": 0,
            "p2_incidents": 0,
            **safe_flags(),
        },
        "drift_report": {"status": "ok", "drift_blocks": 0, **safe_flags()},
        "financial_threshold_report": {"status": "ok", "paper_pnl_net": 10.0, **safe_flags()},
        "anti_leakage_report": {"status": "ok", **safe_flags()},
        "monte_carlo_report": {
            "status": "blocked" if no_trade_policy else "ok",
            "reason": "risk_budget_exceeded" if no_trade_policy else "ok",
            **safe_flags(),
        },
        "monte_carlo_risk_budget_policy_report": {
            "status": "blocked" if no_trade_policy else "ok",
            "policy_action": "no_trade" if no_trade_policy else "observe_only",
            "readiness_may_proceed": False if no_trade_policy else True,
            "live_release_allowed": False,
            **safe_flags(),
        },
        "event_backtest_report": {"status": "ok", **safe_flags()},
        "data_quality_report": {"status": "ok", **safe_flags()},
        "dataset_manifest": {"status": "ok", **safe_flags()},
    }
    for name, payload in payloads.items():
        write_json(paths[name], payload)
    return paths


def create_trades_db(
    path: Path,
    *,
    total: int,
    open_trades: int,
    first_open: str = FIRST_TRADE,
    last_activity: str = LAST_ACTIVITY,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY,
                is_open INTEGER NOT NULL,
                open_date TEXT NOT NULL,
                close_date TEXT
            )
            """
        )
        closed = max(total - open_trades, 0)
        for trade_id in range(1, total + 1):
            is_open = 1 if trade_id > closed else 0
            open_date = first_open if trade_id == 1 else "2026-06-02 00:00:00"
            close_date = None if is_open else last_activity
            connection.execute(
                "INSERT INTO trades (id, is_open, open_date, close_date) VALUES (?, ?, ?, ?)",
                (trade_id, is_open, open_date, close_date),
            )
    return path


def create_empty_trades_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, is_open INTEGER, open_date TEXT)"
        )
    return path


def create_db_without_trades_table(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE metadata (key TEXT)")
    return path


def build_soak_with_db(
    tmp_path: Path,
    *,
    db_path: Path,
    required_days: int = 30,
    strict: bool = True,
    no_trade_policy: bool = False,
) -> dict[str, object]:
    paths = write_soak_sources(tmp_path, no_trade_policy=no_trade_policy)
    return build_paper_shadow_soak_report(
        financial_event_log=paths["financial_event_log"],
        critical_alerting_report=paths["critical_alerting_report"],
        risk_recovery_report=paths["risk_recovery_report"],
        market_health_report=paths["market_health_report"],
        state_reconciliation_report=paths["state_reconciliation_report"],
        ledger_report=paths["ledger_report"],
        ai_governance_report=paths["ai_governance_report"],
        risk_readiness_report=paths["risk_readiness_report"],
        drift_report=paths["drift_report"],
        financial_threshold_report=paths["financial_threshold_report"],
        anti_leakage_report=paths["anti_leakage_report"],
        monte_carlo_report=paths["monte_carlo_report"],
        monte_carlo_risk_budget_policy_report=paths["monte_carlo_risk_budget_policy_report"],
        event_backtest_report=paths["event_backtest_report"],
        data_quality_report=paths["data_quality_report"],
        dataset_manifest=paths["dataset_manifest"],
        report_path=paths["paper_soak_report"],
        required_soak_days=required_days,
        strict=strict,
        now=NOW,
        freqtrade_paper_db=db_path,
        freqtrade_paper_db_authority_report=paths["db_authority_report"],
    )


def test_selects_db_with_254_trades_over_stale_20_trade_db(tmp_path: Path) -> None:
    current = create_trades_db(tmp_path / "snapshot.sqlite", total=254, open_trades=2)
    stale = create_trades_db(
        tmp_path / "stale.sqlite",
        total=20,
        open_trades=0,
        first_open="2026-05-28 10:00:00",
        last_activity=STALE_LAST_ACTIVITY,
    )

    report = resolve_freqtrade_paper_db_authority(candidate_paths=[stale, current])

    assert report["status"] == "ok"
    assert report["selected_path"] == str(current)
    assert report["selection_reason"] == "highest_total_trades_latest_activity"
    assert str(stale) in report["stale_candidates"]
    selected = [item for item in report["candidates"] if item["selected"]][0]
    assert selected["total_trades"] == 254
    assert selected["open_trades"] == 2
    assert selected["closed_trades"] == 252


def test_respects_explicit_valid_path(tmp_path: Path) -> None:
    explicit = create_trades_db(tmp_path / "explicit.sqlite", total=20, open_trades=0)
    richer = create_trades_db(tmp_path / "richer.sqlite", total=254, open_trades=2)

    report = resolve_freqtrade_paper_db_authority(
        explicit_path=explicit,
        candidate_paths=[richer],
    )

    assert report["status"] == "ok"
    assert report["selected_path"] == str(explicit)
    assert report["selection_reason"] == "explicit_valid_path"


def test_rejects_db_without_trades_table(tmp_path: Path) -> None:
    invalid = create_db_without_trades_table(tmp_path / "invalid.sqlite")

    report = resolve_freqtrade_paper_db_authority(candidate_paths=[invalid])

    assert report["status"] == "blocked"
    assert report["candidates"][0]["has_trades_table"] is False
    assert report["candidates"][0]["selection_reason"] == "missing_trades_table"


def test_rejects_empty_trades_table(tmp_path: Path) -> None:
    empty = create_empty_trades_db(tmp_path / "empty.sqlite")

    report = resolve_freqtrade_paper_db_authority(candidate_paths=[empty])

    assert report["status"] == "blocked"
    assert report["candidates"][0]["has_trades_table"] is True
    assert report["candidates"][0]["total_trades"] == 0
    assert report["candidates"][0]["selection_reason"] == "empty_trades_table"


def test_calculates_observed_soak_days_from_first_open_date(tmp_path: Path) -> None:
    db_path = create_trades_db(tmp_path / "snapshot.sqlite", total=254, open_trades=2)

    report = build_soak_with_db(tmp_path, db_path=db_path, strict=False)

    expected_days = (
        datetime.fromisoformat(LAST_ACTIVITY) - datetime.fromisoformat(FIRST_TRADE)
    ).total_seconds() / 86400.0
    assert report["observed_soak_days"] == pytest.approx(expected_days, abs=0.000001)
    assert report["observed_soak_days_from_trade_history"] == pytest.approx(
        expected_days,
        abs=0.000001,
    )
    assert report["first_trade_open_date"] == "2026-06-01T20:15:07.045313Z"
    assert report["last_trade_activity_date"] == "2026-06-08T12:05:33.723298Z"


def test_calculates_remaining_soak_days_from_trade_history(tmp_path: Path) -> None:
    db_path = create_trades_db(tmp_path / "snapshot.sqlite", total=254, open_trades=2)

    report = build_soak_with_db(tmp_path, db_path=db_path, required_days=30, strict=False)
    expected_days = (
        datetime.fromisoformat(LAST_ACTIVITY) - datetime.fromisoformat(FIRST_TRADE)
    ).total_seconds() / 86400.0
    expected_remaining = 30.0 - expected_days

    assert report["remaining_soak_days"] == pytest.approx(expected_remaining, abs=0.000001)
    assert report["remaining_soak_days_from_trade_history"] == pytest.approx(
        expected_remaining,
        abs=0.000001,
    )
    assert report["required_soak_days"] == 30


def test_includes_candidates_and_selection_reason_in_soak_report(tmp_path: Path) -> None:
    db_path = create_trades_db(tmp_path / "snapshot.sqlite", total=254, open_trades=2)

    report = build_soak_with_db(tmp_path, db_path=db_path, strict=False)

    assert report["freqtrade_paper_db_selected"] == str(db_path)
    assert report["freqtrade_paper_db_selection_reason"] == "explicit_valid_path"
    assert report["freqtrade_paper_db_candidates"]
    assert report["trades_total"] == 254
    assert report["trades_open"] == 2
    assert report["trades_closed"] == 252


def test_keeps_status_blocked_when_no_trade_policy_active(tmp_path: Path) -> None:
    db_path = create_trades_db(tmp_path / "snapshot.sqlite", total=254, open_trades=2)

    report = build_soak_with_db(
        tmp_path,
        db_path=db_path,
        required_days=30,
        strict=True,
        no_trade_policy=True,
    )

    assert report["status"] == "blocked"
    assert report["readiness_may_proceed"] is False
    assert report["live_release_allowed"] is False
    assert "monte_carlo_no_trade_policy_active" in report["readiness_blockers"]
    assert "soak_days_below_required" in report["readiness_blockers"]


def test_preserves_safety_flags(tmp_path: Path) -> None:
    db_path = create_trades_db(tmp_path / "snapshot.sqlite", total=254, open_trades=2)

    report = build_soak_with_db(tmp_path, db_path=db_path, strict=False)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
    assert report["sends_orders"] is False
    assert report["changes_risk"] is False
