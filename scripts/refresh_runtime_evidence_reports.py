from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_order_intent_capital_ledger_audit import (  # noqa: E402
    DEFAULT_REPORT_PATH as DEFAULT_LEDGER_REPORT,
    run_order_intent_capital_ledger_audit,
)
from smartcrypto.execution.capital_reservation_ledger import DEFAULT_LEDGER_PATH  # noqa: E402
from smartcrypto.market.market_data_health import MarketDataHealthLimits, run_market_data_health_audit  # noqa: E402
from smartcrypto.market_data.health_runtime_sources import (  # noqa: E402
    DEFAULT_OUTPUT_DIR as DEFAULT_MARKET_RUNTIME_DIR,
    collect_market_data_health_runtime_sources,
)
from smartcrypto.ops.critical_alerting import (  # noqa: E402
    DEFAULT_ALERT_REPORT_PATH,
    build_critical_alerting_report,
)
from smartcrypto.ops.financial_event_log import DEFAULT_EVENT_LOG_PATH, safety_payload, unsafe_safety_flags  # noqa: E402
from smartcrypto.ops.system_healthcheck import (  # noqa: E402
    DEFAULT_BACKUP_REPORT,
    DEFAULT_COMPOSE_FILE,
    DEFAULT_CRITICAL_ALERTING_REPORT,
    DEFAULT_DOCKERFILE,
    DEFAULT_MARKET_HEALTH_REPORT,
    DEFAULT_PAPER_SOAK_REPORT,
    DEFAULT_READINESS_REPORT,
    DEFAULT_REPORT_PATH as DEFAULT_SYSTEM_HEALTHCHECK_REPORT,
    DEFAULT_RESTORE_REPORT,
    DEFAULT_RISK_RECOVERY_REPORT,
    DEFAULT_STATE_RECONCILIATION_REPORT,
    run_system_healthcheck,
)
from smartcrypto.risk.risk_recovery_modes import run_risk_recovery_mode_audit  # noqa: E402
from smartcrypto.state.reconciliation_guard import run_state_reconciliation_audit  # noqa: E402

DEFAULT_REFRESH_REPORT_PATH = Path("data/reports/runtime_evidence_refresh_report.json")
DEFAULT_EQUITY_CURVE_PATH = Path("data/reports/equity_curve.parquet")
DEFAULT_CLOSED_TRADES_PATH = Path("data/feedback/paper_closed_trades_incremental.parquet")
DEFAULT_PAPER_SESSION_REPORT = Path("data/reports/paper_session_report.json")
DEFAULT_MONTE_CARLO_REPORT = Path("data/reports/monte_carlo_risk_simulation_report.json")
DEFAULT_BACKTEST_REPORT = Path("data/reports/event_driven_backtest_report.json")
DEFAULT_KILL_SWITCH_PATH = Path("data/runtime/kill_switch.json")
DEFAULT_INCIDENTS_PATH = Path("data/reports/incidents_report.json")
RISK_RECOVERY_SOURCE_KEYS = (
    "equity_curve",
    "closed_trades",
    "paper_session_report",
    "market_health_report",
    "readiness_report",
    "monte_carlo_report",
    "backtest_report",
    "kill_switch",
    "incidents",
    "state_divergence_report",
)


