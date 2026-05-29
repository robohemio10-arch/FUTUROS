from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


@dataclass(frozen=True)
class PaperFeedbackConfig:
    db_candidates: tuple[Path, ...]
    raw_export: Path
    closed_export_parquet: Path
    closed_export_csv: Path
    inbox_export_csv: Path
    open_positions_report: Path
    closed_feedback_report: Path
    output_summary: Path
    summary: Path
    expected_pairs: tuple[str, ...]
    max_open_trades: int


class PaperTradeLifecycleError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(path: str | Path = "config/paper_feedback.yml") -> PaperFeedbackConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    paths = payload.get("paths", {})
    freqtrade = payload.get("freqtrade", {})
    return PaperFeedbackConfig(
        db_candidates=tuple(Path(item) for item in paths.get("freqtrade_db_candidates", [])),
        raw_export=Path(paths["raw_export"]),
        closed_export_parquet=Path(paths["closed_export_parquet"]),
        closed_export_csv=Path(paths["closed_export_csv"]),
        inbox_export_csv=Path(paths["inbox_export_csv"]),
        open_positions_report=Path(paths["open_positions_report"]),
        closed_feedback_report=Path(paths["closed_feedback_report"]),
        output_summary=Path(paths["output_summary"]),
        summary=Path(paths["summary"]),
        expected_pairs=tuple(freqtrade.get("expected_pairs", [])),
        max_open_trades=int(freqtrade.get("max_open_trades", 2)),
    )


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def find_existing_db(candidates: tuple[Path, ...]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def create_sqlite_snapshot(source_path: Path, snapshot_dir: Path | None = None) -> Path:
    source = Path(source_path)
    if not source.exists():
        raise PaperTradeLifecycleError(f"sqlite_source_missing:{source}")
    if not source.is_file():
        raise PaperTradeLifecycleError(f"sqlite_source_not_file:{source}")

    target_dir = Path(snapshot_dir) if snapshot_dir is not None else Path(tempfile.mkdtemp(prefix="phase14_sqlite_snapshot_"))
    target_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = target_dir / source.name
    try:
        shutil.copy2(source, snapshot_path)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{source}{suffix}")
            if sidecar.exists() and sidecar.is_file():
                shutil.copy2(sidecar, Path(f"{snapshot_path}{suffix}"))
    except Exception as exc:
        raise PaperTradeLifecycleError(f"sqlite_snapshot_copy_failed:{source}:{exc}") from exc
    return snapshot_path


def cleanup_sqlite_snapshot(snapshot_path: Path) -> None:
    snapshot = Path(snapshot_path)
    for _ in range(5):
        locked = False
        for candidate in (snapshot, Path(f"{snapshot}-wal"), Path(f"{snapshot}-shm"), Path(f"{snapshot}-journal")):
            try:
                if candidate.exists():
                    candidate.unlink()
            except OSError:
                locked = True
        if not locked:
            break
        time.sleep(0.05)
    parent = snapshot.parent
    if parent.name.startswith("phase14_sqlite_snapshot_") and parent.exists():
        for _ in range(5):
            shutil.rmtree(parent, ignore_errors=True)
            if not parent.exists():
                break
            time.sleep(0.05)


def read_trades(db_path: Path, use_snapshot: bool = True) -> pd.DataFrame:
    source = Path(db_path)
    snapshot_path: Path | None = None
    read_path = source
    try:
        if use_snapshot:
            snapshot_path = create_sqlite_snapshot(source)
            read_path = snapshot_path
        with sqlite3.connect(read_path) as conn:
            tables = pd.read_sql_query("select name from sqlite_master where type='table'", conn)
            if "trades" not in set(tables["name"].astype(str)):
                return pd.DataFrame()
            return pd.read_sql_query("select * from trades order by id", conn)
    except PaperTradeLifecycleError:
        raise
    except Exception as exc:
        mode = "snapshot" if use_snapshot else "direct"
        raise PaperTradeLifecycleError(f"sqlite_read_failed:{mode}:{source}:{exc}") from exc
    finally:
        if snapshot_path is not None:
            cleanup_sqlite_snapshot(snapshot_path)


def db_read_blocked_report(reason: str, db_path: Path, *, created_at: str | None = None) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "freqtrade_db_read_failed",
        "db_path": str(db_path),
        "error": reason,
        "db_snapshot_used": True,
        "created_at": created_at or utc_now(),
    }


