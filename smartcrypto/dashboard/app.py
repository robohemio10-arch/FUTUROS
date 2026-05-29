from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml

from smartcrypto.dashboard.ai_shadow_panel import render_ai_shadow_panel
from smartcrypto.dashboard.freqtrade_snapshot_reader import (
    load_freqtrade_trades_snapshot,
    perf_metrics as freqtrade_perf_metrics,
    status_payload as freqtrade_status_payload,
)
from smartcrypto.qlib_engine.prediction_freshness import inspect_qlib_prediction_freshness
from smartcrypto.risk.kill_switch_classifier import classify_kill_switch


st.set_page_config(page_title="SmartCrypto Paper", layout="wide")


def load_yaml(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        with target.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except Exception:
        return {}


CONFIG = load_yaml("config/paper_dashboard.yml")
PATHS = CONFIG.get("paths", {})


def read_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        with target.open("r", encoding="utf-8") as handle:
            return json.load(handle) or {}
    except Exception as exc:
        return {"_error": str(exc)}


def read_jsonl(path: str | Path, tail: int = 300) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        return pd.DataFrame()
    rows = []
    for line in target.read_text(encoding="utf-8", errors="ignore").splitlines()[-tail:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return pd.DataFrame(rows)


def read_parquet(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    if not target.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(target)
    except Exception:
        return pd.DataFrame()


def first_existing(paths: list[str]) -> Path | None:
    for value in paths:
        candidate = Path(value)
        if candidate.exists():
            return candidate
    return None


def table_counts(sqlite_path: str | Path) -> pd.DataFrame:
    target = Path(sqlite_path)
    if not target.exists():
        return pd.DataFrame()
    try:
        with sqlite3.connect(str(target)) as connection:
            tables = pd.read_sql_query("select name from sqlite_master where type='table'", connection)
            rows = []
            for name in tables["name"]:
                count = pd.read_sql_query(f"select count(*) as rows from {name}", connection)
                rows.append({"table": name, "rows": int(count.iloc[0]["rows"])})
            return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def signal_frame(path: str | Path) -> pd.DataFrame:
    payload = read_json(path)
    records = payload.get("signals", [])
    return pd.DataFrame(records if isinstance(records, list) else [])


def latest_reports() -> pd.DataFrame:
    root = Path(PATHS.get("reports_dir", "data/reports"))
    if not root.exists():
        return pd.DataFrame()
    rows = []
    for path in sorted(root.rglob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:200]:
        rows.append({"file": str(path), "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(), "size_kb": round(path.stat().st_size / 1024, 2)})
    return pd.DataFrame(rows)


def latest_evidence() -> pd.DataFrame:
    root = Path(PATHS.get("evidence_dir", "data/evidence"))
    if not root.exists():
        return pd.DataFrame()
    rows = []
    for path in sorted(root.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)[:100]:
        rows.append({"file": str(path), "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(), "size_mb": round(path.stat().st_size / 1024 / 1024, 2)})
    return pd.DataFrame(rows)


def perf_metrics(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {"trades": 0, "closed": 0, "open": 0, "pnl": 0.0, "win_rate": None, "profit_factor": None, "max_drawdown": None}
    is_open = trades.get("is_open", pd.Series(dtype=int))
    closed = trades.loc[is_open == 0].copy()
    open_rows = int((is_open == 1).sum())
    pnl_col = "close_profit_abs" if "close_profit_abs" in closed.columns else "realized_profit" if "realized_profit" in closed.columns else None
    if pnl_col is None:
        return {"trades": len(trades), "closed": len(closed), "open": open_rows, "pnl": 0.0, "win_rate": None, "profit_factor": None, "max_drawdown": None}
    pnl = closed[pnl_col].fillna(0).astype(float)
    gains = pnl[pnl > 0].sum()
    losses = abs(pnl[pnl < 0].sum())
    curve = pnl.cumsum()
    drawdown = (curve.cummax() - curve).max() if len(curve) else 0.0
    return {"trades": int(len(trades)), "closed": int(len(closed)), "open": open_rows, "pnl": float(pnl.sum()), "win_rate": float((pnl > 0).mean()) if len(pnl) else None, "profit_factor": float(gains / losses) if losses > 0 else None, "max_drawdown": float(drawdown) if len(curve) else None}


def show_metrics(metrics: dict[str, Any]) -> None:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Trades", metrics.get("trades", 0))
    c2.metric("Fechados", metrics.get("closed", 0))
    c3.metric("Abertos", metrics.get("open", 0))
    c4.metric("PnL Paper", round(float(metrics.get("pnl") or 0), 4))
    win = metrics.get("win_rate")
    c5.metric("Win Rate", "—" if win is None else f"{win:.1%}")
    pf = metrics.get("profit_factor")
    c6.metric("Profit Factor", "—" if pf is None else f"{pf:.2f}")


def show_freqtrade_snapshot_status(state: dict[str, Any]) -> None:
    payload = freqtrade_status_payload(state)
    if state.get("status") == "ok":
        st.success("SQLite Freqtrade lido via snapshot local.")
    elif state.get("status") == "missing":
        st.warning("SQLite Freqtrade não encontrado nos caminhos configurados.")
    else:
        st.error("Falha ao ler SQLite Freqtrade via snapshot local.")
    st.write(payload)


def dataframe(title: str, frame: pd.DataFrame, height: int = 360) -> None:
    st.subheader(title)
    if frame.empty:
        st.info("Sem dados disponíveis.")
    else:
        st.dataframe(frame, use_container_width=True, height=height)


st.title("SmartCrypto — Operação Paper")
page = st.sidebar.radio("Página", ["Visão geral", "Qlib / Predições", "AI Shadow", "Sinais", "Freqtrade", "Trades paper", "Performance", "Feedback dataset", "Logs", "Risco / Kill switch", "Evidências"])

freqtrade_state = load_freqtrade_trades_snapshot(PATHS.get("freqtrade_sqlite_candidates", []))
trades = freqtrade_state["trades"]
metrics = freqtrade_perf_metrics(trades)

if page == "Visão geral":
    show_metrics(metrics)
    primary = signal_frame(PATHS.get("primary_signals", "data/freqtrade_signals.json"))
    pinned = signal_frame(PATHS.get("pinned_signals", "data/runtime/active_freqtrade_signals.json"))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sinais primary", len(primary))
    c2.metric("Sinais pinned", len(pinned))
    c3.metric("Training rows", len(read_parquet(PATHS.get("training_dataset", "data/features/training_dataset.parquet"))))
    c4.metric("Master rows", len(read_parquet(PATHS.get("trades_master", "data/trades/trades_master.parquet"))))
    dataframe("Últimos trades Freqtrade", trades.head(20))
    dataframe("Últimos relatórios", latest_reports().head(20), height=260)

elif page == "Qlib / Predições":
    qlib_predictions_path = PATHS.get("qlib_predictions", "data/predictions/latest_qlib_predictions.parquet")
    freshness = inspect_qlib_prediction_freshness(
        qlib_predictions_path,
        max_allowed_age_minutes=90,
        max_input_data_age_minutes=15,
    )
    st.subheader("Freshness das predições Qlib")
    if freshness.get("freshness_status") == "fresh" and freshness.get("input_data_status") == "input_data_fresh":
        st.success("Predições Qlib e dados de entrada dentro da janela permitida.")
    elif freshness.get("freshness_status") != "fresh":
        st.error("Predições Qlib bloqueadas para geração de sinais: arquivo ausente, inválido ou stale.")
    else:
        st.error("Predições Qlib bloqueadas: dado de entrada usado na predição está ausente, inválido ou stale.")
    st.write(
        {
            "freshness_status": freshness.get("freshness_status"),
            "input_data_status": freshness.get("input_data_status"),
            "reason": freshness.get("reason"),
            "source_file": freshness.get("source_file"),
            "prediction_generated_at": freshness.get("prediction_generated_at"),
            "prediction_age_minutes": freshness.get("prediction_age_minutes"),
            "prediction_date": freshness.get("prediction_date"),
            "input_data_timestamp": freshness.get("input_data_timestamp"),
            "input_data_age_minutes": freshness.get("input_data_age_minutes"),
            "max_allowed_age_minutes": freshness.get("max_allowed_age_minutes"),
            "max_input_data_age_minutes": freshness.get("max_input_data_age_minutes"),
        }
    )
    dataframe("Predições Qlib", read_parquet(qlib_predictions_path), height=420)
    report = read_json("data/reports/phase21_qlib_walkforward_report.json")
    if report:
        st.subheader("Walk-forward")
        st.json(report)
    chart_dir = Path("data/reports/phase21_walkforward")
    if chart_dir.exists():
        for image in sorted(chart_dir.glob("*.png")):
            st.image(str(image), caption=image.name, use_container_width=True)

elif page == "AI Shadow":
    render_ai_shadow_panel(st)

elif page == "Sinais":
    st.subheader("Primary Signal")
    st.json(read_json(PATHS.get("primary_signals", "data/freqtrade_signals.json")))
    dataframe("Primary signals", signal_frame(PATHS.get("primary_signals", "data/freqtrade_signals.json")))
    st.subheader("Pinned Signal")
    st.json(read_json(PATHS.get("pinned_signals", "data/runtime/active_freqtrade_signals.json")))
    dataframe("Pinned signals", signal_frame(PATHS.get("pinned_signals", "data/runtime/active_freqtrade_signals.json")))

elif page == "Freqtrade":
    show_metrics(metrics)
    show_freqtrade_snapshot_status(freqtrade_state)
    dataframe("Tabela trades do Freqtrade", trades)

elif page == "Trades paper":
    show_metrics(metrics)
    show_freqtrade_snapshot_status(freqtrade_state)
    is_open = trades.get("is_open", pd.Series(dtype=int))
    dataframe("Trades abertos", trades.loc[is_open == 1] if not trades.empty else pd.DataFrame())
    dataframe("Trades fechados", trades.loc[is_open == 0] if not trades.empty else pd.DataFrame())

elif page == "Performance":
    show_metrics(metrics)
    show_freqtrade_snapshot_status(freqtrade_state)
    if not trades.empty:
        is_open = trades.get("is_open", pd.Series(dtype=int))
        closed = trades.loc[is_open == 0].copy()
        pnl_col = "close_profit_abs" if "close_profit_abs" in closed.columns else "realized_profit" if "realized_profit" in closed.columns else None
        if pnl_col and not closed.empty:
            closed["pnl_cum"] = closed[pnl_col].fillna(0).astype(float).cumsum()
            st.line_chart(closed.set_index("id")["pnl_cum"])
            dataframe("Performance por par", closed.groupby("pair")[pnl_col].agg(["count", "sum", "mean"]).reset_index())

elif page == "Feedback dataset":
    dataframe("Trades master", read_parquet(PATHS.get("trades_master", "data/trades/trades_master.parquet")))
    dataframe("Training dataset", read_parquet(PATHS.get("training_dataset", "data/features/training_dataset.parquet")).head(200))
    dataframe("SQLite tables", table_counts(PATHS.get("sqlite", "data/sqlite/trading_dataset.sqlite")))

elif page == "Logs":
    dataframe("Decision log", read_jsonl(PATHS.get("decision_log", "data/runtime/freqtrade_signal_decisions.jsonl"), tail=500))
    logfile = Path("freqtrade/user_data/logs/freqtrade-paper.log")
    if logfile.exists():
        st.subheader("Freqtrade log")
        st.code("\n".join(logfile.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]))

elif page == "Risco / Kill switch":
    st.subheader("Kill switch")
    kill_switch_path = PATHS.get("kill_switch", "data/runtime/kill_switch.json")
    kill_switch_classification = classify_kill_switch(kill_switch_path).to_dict()
    if kill_switch_classification["status"] == "active":
        st.error("Kill switch ATIVO.")
    elif kill_switch_classification["status"] == "invalid":
        st.error("Kill switch INVÁLIDO. Tratamento conservador aplicado.")
    elif kill_switch_classification["status"] in {"expired", "historical"}:
        st.warning("Kill switch EXPIRADO/HISTÓRICO.")
    elif kill_switch_classification["status"] == "missing":
        st.info("Kill switch AUSENTE.")
    else:
        st.success("Kill switch INATIVO.")
    st.write(
        {
            "label": kill_switch_classification.get("label"),
            "status": kill_switch_classification.get("status"),
            "active_now": kill_switch_classification.get("active_now"),
            "blocks_paper": kill_switch_classification.get("blocks_paper"),
            "blocks_live": kill_switch_classification.get("blocks_live"),
            "reason": kill_switch_classification.get("reason"),
            "created_at": kill_switch_classification.get("created_at"),
            "expires_at": kill_switch_classification.get("expires_at"),
            "age_minutes": kill_switch_classification.get("age_minutes"),
            "source_path": kill_switch_classification.get("source_path"),
            "parse_error": kill_switch_classification.get("parse_error"),
        }
    )
    st.json(read_json(kill_switch_path))
    st.subheader("Paper exit control")
    st.json(read_json(PATHS.get("paper_exit_control", "data/runtime/paper_exit_control.json")))
    st.subheader("Risk report")
    st.json(read_json("data/reports/phase20_risk_report.json"))

elif page == "Evidências":
    dataframe("Evidências ZIP", latest_evidence(), height=420)
    dataframe("Relatórios JSON", latest_reports(), height=420)
