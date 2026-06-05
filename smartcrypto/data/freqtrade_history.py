from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import sqlite3


@dataclass(frozen=True)
class FreqtradeHistoryReport:
    status: str
    reason: str | None
    db_path: str
    raw_rows: int
    closed_rows: int
    output_raw_path: str | None
    output_compatible_path: str | None
    inbox_export_path: str | None
    created_at: str


def collect_freqtrade_paper_history(
    db_path: str | Path = "freqtrade/user_data/tradesv3.paper.sqlite",
    output_raw_path: str | Path = "data/trades/freqtrade_paper_trades_raw.parquet",
    output_compatible_path: str | Path = "data/trades/freqtrade_paper_trades_smartcrypto.xlsx",
    inbox_export: bool = True,
    report_path: str | Path = "data/reports/phase7_paper_history_report.json",
) -> FreqtradeHistoryReport:
    candidates = [
        Path(db_path),
        Path("/app/freqtrade_user_data/tradesv3.paper.sqlite"),
        Path("freqtrade/user_data/tradesv3.paper.sqlite"),
    ]
    resolved_db = next((path for path in candidates if path.exists()), Path(db_path))

    if not resolved_db.exists():
        report = FreqtradeHistoryReport(
            status="blocked",
            reason="freqtrade_db_not_found",
            db_path=str(resolved_db),
            raw_rows=0,
            closed_rows=0,
            output_raw_path=None,
            output_compatible_path=None,
            inbox_export_path=None,
            created_at=_utc_now(),
        )
        _write_json(report_path, asdict(report))
        return report

    with sqlite3.connect(resolved_db) as con:
        tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table'", con)["name"].tolist()
        if "trades" not in tables:
            report = FreqtradeHistoryReport(
                status="blocked",
                reason="trades_table_not_found",
                db_path=str(resolved_db),
                raw_rows=0,
                closed_rows=0,
                output_raw_path=None,
                output_compatible_path=None,
                inbox_export_path=None,
                created_at=_utc_now(),
            )
            _write_json(report_path, asdict(report))
            return report
        raw = pd.read_sql_query("SELECT * FROM trades", con)

    output_raw = Path(output_raw_path)
    output_raw.parent.mkdir(parents=True, exist_ok=True)
    raw.to_parquet(output_raw, index=False)

    if raw.empty:
        report = FreqtradeHistoryReport(
            status="no_trades",
            reason="trades_table_empty",
            db_path=str(resolved_db),
            raw_rows=0,
            closed_rows=0,
            output_raw_path=str(output_raw),
            output_compatible_path=None,
            inbox_export_path=None,
            created_at=_utc_now(),
        )
        _write_json(report_path, asdict(report))
        return report

    closed = _closed_trades(raw)
    if closed.empty:
        report = FreqtradeHistoryReport(
            status="no_closed_trades",
            reason="no_closed_paper_trades_yet",
            db_path=str(resolved_db),
            raw_rows=int(len(raw)),
            closed_rows=0,
            output_raw_path=str(output_raw),
            output_compatible_path=None,
            inbox_export_path=None,
            created_at=_utc_now(),
        )
        _write_json(report_path, asdict(report))
        return report

    compatible = _to_smartcrypto_schema(closed)
    output_compatible = Path(output_compatible_path)
    output_compatible.parent.mkdir(parents=True, exist_ok=True)
    compatible.to_excel(output_compatible, index=False)

    inbox_path = None
    if inbox_export:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        inbox_path = Path(f"data/trades/inbox/freqtrade_paper_trades_{run_id}.xlsx")
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        compatible.to_excel(inbox_path, index=False)

    report = FreqtradeHistoryReport(
        status="ok",
        reason=None,
        db_path=str(resolved_db),
        raw_rows=int(len(raw)),
        closed_rows=int(len(compatible)),
        output_raw_path=str(output_raw),
        output_compatible_path=str(output_compatible),
        inbox_export_path=str(inbox_path) if inbox_path else None,
        created_at=_utc_now(),
    )
    _write_json(report_path, asdict(report))
    return report


def _closed_trades(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()
    if "is_open" in frame.columns:
        frame = frame.loc[frame["is_open"].astype(int).eq(0)].copy()
    if "close_date" in frame.columns:
        frame = frame.loc[frame["close_date"].notna()].copy()
    return frame.reset_index(drop=True)


def _to_smartcrypto_schema(trades: pd.DataFrame) -> pd.DataFrame:
    def col(name: str, default: Any = None) -> pd.Series:
        if name in trades.columns:
            return trades[name]
        return pd.Series([default] * len(trades), index=trades.index)

    pair = col("pair", "")
    is_short = col("is_short", False).fillna(False).astype(bool) if "is_short" in trades.columns else pd.Series([False] * len(trades), index=trades.index)
    side = is_short.map({True: "short", False: "long"})

    ids = col("id", range(len(trades))).astype(str)
    close_profit_abs = pd.to_numeric(col("close_profit_abs", 0.0), errors="coerce").fillna(0.0)
    close_profit = pd.to_numeric(col("close_profit", 0.0), errors="coerce").fillna(0.0)
    amount = pd.to_numeric(col("amount", 0.0), errors="coerce").fillna(0.0)
    open_rate = pd.to_numeric(col("open_rate", 0.0), errors="coerce").fillna(0.0)
    close_rate = pd.to_numeric(col("close_rate", 0.0), errors="coerce").fillna(0.0)

    output = pd.DataFrame(
        {
            "moeda": pair.astype(str),
            "fechar_side": side,
            "leverage": pd.to_numeric(col("leverage", 1.0), errors="coerce").fillna(1.0),
            "order_id": "freqtrade_" + ids,
            "pnl_fechado": close_profit_abs,
            "taxa_lucros_perdas_fechados_pct": close_profit * 100.0,
            "preco_abertura": open_rate,
            "preco_fechamento": close_rate,
            "volume_posicao": amount,
            "volume_fechado": amount,
            "horario_abertura": col("open_date", None),
            "horario_fechamento": col("close_date", None),
            "taxa_1": pd.to_numeric(col("fee_open", 0.0), errors="coerce").fillna(0.0),
            "preco_transacao": close_rate,
            "volume_transacao": amount,
            "direcao_liquidez": col("enter_tag", ""),
            "taxa_2": pd.to_numeric(col("fee_close", 0.0), errors="coerce").fillna(0.0),
            "horario_transacao": col("close_date", None),
        }
    )
    return output


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    import json

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