def inspect_open_positions(config: PaperFeedbackConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    db_path = find_existing_db(cfg.db_candidates)

    if db_path is None:
        report = {
            "status": "blocked",
            "reason": "freqtrade_db_not_found",
            "db_candidates": [str(item) for item in cfg.db_candidates],
            "rows": 0,
            "open_rows": 0,
            "closed_rows": 0,
            "saturated": False,
            "created_at": utc_now(),
        }
        write_json(cfg.open_positions_report, report)
        return report

    try:
        trades = read_trades(db_path, use_snapshot=True)
    except PaperTradeLifecycleError as exc:
        report = {
            **db_read_blocked_report(str(exc), db_path),
            "rows": 0,
            "open_rows": 0,
            "closed_rows": 0,
            "saturated": False,
        }
        write_json(cfg.open_positions_report, report)
        return report
    if trades.empty:
        report = {
            "status": "ok",
            "reason": "trades_table_empty",
        "db_path": str(db_path),
        "db_snapshot_used": True,
        "rows": 0,
            "open_rows": 0,
            "closed_rows": 0,
            "saturated": False,
            "created_at": utc_now(),
        }
        write_json(cfg.open_positions_report, report)
        return report

    open_trades = trades[trades.get("is_open", 0).fillna(0).astype(int) == 1].copy()
    closed_trades = trades[trades.get("is_open", 0).fillna(0).astype(int) == 0].copy()

    recent_columns = [
        "id",
        "pair",
        "is_open",
        "is_short",
        "open_rate",
        "close_rate",
        "open_date",
        "close_date",
        "enter_tag",
        "exit_reason",
        "realized_profit",
        "close_profit",
    ]
    available = [column for column in recent_columns if column in trades.columns]

    report = {
        "status": "ok",
        "reason": None,
        "db_path": str(db_path),
        "db_snapshot_used": True,
        "rows": int(len(trades)),
        "open_rows": int(len(open_trades)),
        "closed_rows": int(len(closed_trades)),
        "max_open_trades": cfg.max_open_trades,
        "saturated": int(len(open_trades)) >= cfg.max_open_trades,
        "expected_pairs": list(cfg.expected_pairs),
        "open_pairs": sorted(open_trades["pair"].dropna().astype(str).unique().tolist()) if "pair" in open_trades else [],
        "recent": trades.tail(20)[available].to_dict(orient="records"),
        "created_at": utc_now(),
    }
    write_json(cfg.open_positions_report, report)
    return report


def _safe_series(frame: pd.DataFrame, column: str, default: Any = None) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series([default] * len(frame), index=frame.index)


def _format_symbol(pair: Any) -> str:
    text = str(pair or "")
    return text.replace("/USDT:USDT", "USDT").replace("/", "").replace(":", "")


def _side_from_is_short(value: Any) -> str:
    try:
        return "short" if int(value) == 1 else "long"
    except Exception:
        return "unknown"


def normalize_closed_trades(closed: pd.DataFrame) -> pd.DataFrame:
    if closed.empty:
        return pd.DataFrame(
            columns=[
                "moeda",
                "fechar_side",
                "leverage",
                "order_id",
                "pnl_fechado",
                "taxa_lucros_perdas_fechados_pct",
                "preco_abertura",
                "preco_fechamento",
                "volume_posicao",
                "volume_fechado",
                "horario_abertura",
                "horario_fechamento",
                "taxa_1",
                "preco_transacao",
                "volume_transacao",
                "direcao_liquidez",
                "taxa_2",
                "horario_transacao",
            ]
        )

    pair = _safe_series(closed, "pair", "")
    is_short = _safe_series(closed, "is_short", 0)

    normalized = pd.DataFrame(index=closed.index)
    normalized["moeda"] = pair.map(_format_symbol)
    normalized["fechar_side"] = is_short.map(_side_from_is_short)
    normalized["leverage"] = _safe_series(closed, "leverage", 1).fillna(1)
    normalized["order_id"] = _safe_series(closed, "id", "").map(lambda value: f"freqtrade-paper-{value}")
    normalized["pnl_fechado"] = _safe_series(closed, "close_profit_abs", None)
    if normalized["pnl_fechado"].isna().all():
        normalized["pnl_fechado"] = _safe_series(closed, "realized_profit", None)
    normalized["taxa_lucros_perdas_fechados_pct"] = _safe_series(closed, "close_profit", None)
    normalized["preco_abertura"] = _safe_series(closed, "open_rate", None)
    normalized["preco_fechamento"] = _safe_series(closed, "close_rate", None)
    normalized["volume_posicao"] = _safe_series(closed, "amount", None)
    normalized["volume_fechado"] = _safe_series(closed, "amount", None)
    normalized["horario_abertura"] = _safe_series(closed, "open_date", None)
    normalized["horario_fechamento"] = _safe_series(closed, "close_date", None)
    normalized["taxa_1"] = _safe_series(closed, "fee_open_cost", 0).fillna(0)
    normalized["preco_transacao"] = _safe_series(closed, "open_rate", None)
    normalized["volume_transacao"] = _safe_series(closed, "amount", None)
    normalized["direcao_liquidez"] = _safe_series(closed, "enter_tag", None)
    normalized["taxa_2"] = _safe_series(closed, "fee_close_cost", 0).fillna(0)
    normalized["horario_transacao"] = _safe_series(closed, "close_date", None)
    return normalized


def collect_closed_feedback(config: PaperFeedbackConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    db_path = find_existing_db(cfg.db_candidates)

    if db_path is None:
        report = {
            "status": "blocked",
            "reason": "freqtrade_db_not_found",
            "db_candidates": [str(item) for item in cfg.db_candidates],
            "raw_rows": 0,
            "closed_rows": 0,
            "exported_to_inbox": False,
            "created_at": utc_now(),
        }
        write_json(cfg.closed_feedback_report, report)
        return report

    try:
        trades = read_trades(db_path, use_snapshot=True)
    except PaperTradeLifecycleError as exc:
        report = {
            **db_read_blocked_report(str(exc), db_path),
            "raw_rows": 0,
            "closed_rows": 0,
            "exported_to_inbox": False,
        }
        write_json(cfg.closed_feedback_report, report)
        return report
    ensure_parent(cfg.raw_export)
    trades.to_parquet(cfg.raw_export, index=False)

    if trades.empty:
        report = {
            "status": "no_trades",
            "reason": "trades_table_empty",
            "db_path": str(db_path),
            "db_snapshot_used": True,
            "raw_rows": 0,
            "closed_rows": 0,
            "raw_export": str(cfg.raw_export),
            "exported_to_inbox": False,
            "created_at": utc_now(),
        }
        write_json(cfg.closed_feedback_report, report)
        return report

    closed = trades[trades.get("is_open", 0).fillna(0).astype(int) == 0].copy()
    normalized = normalize_closed_trades(closed)

    if normalized.empty:
        report = {
            "status": "waiting",
            "reason": "no_closed_trades_yet",
            "db_path": str(db_path),
            "db_snapshot_used": True,
            "raw_rows": int(len(trades)),
            "open_rows": int((trades.get("is_open", 0).fillna(0).astype(int) == 1).sum()),
            "closed_rows": 0,
            "raw_export": str(cfg.raw_export),
            "exported_to_inbox": False,
            "created_at": utc_now(),
        }
        write_json(cfg.closed_feedback_report, report)
        return report

    ensure_parent(cfg.closed_export_parquet)
    normalized.to_parquet(cfg.closed_export_parquet, index=False)
    normalized.to_csv(cfg.closed_export_csv, index=False, encoding="utf-8-sig")
    ensure_parent(cfg.inbox_export_csv)
    normalized.to_csv(cfg.inbox_export_csv, index=False, encoding="utf-8-sig")

    report = {
        "status": "ok",
        "reason": None,
        "db_path": str(db_path),
        "db_snapshot_used": True,
        "raw_rows": int(len(trades)),
        "open_rows": int((trades.get("is_open", 0).fillna(0).astype(int) == 1).sum()),
        "closed_rows": int(len(normalized)),
        "raw_export": str(cfg.raw_export),
        "closed_export_parquet": str(cfg.closed_export_parquet),
        "closed_export_csv": str(cfg.closed_export_csv),
        "inbox_export_csv": str(cfg.inbox_export_csv),
        "exported_to_inbox": True,
        "created_at": utc_now(),
    }
    write_json(cfg.closed_feedback_report, report)
    return report


def inspect_outputs(config: PaperFeedbackConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()

    def file_info(path: Path) -> dict[str, Any]:
        exists = path.exists()
        payload: dict[str, Any] = {"path": str(path), "exists": exists}
        if exists:
            payload["size_bytes"] = path.stat().st_size
            if path.suffix == ".json":
                try:
                    payload["content"] = json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:
                    payload["json_error"] = str(exc)
            elif path.suffix == ".parquet":
                try:
                    frame = pd.read_parquet(path)
                    payload["rows"] = int(len(frame))
                    payload["columns"] = list(frame.columns)
                except Exception as exc:
                    payload["read_error"] = str(exc)
            elif path.suffix == ".csv":
                try:
                    frame = pd.read_csv(path)
                    payload["rows"] = int(len(frame))
                    payload["columns"] = list(frame.columns)
                except Exception as exc:
                    payload["read_error"] = str(exc)
        return payload

    open_report = file_info(cfg.open_positions_report)
    closed_report = file_info(cfg.closed_feedback_report)
    raw_export = file_info(cfg.raw_export)
    closed_parquet = file_info(cfg.closed_export_parquet)
    inbox_csv = file_info(cfg.inbox_export_csv)

    phase14_status = "ok"
    reason = None

    closed_content = closed_report.get("content") or {}
    open_content = open_report.get("content") or {}

    if closed_content.get("status") in {"blocked"}:
        phase14_status = "blocked"
        reason = closed_content.get("reason")
    elif closed_content.get("status") in {"waiting", "no_trades"}:
        phase14_status = "waiting"
        reason = closed_content.get("reason")

    summary = {
        "open_positions_report": open_report,
        "closed_feedback_report": closed_report,
        "raw_export": raw_export,
        "closed_export_parquet": closed_parquet,
        "inbox_export_csv": inbox_csv,
        "phase14_status": {
            "status": phase14_status,
            "reason": reason,
            "open_rows": open_content.get("open_rows"),
            "closed_rows": closed_content.get("closed_rows"),
            "saturated": open_content.get("saturated"),
            "exported_to_inbox": closed_content.get("exported_to_inbox"),
        },
        "created_at": utc_now(),
    }

    write_json(cfg.output_summary, summary)
    return summary


def collect_summary(config: PaperFeedbackConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    output = inspect_outputs(cfg)
    summary = {
        "status": "ok",
        "phase": "phase14_paper_trade_lifecycle_feedback",
        "output_summary": str(cfg.output_summary),
        "phase14_status": output.get("phase14_status", {}),
        "created_at": utc_now(),
    }
    write_json(cfg.summary, summary)
    return summary
