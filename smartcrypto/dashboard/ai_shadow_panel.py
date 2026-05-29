from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_REPORT_PATH = Path("data/reports/ai_shadow_entry_observer_report.json")
DEFAULT_DECISIONS_PATH = Path("data/reports/ai_shadow_entry_decisions.jsonl")

DECISION_COLUMNS = [
    "created_at",
    "symbol",
    "open_1m_ts",
    "probability_win",
    "probability_threshold",
    "decision",
    "decision_reason",
    "model_name",
    "blocked_reason",
]

SAFE_FALSE_FLAGS = (
    "live_trading_enabled",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "exchange_private_access",
)


def load_ai_shadow_panel_state(
    report_path: str | Path = DEFAULT_REPORT_PATH,
    decisions_path: str | Path = DEFAULT_DECISIONS_PATH,
    *,
    tail: int = 200,
) -> dict[str, Any]:
    report_target = Path(report_path)
    decisions_target = Path(decisions_path)
    report = read_json(report_target)
    decisions = read_jsonl(decisions_target, tail=tail)
    safety_alerts = validate_safety_status(report)
    files_present = {
        "report": report_target.exists(),
        "decisions": decisions_target.exists(),
    }
    status = "EMPTY"
    if report:
        status = "SAFETY_ALERT" if safety_alerts else str(report.get("status") or "UNKNOWN").upper()
    return {
        "status": status,
        "files_present": files_present,
        "report_path": str(report_target),
        "decisions_path": str(decisions_target),
        "report": report,
        "decisions": decisions,
        "decision_table": build_decision_table(decisions),
        "safety_alerts": safety_alerts,
        "is_empty": not report and not decisions,
        "is_read_only": True,
        "recommended_command": recommended_command(),
    }


def read_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "BLOCKED", "_error": str(exc)}
    return payload if isinstance(payload, dict) else {"status": "BLOCKED", "_error": "json_report_root_not_object"}


def read_jsonl(path: str | Path, *, tail: int = 200) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in lines[-int(tail) :]:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def validate_safety_status(report: dict[str, Any]) -> list[str]:
    if not report:
        return []
    safety = report.get("safety_status") if isinstance(report.get("safety_status"), dict) else {}
    alerts: list[str] = []
    shadow_only = report.get("shadow_only", safety.get("shadow_only"))
    dry_run = report.get("dry_run", safety.get("dry_run"))
    if shadow_only is not True:
        alerts.append("shadow_only_not_true")
    if dry_run is not True:
        alerts.append("dry_run_not_true")
    for flag in SAFE_FALSE_FLAGS:
        value = report.get(flag, safety.get(flag))
        if value is True:
            alerts.append(f"{flag}_true")
    return alerts


def build_decision_table(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table: list[dict[str, Any]] = []
    for item in decisions:
        row = {column: item.get(column) for column in DECISION_COLUMNS}
        if row.get("probability_win") is not None:
            row["probability_win"] = round(float(row["probability_win"]), 6)
        if row.get("probability_threshold") is not None:
            row["probability_threshold"] = round(float(row["probability_threshold"]), 6)
        table.append(row)
    return table


def render_ai_shadow_panel(st_module: Any, state: dict[str, Any] | None = None) -> None:
    state = state or load_ai_shadow_panel_state()
    report = state.get("report") or {}

    st_module.subheader("AI Shadow Entry Observer")
    st_module.caption("Painel read-only. Nenhuma ordem e enviada por esta tela.")
    if state.get("is_empty"):
        st_module.info("Relatórios do AI Shadow Entry Observer ainda não encontrados.")
        st_module.code(state["recommended_command"], language="powershell")
        return

    if state.get("safety_alerts"):
        st_module.error({"safety_alerts": state["safety_alerts"]})
    else:
        st_module.success("Shadow-only confirmado: dry-run ativo, sem order submission e sem acesso privado.")

    c1, c2, c3, c4, c5 = st_module.columns(5)
    c1.metric("Status", report.get("status", state.get("status", "UNKNOWN")))
    c2.metric("Observed", int(report.get("rows_observed") or 0))
    c3.metric("Shadow entry", int(report.get("shadow_entry_count") or 0))
    c4.metric("Shadow skip", int(report.get("shadow_skip_count") or 0))
    c5.metric("Blocked", int(report.get("blocked_count") or 0))

    st_module.write(
        {
            "probability_threshold": report.get("probability_threshold"),
            "model_name": report.get("model_name"),
            "model_version": report.get("model_version"),
            "model_source": report.get("model_source"),
            "leakage_status": report.get("leakage_status"),
            "shadow_only": report.get("shadow_only"),
            "dry_run": report.get("dry_run"),
            "order_submission_enabled": report.get("order_submission_enabled"),
            "real_order_submission_enabled": report.get("real_order_submission_enabled"),
            "exchange_private_access": (report.get("safety_status") or {}).get("exchange_private_access"),
        }
    )

    table = pd.DataFrame(state.get("decision_table") or [])
    st_module.subheader("Últimas decisões shadow")
    if table.empty:
        st_module.info("Nenhuma decisão JSONL disponível.")
    else:
        st_module.dataframe(table, use_container_width=True, height=420)


def recommended_command() -> str:
    return (
        "python scripts/run_ai_shadow_entry_observer.py `\n"
        "  --features data/features/training_dataset_open_decision_clean.parquet `\n"
        "  --model-report data/reports/model_vs_baseline_financial_evaluation_report.json `\n"
        "  --output data/reports/ai_shadow_entry_observer_report.json `\n"
        "  --decisions-output data/reports/ai_shadow_entry_decisions.jsonl `\n"
        "  --id-column trade_id `\n"
        "  --symbol-column symbol `\n"
        "  --time-column open_1m_ts `\n"
        "  --target-column target_win `\n"
        "  --probability-threshold 0.60 `\n"
        "  --max-rows 500 `\n"
        "  --dry-run true `\n"
        "  --shadow-only true `\n"
        "  --seed 42"
    )
