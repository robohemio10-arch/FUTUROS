"""Research-only OCR trades master and real candle alignment.

This module is deliberately read-only by default. Runtime/data reads require
``allow_runtime_read=True``. It never changes Freqtrade, RiskManager, Qlib,
IA Shadow, SQLite operational state, model registries, live/canary settings or
order submission surfaces.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None  # type: ignore[assignment]

SCHEMA_VERSION = "ocr_master_candle_aligned_oos_research_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION = "MANTER_EM_RESEARCH"
HYPOTHESES = ["H1", "H2", "H6"]
SLICE_DIMENSIONS = ["symbol", "side", "day", "hour", "duration_bucket", "regime_bucket"]
EXPECTED_TRADE_VALUE_CONTRACT = (
    "expected_trade_value = Qlib_expected_return_net × Shadow_probability_quality × "
    "Regime_confidence - Estimated_fee - Estimated_spread - Estimated_slippage - "
    "Latency_penalty - Drawdown_penalty - Drift_penalty"
)
H6_10M = -0.0038501215827868
H6_30M = -0.0060685748963285
CANDIDATE_RULE = "lb_10m_ret_close <= -0.0038501215827868 AND lb_30m_ret_close <= -0.0060685748963285"
CANONICAL_1M_CANDLE_FILES = (
    Path("data/raw/binance_futures_klines/BTCUSDT_1m_20251230_20261208.csv"),
    Path("data/raw/binance_futures_klines/ETHUSDT_1m_20251230_20261208.csv"),
    Path("data/raw/binance_futures_klines/BTCUSDT_1m_20260106_20260519.parquet"),
    Path("data/raw/binance_futures_klines/ETHUSDT_1m_20260106_20260519.parquet"),
)
FORBIDDEN_ACTIONS = [
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "registrar ou promover candidate rule",
    "promover modelo",
    "executar treino operacional",
    "habilitar live ou canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever artefatos em data/runtime/reports/logs/freqtrade por padrão",
]
SAFETY_FALSE = {
    "applies_feedback_to_ai_shadow": False,
    "applies_shadow_rules": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "can_promote_model": False,
    "can_promote_rules": False,
    "canary_release_allowed": False,
    "changes_model": False,
    "changes_risk": False,
    "exchange_private_access": False,
    "executes_orchestrator": False,
    "executes_scheduler": False,
    "executes_stage_builders": False,
    "live_release_allowed": False,
    "live_trading_enabled": False,
    "operational_authority": False,
    "order_submission_enabled": False,
    "readiness_release_authority": False,
    "ready_for_candidate_registry": False,
    "real_order_submission_enabled": False,
    "registers_candidate_rules": False,
    "release_authority": False,
    "remediation_application_allowed": False,
    "runs_training": False,
    "sends_orders": False,
    "updates_ai_shadow_runtime": False,
    "updates_freqtrade": False,
    "updates_qlib_runtime": False,
    "updates_risk_manager": False,
    "writes_data": False,
    "writes_parquet": False,
    "writes_runtime": False,
    "writes_sqlite": False,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


def _lookup(columns: Iterable[str]) -> dict[str, str]:
    return {str(col).strip().lower(): str(col) for col in columns}


def _first(columns: Iterable[str], candidates: Sequence[str]) -> str | None:
    lookup = _lookup(columns)
    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]
    return None


def _parse_number(value: Any) -> float | None:
    if value is None:
        return None
    if pd is not None and pd.isna(value):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = (
        text.replace("USDT", "")
        .replace("BTC", "")
        .replace("ETH", "")
        .replace("%", "")
        .replace("+", "")
        .strip()
    )
    text = text.replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def _symbol(value: Any) -> str:
    text = str(value or "").upper().strip()
    text = text.replace("_", "").replace("/", "").replace(":USDT", "").replace(":", "")
    if "BTC" in text:
        return "BTCUSDT"
    if "ETH" in text:
        return "ETHUSDT"
    return text or "UNKNOWN"


def _side(value: Any) -> str:
    text = str(value or "").lower().strip()
    if "long" in text:
        return "long"
    if "short" in text:
        return "short"
    return text or "unknown"


def _read_table(path: Path) -> Any:
    suffix = path.suffix.lower()
    if pd is None:
        if suffix != ".csv":
            raise RuntimeError("pandas_unavailable_for_non_csv_source")
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".jsonl":
        return pd.read_json(path, lines=True)
    if suffix == ".json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict):
            for key in ("rows", "data", "records", "trades", "candles"):
                if isinstance(payload.get(key), list):
                    return pd.DataFrame(payload[key])
            return pd.DataFrame([payload])
    raise ValueError(f"unsupported_source_suffix:{suffix}")


def _dt(series: Any) -> Any:
    if pd is None:
        return series
    numeric = pd.to_numeric(series, errors="coerce")
    non_null = numeric.dropna()
    if not non_null.empty and numeric.notna().mean() > 0.8:
        median = float(non_null.abs().median())
        if 20_000 <= median <= 80_000:
            return pd.to_datetime(numeric, errors="coerce", unit="D", origin="1899-12-30", utc=True)
        unit = "ms" if median > 10_000_000_000 else "s"
        return pd.to_datetime(numeric, errors="coerce", unit=unit, utc=True)
    return pd.to_datetime(series, errors="coerce", utc=True)


def _normalize_trades(raw: Any) -> Any:
    if pd is None:
        return raw
    df = raw.copy()
    symbol_col = _first(df.columns, ["symbol", "pair", "asset", "ticker", "11_moeda", "moeda"])
    side_col = _first(df.columns, ["side", "direction", "position_side", "trade_side", "12_fechar_long_short", "fechar_long_short"])
    open_col = _first(
        df.columns,
        ["open_time_utc", "open_time", "open_date", "entry_time", "datetime", "timestamp", "date", "7_horario_de_abertura", "horario_de_abertura"],
    )
    close_col = _first(df.columns, ["close_time_utc", "close_time", "close_date", "exit_time", "8_horario_de_fechamento", "horario_de_fechamento"])
    pnl_col = _first(
        df.columns,
        ["pnl_usdt", "net_pnl_usdt", "raw_pnl_usdt", "profit_abs", "close_profit_abs", "realized_pnl_usdt", "pnl", "1_pnl_fechado", "pnl_fechado"],
    )
    entry_price_col = _first(df.columns, ["entry_price", "open_rate", "open_price", "3_preco_de_abertura", "preco_de_abertura"])
    exit_price_col = _first(df.columns, ["exit_price", "close_rate", "close_price", "4_preco_de_fechamento", "preco_de_fechamento"])
    exit_col = _first(df.columns, ["exit_reason", "reason", "exit_tag", "close_reason", "final_bucket", "candidate_action_final"])
    dur_col = _first(df.columns, ["duration_minutes", "duration_min", "trade_duration", "duration"])
    order_col = _first(df.columns, ["trade_id", "order_id", "10_numero_do_pedido", "numero_do_pedido", "fingerprint_operacional"])

    out = pd.DataFrame(index=df.index)
    out["source_row_index"] = df.index.astype(int)
    out["trade_id"] = df[order_col].astype(str) if order_col else out["source_row_index"].astype(str)
    out["symbol"] = df[symbol_col].map(_symbol) if symbol_col else "UNKNOWN"
    out["side"] = df[side_col].map(_side) if side_col else "unknown"
    out["open_time"] = _dt(df[open_col]) if open_col else pd.NaT
    out["close_time"] = _dt(df[close_col]) if close_col else pd.NaT
    out["pnl_usdt"] = df[pnl_col].map(_parse_number) if pnl_col else math.nan
    out["entry_price"] = df[entry_price_col].map(_parse_number) if entry_price_col else math.nan
    out["exit_price"] = df[exit_price_col].map(_parse_number) if exit_price_col else math.nan
    out["exit_reason"] = df[exit_col].astype(str).str.lower().str.strip() if exit_col else "unknown"
    if dur_col:
        out["duration_minutes"] = pd.to_numeric(df[dur_col], errors="coerce")
    else:
        out["duration_minutes"] = (out["close_time"] - out["open_time"]).dt.total_seconds() / 60.0
    out["day"] = out["open_time"].dt.date.astype(str)
    out["hour"] = out["open_time"].dt.hour.fillna(-1).astype(int)
    return out.dropna(subset=["open_time", "pnl_usdt"])


def _schema_status(columns: Sequence[str], source_type: str) -> str:
    lower = {str(col).lower() for col in columns}
    if source_type == "legacy_trade_dataset":
        has_symbol = bool(lower & {"symbol", "pair", "11_moeda", "moeda"})
        has_time = bool(lower & {"open_time_utc", "open_time", "open_date", "entry_time", "datetime", "timestamp", "date", "7_horario_de_abertura"})
        has_close_time = bool(lower & {"close_time_utc", "close_time", "close_date", "exit_time", "8_horario_de_fechamento"})
        has_pnl = bool(lower & {"pnl_usdt", "net_pnl_usdt", "raw_pnl_usdt", "profit_abs", "close_profit_abs", "pnl", "1_pnl_fechado"})
        return "candidate_trade_schema" if has_symbol and has_time and has_pnl else "partial_trade_schema" if has_symbol and has_close_time and has_pnl else "unknown_schema"
    if source_type == "candles":
        has_time = bool(lower & {"open_time_utc", "open_time", "timestamp", "date", "datetime", "time", "ts", "ts_ms"})
        has_close = bool(lower & {"close", "c"})
        return "candidate_candle_schema" if has_time and has_close else "unknown_schema"
    return "unknown_schema"


def _infer_symbol(path: Path) -> str | None:
    name = path.name.upper()
    if "BTC" in name:
        return "BTCUSDT"
    if "ETH" in name:
        return "ETHUSDT"
    return None


def _normalize_candles(raw: Any, fallback_symbol: str | None) -> Any:
    if pd is None:
        return raw
    df = raw.copy()
    symbol_col = _first(df.columns, ["symbol", "pair", "asset", "ticker"])
    ts_col = _first(df.columns, ["ts", "timestamp", "open_time_utc", "open_time", "date", "datetime", "time", "ts_ms"])
    close_col = _first(df.columns, ["close", "close_price", "c"])
    open_col = _first(df.columns, ["open", "open_price", "o"])
    high_col = _first(df.columns, ["high", "h"])
    low_col = _first(df.columns, ["low", "l"])
    volume_col = _first(df.columns, ["volume", "v"])
    if ts_col is None or close_col is None:
        return pd.DataFrame(columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
    out = pd.DataFrame(index=df.index)
    out["symbol"] = df[symbol_col].map(_symbol) if symbol_col else fallback_symbol or "UNKNOWN"
    out["timestamp"] = _dt(df[ts_col])
    out["close"] = pd.to_numeric(df[close_col], errors="coerce")
    out["open"] = pd.to_numeric(df[open_col], errors="coerce") if open_col else out["close"]
    out["high"] = pd.to_numeric(df[high_col], errors="coerce") if high_col else out[["open", "close"]].max(axis=1)
    out["low"] = pd.to_numeric(df[low_col], errors="coerce") if low_col else out[["open", "close"]].min(axis=1)
    out["volume"] = pd.to_numeric(df[volume_col], errors="coerce") if volume_col else 0.0
    return out.dropna(subset=["timestamp", "close"]).sort_values(["symbol", "timestamp"])


def _discover_candle_paths(root: Path, candle_roots: Sequence[str | Path]) -> list[Path]:
    suffixes = {".csv", ".parquet", ".json", ".jsonl"}
    explicit_files: list[Path] = []
    for value in candle_roots:
        base = Path(value)
        if not base.is_absolute():
            base = root / base
        if base.is_file() and base.suffix.lower() in suffixes:
            explicit_files.append(base)
    if explicit_files:
        return sorted({str(path.resolve()): path for path in explicit_files}.values(), key=lambda p: str(p).lower())

    canonical = [root / rel for rel in CANONICAL_1M_CANDLE_FILES if (root / rel).exists()]
    large_canonical = [path for path in canonical if "20251230_20261208" in path.name]
    if large_canonical:
        return sorted(large_canonical, key=lambda p: str(p).lower())
    if canonical:
        return sorted(canonical, key=lambda p: str(p).lower())

    paths: list[Path] = []
    for value in candle_roots:
        base = Path(value)
        if not base.is_absolute():
            base = root / base
        if not base.exists():
            continue
        if base.is_file() and base.suffix.lower() in suffixes:
            paths.append(base)
            continue
        for item in base.rglob("*"):
            if not item.is_file() or item.suffix.lower() not in suffixes:
                continue
            location = f"{item.parent} {item.name}".lower()
            is_1m = "1m" in location and "15s" not in location
            has_symbol = "btcusdt" in location or "ethusdt" in location
            if is_1m and has_symbol:
                paths.append(item)
    return sorted({str(path.resolve()): path for path in paths}.values(), key=lambda p: str(p).lower())[:20]


def _audit(path: Path, root: Path, source_type: str, rows: int | None, columns: Sequence[str]) -> dict[str, Any]:
    return {
        "path": _rel(path, root),
        "exists": path.exists(),
        "suffix": path.suffix.lower(),
        "source_type": source_type,
        "row_count": rows,
        "sha256": _sha256(path) if path.exists() and path.is_file() else None,
        "schema_status": _schema_status(columns, source_type),
        "columns": list(columns),
    }


def _load_candles(root: Path, candle_roots: Sequence[str | Path]) -> tuple[Any, list[dict[str, Any]], list[str]]:
    if pd is None:
        return [], [], ["pandas_unavailable"]
    frames: list[Any] = []
    audits: list[dict[str, Any]] = []
    warnings: list[str] = []
    for path in _discover_candle_paths(root, candle_roots):
        try:
            raw = _read_table(path)
            normalized = _normalize_candles(raw, _infer_symbol(path))
            audits.append(_audit(path, root, "candles", len(normalized), list(raw.columns)))
            if not normalized.empty and set(normalized["symbol"].unique()) & {"BTCUSDT", "ETHUSDT"}:
                frames.append(normalized)
        except Exception as exc:
            warnings.append(f"candle_load_error:{_rel(path, root)}:{exc.__class__.__name__}")
    if not frames:
        return pd.DataFrame(columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"]), audits, warnings
    candles = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["symbol", "timestamp"], keep="last").sort_values(["symbol", "timestamp"])
    return candles, audits, warnings


def _duration_bucket(minutes: float) -> str:
    if not math.isfinite(minutes):
        return "unknown"
    if minutes <= 30:
        return "le_30m"
    if minutes <= 120:
        return "le_2h"
    if minutes <= 360:
        return "le_6h"
    return "gt_6h"


def _regime(lb10: float | None, lb30: float | None) -> str:
    if lb10 is None or lb30 is None:
        return "unknown"
    if lb10 > 0 and lb30 > 0:
        return "bullish_short"
    if lb10 < 0 and lb30 < 0:
        return "bearish_short"
    if lb10 > 0 > lb30:
        return "reversal_up"
    if lb10 < 0 < lb30:
        return "pullback_down"
    return "flat"


def _as_int_ns(timestamp: Any) -> int | None:
    if pd is None or pd.isna(timestamp):
        return None
    return int(pd.Timestamp(timestamp).value)


def _lookback_from_arrays(times: Any, closes: Any, entry_ns: int, minutes: int) -> float | None:
    current_idx = int(times.searchsorted(entry_ns, side="right") - 1)
    if current_idx < 0:
        return None
    target_ns = entry_ns - int(pd.Timedelta(minutes=minutes).value)
    past_idx = int(times.searchsorted(target_ns, side="right") - 1)
    if past_idx < 0:
        return None
    current_close = float(closes[current_idx])
    past_close = float(closes[past_idx])
    if past_close == 0 or not math.isfinite(past_close) or not math.isfinite(current_close):
        return None
    return (current_close / past_close) - 1.0


def _entry_candle_from_arrays(times: Any, closes: Any, entry_ns: int) -> tuple[Any, float | None, float | None]:
    idx = int(times.searchsorted(entry_ns, side="right") - 1)
    if idx < 0:
        return None, None, None
    ts = pd.Timestamp(int(times[idx]), tz="UTC")
    age_seconds = (entry_ns - int(times[idx])) / 1_000_000_000
    return ts.isoformat(), float(closes[idx]), float(age_seconds)


def _align(trades: Any, candles: Any, max_entry_candle_age_seconds: int = 300) -> Any:
    if pd is None or trades.empty or candles.empty:
        return pd.DataFrame()
    candle_groups: dict[str, tuple[Any, Any]] = {}
    for symbol, group in candles.groupby("symbol"):
        ordered = group.sort_values("timestamp").dropna(subset=["timestamp", "close"])
        if ordered.empty:
            continue
        times = pd.to_datetime(ordered["timestamp"], utc=True).astype("int64").to_numpy()
        closes = pd.to_numeric(ordered["close"], errors="coerce").to_numpy()
        candle_groups[str(symbol)] = (times, closes)

    rows: list[dict[str, Any]] = []
    for _, trade in trades.iterrows():
        symbol = str(trade.get("symbol", "UNKNOWN"))
        arrays = candle_groups.get(symbol)
        entry_ns = _as_int_ns(trade.get("open_time"))
        if arrays is None or entry_ns is None:
            continue
        times, closes = arrays
        entry_candle_ts, entry_candle_close, entry_age_seconds = _entry_candle_from_arrays(times, closes, entry_ns)
        if entry_age_seconds is None or entry_age_seconds > max_entry_candle_age_seconds:
            continue
        lb5 = _lookback_from_arrays(times, closes, entry_ns, 5)
        lb10 = _lookback_from_arrays(times, closes, entry_ns, 10)
        lb30 = _lookback_from_arrays(times, closes, entry_ns, 30)
        if lb5 is None and lb10 is None and lb30 is None:
            continue
        duration = float(trade.get("duration_minutes", math.nan)) if pd.notna(trade.get("duration_minutes")) else math.nan
        row = trade.to_dict()
        row.update(
            {
                "entry_candle_timestamp": entry_candle_ts,
                "entry_candle_close": entry_candle_close,
                "entry_candle_age_seconds": entry_age_seconds,
                "lb_5m_ret_close": lb5,
                "lb_10m_ret_close": lb10,
                "lb_30m_ret_close": lb30,
                "duration_bucket": _duration_bucket(duration),
                "regime_bucket": _regime(lb10, lb30),
                "h1_triggered": bool((math.isfinite(duration) and duration <= 30) or "stop" in str(trade.get("exit_reason", ""))),
                "h2_triggered": bool(symbol == "ETHUSDT" and str(trade.get("side", "")).lower() == "long"),
                "h6_triggered": bool(lb10 is not None and lb30 is not None and lb10 <= H6_10M and lb30 <= H6_30M),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _num(value: Any) -> Any:
    if value is None:
        return None
    try:
        number = float(value)
    except Exception:
        return value
    if math.isnan(number):
        return None
    if math.isinf(number):
        return "inf"
    return round(number, 10)


def _pf(pnl: Any) -> Any:
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(pnl[pnl < 0].sum())
    if gross_loss == 0:
        return None if gross_profit == 0 else "inf"
    return _num(gross_profit / abs(gross_loss))


def _empty_metrics() -> dict[str, Any]:
    return {
        "trade_count": 0,
        "triggered_count": 0,
        "winner_count": 0,
        "loser_count": 0,
        "true_positive_count": 0,
        "false_positive_count": 0,
        "false_negative_count": 0,
        "net_pnl": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "profit_factor": None,
        "win_rate": None,
        "coverage_ratio": None,
        "precision": None,
        "recall": None,
        "winner_retention_rate": None,
        "winner_pnl_removed": 0.0,
        "loser_pnl_removed": 0.0,
        "simulated_removed_pnl_delta": 0.0,
    }


def _hypothesis_metrics(aligned: Any, trigger_col: str) -> dict[str, Any]:
    if pd is None or aligned.empty or trigger_col not in aligned.columns:
        return _empty_metrics()
    pnl = pd.to_numeric(aligned["pnl_usdt"], errors="coerce").fillna(0.0)
    triggered = aligned[trigger_col].fillna(False).astype(bool)
    winners = pnl > 0
    losers = pnl < 0
    true_positive = int((triggered & losers).sum())
    false_positive = int((triggered & winners).sum())
    false_negative = int((~triggered & losers).sum())
    winner_count = int(winners.sum())
    return {
        "trade_count": int(len(aligned)),
        "triggered_count": int(triggered.sum()),
        "winner_count": winner_count,
        "loser_count": int(losers.sum()),
        "true_positive_count": true_positive,
        "false_positive_count": false_positive,
        "false_negative_count": false_negative,
        "net_pnl": round(float(pnl.sum()), 10),
        "gross_profit": round(float(pnl[pnl > 0].sum()), 10),
        "gross_loss": round(float(pnl[pnl < 0].sum()), 10),
        "profit_factor": _pf(pnl),
        "win_rate": _num(float(winners.mean()) if len(winners) else None),
        "coverage_ratio": _num(float(triggered.mean()) if len(triggered) else None),
        "precision": _num(true_positive / int(triggered.sum()) if int(triggered.sum()) else None),
        "recall": _num(true_positive / int(losers.sum()) if int(losers.sum()) else None),
        "winner_retention_rate": _num((winner_count - false_positive) / winner_count if winner_count else None),
        "winner_pnl_removed": round(float(pnl[triggered & winners].sum()), 10),
        "loser_pnl_removed": round(float(pnl[triggered & losers].sum()), 10),
        "simulated_removed_pnl_delta": round(-float(pnl[triggered].sum()), 10),
    }


def _slice_metrics(aligned: Any) -> list[dict[str, Any]]:
    if pd is None or aligned.empty:
        return []
    rows: list[dict[str, Any]] = []
    for dimension in SLICE_DIMENSIONS:
        if dimension not in aligned.columns:
            continue
        for value, frame in aligned.groupby(dimension, dropna=False):
            pnl = pd.to_numeric(frame["pnl_usdt"], errors="coerce").fillna(0.0)
            rows.append(
                {
                    "dimension": dimension,
                    "value": str(value),
                    "trade_count": int(len(frame)),
                    "net_pnl": round(float(pnl.sum()), 10),
                    "gross_profit": round(float(pnl[pnl > 0].sum()), 10),
                    "gross_loss": round(float(pnl[pnl < 0].sum()), 10),
                    "profit_factor": _pf(pnl),
                    "win_rate": _num(float((pnl > 0).mean()) if len(pnl) else None),
                    "mean_pnl": _num(float(pnl.mean()) if len(pnl) else None),
                }
            )
    return sorted(rows, key=lambda row: (row["dimension"], -abs(float(row.get("net_pnl") or 0.0)), row["value"]))


def _gate_matrix(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"gate_id": "research_only_contract", "gate_name": "Research-only contract preserved", "severity": "critical", "passed": bool(report.get("research_only") and not report.get("operational_authority")), "evidence": f"research_only={report.get('research_only')}; operational_authority={report.get('operational_authority')}"},
        {"gate_id": "runtime_read_explicit", "gate_name": "Runtime/data reads are explicit", "severity": "critical", "passed": bool(report.get("allow_runtime_read") or report.get("input_mode") == "no_runtime_rows_loaded"), "evidence": f"allow_runtime_read={report.get('allow_runtime_read')}; input_mode={report.get('input_mode')}"},
        {"gate_id": "legacy_dataset_status_explicit", "gate_name": "Legacy dataset loading status is explicit", "severity": "high", "passed": "legacy_trade_dataset_loaded" in report, "evidence": f"legacy_trade_dataset_loaded={report.get('legacy_trade_dataset_loaded')}; rows={report.get('legacy_trade_dataset_rows')}"},
        {"gate_id": "candle_source_status_explicit", "gate_name": "Candle source status is explicit", "severity": "high", "passed": "candle_sources_loaded" in report, "evidence": f"candle_sources_loaded={report.get('candle_sources_loaded')}; candle_rows={report.get('candle_rows')}"},
        {"gate_id": "oos_required_not_bypassed", "gate_name": "OOS validation remains mandatory", "severity": "critical", "passed": bool(report.get("oos_validation_required") and not report.get("oos_validated")), "evidence": f"oos_validation_required={report.get('oos_validation_required')}; oos_validated={report.get('oos_validated')}"},
        {"gate_id": "promotion_blocked", "gate_name": "Rule and model promotion blocked", "severity": "critical", "passed": not bool(report.get("can_promote_rules") or report.get("can_promote_model")), "evidence": f"can_promote_rules={report.get('can_promote_rules')}; can_promote_model={report.get('can_promote_model')}"},
        {"gate_id": "runtime_unchanged", "gate_name": "Runtime and execution surfaces unchanged", "severity": "critical", "passed": not bool(report.get("updates_freqtrade") or report.get("updates_risk_manager") or report.get("sends_orders")), "evidence": "updates_freqtrade=false; updates_risk_manager=false; sends_orders=false"},
    ]


def _gate_summary(gates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failed = [gate for gate in gates if not gate.get("passed")]
    return {
        "gate_count": len(gates),
        "passed_gate_count": len(gates) - len(failed),
        "failed_gate_count": len(failed),
        "failed_gate_ids": [gate["gate_id"] for gate in failed],
        "critical_failed_gate_ids": [gate["gate_id"] for gate in failed if gate.get("severity") == "critical"],
    }


def _base(root: Path, allow_runtime_read: bool, write_requested: bool) -> dict[str, Any]:
    report = {
        "project_name": PROJECT_NAME,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _now(),
        "project_root": str(root),
        "status": "blocked",
        "decision": DECISION,
        "reason": "ocr_master_candle_alignment_requires_explicit_runtime_read_and_sources",
        "research_only": True,
        "read_only": True,
        "paper_only": True,
        "shadow_only": True,
        "allow_runtime_read": bool(allow_runtime_read),
        "write_requested": bool(write_requested),
        "write_performed": False,
        "writes_reports": False,
        "input_mode": "no_runtime_rows_loaded",
        "expected_trade_value_contract": EXPECTED_TRADE_VALUE_CONTRACT,
        "hypothesis_scope": HYPOTHESES,
        "oos_slice_dimensions": SLICE_DIMENSIONS,
        "candidate_shadow_rule": CANDIDATE_RULE,
        "forbidden_actions": FORBIDDEN_ACTIONS,
        "legacy_trade_dataset_loaded": False,
        "legacy_trade_dataset_path": None,
        "legacy_trade_dataset_rows": 0,
        "normalized_trade_rows": 0,
        "legacy_trade_dataset_sha256": None,
        "legacy_trade_dataset_schema_status": None,
        "candle_sources_loaded": False,
        "candle_source_count": 0,
        "candle_rows": 0,
        "candle_sources": [],
        "master_candle_alignment_computed": False,
        "feature_rows": 0,
        "aligned_rows": 0,
        "alignment_coverage_ratio": None,
        "oos_validated": False,
        "oos_validation_required": True,
        "slice_count": 0,
        "slice_metrics": [],
        "global_metrics": {hypothesis: _empty_metrics() for hypothesis in HYPOTHESES},
        "critical_warnings": [],
        "output_path": None,
        "minimum_next_research_gates": [
            "validar cobertura candles por symbol/side/dia",
            "validar estabilidade por período e regime",
            "bloquear qualquer regra que remova ROI winners materialmente",
            "medir custos, spread, slippage, drawdown e drift antes de observação paper",
            "exigir registry shadow bloqueado antes de qualquer uso operacional",
        ],
    }
    report.update(SAFETY_FALSE)
    report["gate_matrix"] = _gate_matrix(report)
    report["gate_summary"] = _gate_summary(
        cast(Sequence[Mapping[str, Any]], report["gate_matrix"])
    )
    return report


def build_ocr_master_candle_aligned_oos_research_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    legacy_trade_dataset: str | Path | None = None,
    candle_roots: Sequence[str | Path] | None = None,
    output_path: str | Path | None = None,
    write: bool = False,
) -> dict[str, Any]:
    root = Path(project_root)
    report = _base(root, allow_runtime_read, write)
    if not allow_runtime_read:
        return report

    report["input_mode"] = "runtime_read_only"
    warnings: list[str] = []
    trades: Any = pd.DataFrame() if pd is not None else []

    if legacy_trade_dataset is None:
        warnings.append("legacy_trade_dataset_not_supplied")
    else:
        master_path = Path(legacy_trade_dataset)
        if not master_path.is_absolute():
            master_path = root / master_path
        report["legacy_trade_dataset_path"] = _rel(master_path, root)
        if not master_path.exists():
            warnings.append("legacy_trade_dataset_missing")
        else:
            try:
                raw_master = _read_table(master_path)
                trades = _normalize_trades(raw_master)
                report.update({
                    "legacy_trade_dataset_loaded": True,
                    "legacy_trade_dataset_rows": int(len(raw_master)),
                    "normalized_trade_rows": int(len(trades)),
                    "legacy_trade_dataset_sha256": _sha256(master_path),
                    "legacy_trade_dataset_schema_status": _schema_status(list(raw_master.columns), "legacy_trade_dataset"),
                })
                if int(len(trades)) == 0:
                    warnings.append("normalized_trades_empty")
            except Exception as exc:
                warnings.append(f"legacy_trade_dataset_load_error:{exc.__class__.__name__}")

    candles, audits, candle_warnings = _load_candles(root, list(candle_roots or []))
    warnings.extend(candle_warnings)
    report.update({
        "candle_sources": audits,
        "candle_source_count": len(audits),
        "candle_rows": int(len(candles)) if pd is not None else 0,
        "candle_sources_loaded": bool(pd is not None and len(candles) > 0),
    })

    if pd is not None and not trades.empty and not candles.empty:
        aligned = _align(trades, candles)
        slice_metrics = _slice_metrics(aligned)
        report.update({
            "master_candle_alignment_computed": not aligned.empty,
            "feature_rows": int(len(aligned)),
            "aligned_rows": int(len(aligned)),
            "alignment_coverage_ratio": _num(len(aligned) / len(trades) if len(trades) else None),
            "slice_metrics": slice_metrics,
            "slice_count": len(slice_metrics),
            "global_metrics": {
                "H1": _hypothesis_metrics(aligned, "h1_triggered"),
                "H2": _hypothesis_metrics(aligned, "h2_triggered"),
                "H6": _hypothesis_metrics(aligned, "h6_triggered"),
            },
        })
        if aligned.empty:
            warnings.append("alignment_empty")
    else:
        if pd is None:
            warnings.append("pandas_unavailable")
        if getattr(trades, "empty", True):
            warnings.append("normalized_trades_empty")
        if getattr(candles, "empty", True):
            warnings.append("candles_empty")

    report["critical_warnings"] = sorted(set(warnings))
    report["reason"] = "ocr_master_candle_alignment_computed_research_only" if report["master_candle_alignment_computed"] else "ocr_master_candle_alignment_blocked_missing_compatible_rows"

    if write and output_path:
        output = Path(output_path)
        if not output.is_absolute():
            output = root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        report["write_performed"] = True
        report["writes_reports"] = True
        report["output_path"] = _rel(output, root)

    report["gate_matrix"] = _gate_matrix(report)
    report["gate_summary"] = _gate_summary(report["gate_matrix"])
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build OCR Master + candle aligned OOS research report.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--allow-runtime-read", action="store_true")
    parser.add_argument("--legacy-trade-dataset")
    parser.add_argument("--candle-root", action="append", default=[])
    parser.add_argument("--output-path")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = build_ocr_master_candle_aligned_oos_research_report(
        project_root=args.project_root,
        allow_runtime_read=args.allow_runtime_read,
        legacy_trade_dataset=args.legacy_trade_dataset,
        candle_roots=args.candle_root,
        output_path=args.output_path,
        write=bool(args.write and not args.no_write),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=bool(args.json_output), indent=None if args.json_output else 2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