def refresh_runtime_evidence_reports(
    *,
    report_path: str | Path | None = DEFAULT_REFRESH_REPORT_PATH,
    readiness_report: str | Path | None = DEFAULT_READINESS_REPORT,
    paper_soak_report: str | Path | None = DEFAULT_PAPER_SOAK_REPORT,
    critical_event_log: str | Path = DEFAULT_EVENT_LOG_PATH,
    critical_alerting_report: str | Path | None = DEFAULT_CRITICAL_ALERTING_REPORT,
    risk_recovery_report: str | Path | None = DEFAULT_RISK_RECOVERY_REPORT,
    equity_curve: str | Path | None = DEFAULT_EQUITY_CURVE_PATH,
    closed_trades: str | Path | None = DEFAULT_CLOSED_TRADES_PATH,
    paper_session_report: str | Path | None = DEFAULT_PAPER_SESSION_REPORT,
    monte_carlo_report: str | Path | None = DEFAULT_MONTE_CARLO_REPORT,
    backtest_report: str | Path | None = DEFAULT_BACKTEST_REPORT,
    kill_switch: str | Path | None = DEFAULT_KILL_SWITCH_PATH,
    incidents: str | Path | None = DEFAULT_INCIDENTS_PATH,
    state_divergence_report: str | Path | None = None,
    market_health_report: str | Path | None = DEFAULT_MARKET_HEALTH_REPORT,
    market_runtime_dir: str | Path = DEFAULT_MARKET_RUNTIME_DIR,
    market_symbols: list[str] | tuple[str, ...] = ("BTCUSDT", "ETHUSDT"),
    skip_market_health: bool = False,
    state_repository: str | Path | None = None,
    state_reconciliation_report: str | Path | None = DEFAULT_STATE_RECONCILIATION_REPORT,
    ledger_repository: str | Path | None = DEFAULT_LEDGER_PATH,
    ledger_report: str | Path | None = DEFAULT_LEDGER_REPORT,
    backup_report: str | Path | None = DEFAULT_BACKUP_REPORT,
    restore_report: str | Path | None = DEFAULT_RESTORE_REPORT,
    system_healthcheck_report: str | Path | None = DEFAULT_SYSTEM_HEALTHCHECK_REPORT,
    dockerfile: str | Path | None = DEFAULT_DOCKERFILE,
    compose_file: str | Path | None = DEFAULT_COMPOSE_FILE,
    max_report_age_seconds: int = 900,
    strict: bool = False,
    now: datetime | None = None,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_time = ensure_utc(now or datetime.now(timezone.utc))
    safety = safety_payload(safety_overrides)
    safety_errors = [f"unsafe_safety_flag:{flag}" for flag in unsafe_safety_flags(safety)]
    refreshed_reports: list[str] = []
    skipped_reports: list[dict[str, Any]] = []
    missing_generators: list[str] = []
    runtime_source_paths: dict[str, str] = {}

    stale_before = run_system_healthcheck(
        readiness_report=readiness_report,
        paper_soak_report=paper_soak_report,
        critical_alerting_report=critical_alerting_report,
        risk_recovery_report=risk_recovery_report,
        market_health_report=market_health_report,
        state_reconciliation_report=state_reconciliation_report,
        ledger_report=ledger_report,
        backup_report=backup_report,
        restore_report=restore_report,
        dockerfile=dockerfile,
        compose_file=compose_file,
        report_path=None,
        max_report_age_seconds=max_report_age_seconds,
        now=current_time,
        safety_overrides=safety,
    )

    if safety_errors:
        report = base_report(current_time, safety)
        report.update(
            {
                "status": "blocked",
                "reason": ";".join(safety_errors),
                "blocking_findings": safety_errors,
                "stale_before": stale_before.get("stale_reports", []),
                "stale_after": stale_before.get("stale_reports", []),
            }
        )
        write_report(report, report_path)
        return report

    critical = build_critical_alerting_report(
        event_log_path=critical_event_log,
        report_path=critical_alerting_report,
        strict=False,
        safety_overrides=safety,
    )
    refreshed_reports.append("critical_alerting_report")

    state = refresh_state_report(state_repository, state_reconciliation_report, refreshed_reports, skipped_reports, current_time, safety)
    market = refresh_market_health_report(
        skip_market_health=skip_market_health,
        market_runtime_dir=market_runtime_dir,
        market_symbols=market_symbols,
        market_health_report=market_health_report,
        refreshed_reports=refreshed_reports,
        skipped_reports=skipped_reports,
        runtime_source_paths=runtime_source_paths,
        current_time=current_time,
        safety=safety,
    )
    effective_state_divergence_report = state_divergence_report or state_reconciliation_report
    risk_sources = risk_recovery_sources(
        equity_curve=equity_curve,
        closed_trades=closed_trades,
        paper_session_report=paper_session_report,
        market_health_report=market_health_report,
        readiness_report=readiness_report,
        monte_carlo_report=monte_carlo_report,
        backtest_report=backtest_report,
        kill_switch=kill_switch,
        incidents=incidents,
        state_divergence_report=effective_state_divergence_report,
    )
    risk = run_risk_recovery_mode_audit(
        equity_curve_path=risk_sources["equity_curve"],
        closed_trades_path=risk_sources["closed_trades"],
        paper_session_report_path=risk_sources["paper_session_report"],
        market_health_report_path=risk_sources["market_health_report"],
        readiness_report_path=risk_sources["readiness_report"],
        monte_carlo_report_path=risk_sources["monte_carlo_report"],
        backtest_report_path=risk_sources["backtest_report"],
        kill_switch_path=risk_sources["kill_switch"],
        incidents_path=risk_sources["incidents"],
        state_divergence_report_path=risk_sources["state_divergence_report"],
        report_path=risk_recovery_report,
        strict=False,
        safety_overrides=safety,
        now=current_time,
    )
    refreshed_reports.append("risk_recovery_report")

    ledger = refresh_ledger_report(ledger_repository, ledger_report, refreshed_reports, skipped_reports, current_time, safety)

    system_after = run_system_healthcheck(
        readiness_report=readiness_report,
        paper_soak_report=paper_soak_report,
        critical_alerting_report=critical_alerting_report,
        risk_recovery_report=risk_recovery_report,
        market_health_report=market_health_report,
        state_reconciliation_report=state_reconciliation_report,
        ledger_report=ledger_report,
        backup_report=backup_report,
        restore_report=restore_report,
        dockerfile=dockerfile,
        compose_file=compose_file,
        report_path=system_healthcheck_report,
        max_report_age_seconds=max_report_age_seconds,
        strict=strict,
        now=current_time,
        safety_overrides=safety,
    )

    status = "blocked" if system_after.get("status") == "blocked" and strict else "ok"
    report = base_report(current_time, safety)
    report.update(
        {
            "status": status,
            "reason": "runtime_evidence_refresh_ok" if status == "ok" else str(system_after.get("reason")),
            "refreshed_reports": sorted(set(refreshed_reports)),
            "skipped_reports": skipped_reports,
            "missing_generators": missing_generators,
            "stale_before": stale_before.get("stale_reports", []),
            "stale_after": system_after.get("stale_reports", []),
            "stale_after_count": int(system_after.get("stale_reports_count", 0)),
            "system_health_status": system_after.get("status"),
            "system_health_reason": system_after.get("reason"),
            "system_health_warnings": system_after.get("warnings", []),
            "system_health_blocking_findings": system_after.get("blocking_findings", []),
            "no_trade_policy_active": "no_trade_policy_active" in system_after.get("blocking_findings", []),
            "readiness_blocked": "readiness_gate_blocked" in system_after.get("blocking_findings", []),
            "live_release_allowed": False,
            "readiness_approved": False if "readiness_gate_blocked" in system_after.get("blocking_findings", []) else None,
            "runtime_source_paths": runtime_source_paths,
            "risk_recovery_sources_passed": stringify_sources(risk_sources),
            "risk_recovery_optional_sources_missing": risk.get("optional_sources_missing", []),
            "risk_recovery_source_status": risk.get("sources", {}),
            "risk_recovery_reason": risk.get("reason"),
            "source_statuses": {
                "critical_alerting_report": critical.get("status"),
                "risk_recovery_report": risk.get("status"),
                "ledger_report": ledger.get("status"),
                "state_reconciliation_report": state.get("status"),
                "market_health_report": market.get("status"),
            },
        }
    )
    write_report(report, report_path)
    return report


def risk_recovery_sources(
    *,
    equity_curve: str | Path | None,
    closed_trades: str | Path | None,
    paper_session_report: str | Path | None,
    market_health_report: str | Path | None,
    readiness_report: str | Path | None,
    monte_carlo_report: str | Path | None,
    backtest_report: str | Path | None,
    kill_switch: str | Path | None,
    incidents: str | Path | None,
    state_divergence_report: str | Path | None,
) -> dict[str, str | Path | None]:
    return {
        "equity_curve": equity_curve,
        "closed_trades": closed_trades,
        "paper_session_report": paper_session_report,
        "market_health_report": market_health_report,
        "readiness_report": readiness_report,
        "monte_carlo_report": monte_carlo_report,
        "backtest_report": backtest_report,
        "kill_switch": kill_switch,
        "incidents": incidents,
        "state_divergence_report": state_divergence_report,
    }


def stringify_sources(sources: dict[str, str | Path | None]) -> dict[str, str | None]:
    return {name: str(path) if path is not None else None for name, path in sources.items()}


def refresh_ledger_report(
    repository: str | Path | None,
    report_path: str | Path | None,
    refreshed_reports: list[str],
    skipped_reports: list[dict[str, Any]],
    current_time: datetime,
    safety: dict[str, Any],
) -> dict[str, Any]:
    if repository is None or not Path(repository).exists():
        report = missing_source_report(
            name="ledger_report",
            reason="missing_ledger_repository",
            source_path=repository,
            current_time=current_time,
            safety=safety,
            extra={"reconciliation_required": True, "recommended_mode": "RECONCILING"},
        )
        write_report(report, report_path)
        refreshed_reports.append("ledger_report")
        skipped_reports.append({"report_name": "ledger_report", "reason": "missing_ledger_repository", "source_path": str(repository) if repository else None})
        return report
    refreshed_reports.append("ledger_report")
    return run_order_intent_capital_ledger_audit(repository_path=repository, report_path=report_path, strict=False)


def refresh_state_report(
    repository: str | Path | None,
    report_path: str | Path | None,
    refreshed_reports: list[str],
    skipped_reports: list[dict[str, Any]],
    current_time: datetime,
    safety: dict[str, Any],
) -> dict[str, Any]:
    if repository is None or not Path(repository).exists():
        report = missing_source_report(
            name="state_reconciliation_report",
            reason="missing_state_repository",
            source_path=repository,
            current_time=current_time,
            safety=safety,
            extra={"reconciliation_required": False, "state_divergence_count": 0},
        )
        write_report(report, report_path)
        refreshed_reports.append("state_reconciliation_report")
        skipped_reports.append({"report_name": "state_reconciliation_report", "reason": "missing_state_repository", "source_path": str(repository) if repository else None})
        return report
    refreshed_reports.append("state_reconciliation_report")
    return run_state_reconciliation_audit(repository_path=repository, report_path=report_path, strict=False, runtime_mode="paper")


def refresh_market_health_report(
    *,
    skip_market_health: bool,
    market_runtime_dir: str | Path,
    market_symbols: list[str] | tuple[str, ...],
    market_health_report: str | Path | None,
    refreshed_reports: list[str],
    skipped_reports: list[dict[str, Any]],
    runtime_source_paths: dict[str, str],
    current_time: datetime,
    safety: dict[str, Any],
) -> dict[str, Any]:
    if skip_market_health:
        skipped_reports.append({"report_name": "market_health_report", "reason": "market_health_refresh_skipped"})
        return {}
    sources = collect_market_data_health_runtime_sources(
        symbols=market_symbols,
        output_dir=market_runtime_dir,
        report_path=None,
        strict=False,
        safety_overrides=safety,
        now=current_time,
    )
    runtime_source_paths.update(sources.get("runtime_source_paths", {}))
    paths = sources.get("runtime_source_paths", {})
    market = run_market_data_health_audit(
        candles_path=None,
        runtime_candles_path=paths.get("candles"),
        ticker_path=paths.get("ticker"),
        order_book_path=paths.get("order_book"),
        trades_path=paths.get("trades"),
        rest_snapshot_path=paths.get("rest_snapshot"),
        ws_heartbeat_path=paths.get("ws_heartbeat"),
        report_path=market_health_report,
        limits=MarketDataHealthLimits(),
        strict=False,
        now=current_time,
        safety_overrides=safety,
    )
    refreshed_reports.append("market_health_report")
    refreshed_reports.append("market_data_health_runtime_sources")
    return market


def missing_source_report(
    *,
    name: str,
    reason: str,
    source_path: str | Path | None,
    current_time: datetime,
    safety: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
        "status": "missing_data",
        "reason": reason,
        "generated_at_utc": iso(current_time),
        "report_name": name,
        "source_path": str(source_path) if source_path is not None else None,
        "write_performed": True,
        **(extra or {}),
        **safety,
    }
    return report


def base_report(current_time: datetime, safety: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "reason": "runtime_evidence_refresh_ok",
        "generated_at_utc": iso(current_time),
        "runtime_mode": "paper",
        "paper_only": True,
        "shadow_only": True,
        "public_data_only": True,
        "market_health_public_data_only": True,
        "private_endpoints_used": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        **safety,
    }


def write_report(report: dict[str, Any], report_path: str | Path | None) -> None:
    if report_path is None:
        return
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return ensure_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh runtime evidence reports in paper/shadow mode.")
    parser.add_argument("--report", default=str(DEFAULT_REFRESH_REPORT_PATH))
    parser.add_argument("--readiness-report", default=str(DEFAULT_READINESS_REPORT))
    parser.add_argument("--paper-soak-report", default=str(DEFAULT_PAPER_SOAK_REPORT))
    parser.add_argument("--critical-event-log", default=str(DEFAULT_EVENT_LOG_PATH))
    parser.add_argument("--critical-alerting-report", default=str(DEFAULT_ALERT_REPORT_PATH))
    parser.add_argument("--risk-recovery-report", default=str(DEFAULT_RISK_RECOVERY_REPORT))
    parser.add_argument("--equity-curve", default=str(DEFAULT_EQUITY_CURVE_PATH))
    parser.add_argument("--closed-trades", default=str(DEFAULT_CLOSED_TRADES_PATH))
    parser.add_argument("--paper-session-report", default=str(DEFAULT_PAPER_SESSION_REPORT))
    parser.add_argument("--monte-carlo-report", default=str(DEFAULT_MONTE_CARLO_REPORT))
    parser.add_argument("--backtest-report", default=str(DEFAULT_BACKTEST_REPORT))
    parser.add_argument("--kill-switch", default=str(DEFAULT_KILL_SWITCH_PATH))
    parser.add_argument("--incidents", default=str(DEFAULT_INCIDENTS_PATH))
    parser.add_argument("--state-divergence-report")
    parser.add_argument("--market-health-report", default=str(DEFAULT_MARKET_HEALTH_REPORT))
    parser.add_argument("--market-runtime-dir", default=str(DEFAULT_MARKET_RUNTIME_DIR))
    parser.add_argument("--market-symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--skip-market-health", action="store_true")
    parser.add_argument("--state-repository")
    parser.add_argument("--state-reconciliation-report", default=str(DEFAULT_STATE_RECONCILIATION_REPORT))
    parser.add_argument("--ledger-repository", default=str(DEFAULT_LEDGER_PATH))
    parser.add_argument("--ledger-report", default=str(DEFAULT_LEDGER_REPORT))
    parser.add_argument("--backup-report", default=str(DEFAULT_BACKUP_REPORT))
    parser.add_argument("--restore-report", default=str(DEFAULT_RESTORE_REPORT))
    parser.add_argument("--system-healthcheck-report", default=str(DEFAULT_SYSTEM_HEALTHCHECK_REPORT))
    parser.add_argument("--dockerfile", default=str(DEFAULT_DOCKERFILE))
    parser.add_argument("--compose-file", default=str(DEFAULT_COMPOSE_FILE))
    parser.add_argument("--max-report-age-seconds", type=int, default=900)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = refresh_runtime_evidence_reports(
        report_path=args.report,
        readiness_report=args.readiness_report,
        paper_soak_report=args.paper_soak_report,
        critical_event_log=args.critical_event_log,
        critical_alerting_report=args.critical_alerting_report,
        risk_recovery_report=args.risk_recovery_report,
        equity_curve=args.equity_curve,
        closed_trades=args.closed_trades,
        paper_session_report=args.paper_session_report,
        monte_carlo_report=args.monte_carlo_report,
        backtest_report=args.backtest_report,
        kill_switch=args.kill_switch,
        incidents=args.incidents,
        state_divergence_report=args.state_divergence_report,
        market_health_report=args.market_health_report,
        market_runtime_dir=args.market_runtime_dir,
        market_symbols=args.market_symbols,
        skip_market_health=args.skip_market_health,
        state_repository=args.state_repository,
        state_reconciliation_report=args.state_reconciliation_report,
        ledger_repository=args.ledger_repository,
        ledger_report=args.ledger_report,
        backup_report=args.backup_report,
        restore_report=args.restore_report,
        system_healthcheck_report=args.system_healthcheck_report,
        dockerfile=args.dockerfile,
        compose_file=args.compose_file,
        max_report_age_seconds=args.max_report_age_seconds,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
