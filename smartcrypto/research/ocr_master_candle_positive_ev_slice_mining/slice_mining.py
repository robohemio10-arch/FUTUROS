"""Research-only positive-EV slice mining for OCR master trades aligned to candles.

This module is intentionally read-only by default.  It discovers and evaluates
candidate slices from the OCR/Bitradex master plus canonical Binance futures
candles, but it never promotes rules, writes runtime state, or changes trading
surfaces.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

SCHEMA_VERSION = "ocr_master_candle_positive_ev_slice_mining_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
CANDIDATE_RULE_HISTORICAL = (
    "lb_10m_ret_close <= -0.0038501215827868 AND "
    "lb_30m_ret_close <= -0.0060685748963285"
)
EXPECTED_TRADE_VALUE_CONTRACT = (
    "expected_trade_value = Qlib_expected_return_net × Shadow_probability_quality × "
    "Regime_confidence - Estimated_fee - Estimated_spread - Estimated_slippage - "
    "Latency_penalty - Drawdown_penalty - Drift_penalty"
)
CANONICAL_CANDLE_FILENAMES = {
    "BTCUSDT_1m_20251230_20261208.csv",
    "ETHUSDT_1m_20251230_20261208.csv",
}
DEFAULT_MIN_TRADE_COUNT = 30
DEFAULT_MAX_DAY_CONCENTRATION = 0.35
DEFAULT_ALIGNMENT_TOLERANCE_SECONDS = 300

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "release_authority": False,
    "readiness_release_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_risk": False,
    "changes_model": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "runs_training": False,
    "registers_candidate_rules": False,
    "can_promote_rules": False,
    "can_promote_model": False,
    "ready_for_candidate_registry": False,
    "remediation_application_allowed": False,
    "applies_shadow_rules": False,
    "applies_feedback_to_ai_shadow": False,
    "executes_orchestrator": False,
    "executes_scheduler": False,
    "executes_stage_builders": False,
    "writes_data": False,
    "writes_runtime": False,
    "writes_reports": False,
    "writes_parquet": False,
    "writes_sqlite": False,
}

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


@dataclass(frozen=True)
class SourceInfo:
    path: str
    exists: bool
    suffix: str
    source_type: str
    row_count: int
    columns: list[str]
    schema_status: str
    sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "suffix": self.suffix,
            "source_type": self.source_type,
            "row_count": self.row_count,
            "columns": self.columns,
            "schema_status": self.schema_status,
            "sha256": self.sha256,
        }


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_table(path: Path, *, nrows: int | None = None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, nrows=nrows)
    if suffix == ".csv":
        return pd.read_csv(path, nrows=nrows)
    if suffix == ".parquet":
        frame = pd.read_parquet(path)
        return frame.head(nrows) if nrows is not None else frame
    raise ValueError(f"unsupported_source_suffix:{path.suffix}")


def _parse_number(value: object) -> float | None:
    if pd.isna(value):
        return None
    text = str(value)
    text = text.replace("USDT", "").replace("BTC", "").replace("ETH", "")
    text = text.replace("%", "").replace("+", "").strip().replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _normalize_symbol(value: object) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).upper().strip()
    text = text.replace("_", "").replace("/", "").replace(":USDT", "")
    if "BTC" in text:
        return "BTCUSDT"
    if "ETH" in text:
        return "ETHUSDT"
    return None


def _normalize_side(value: object) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).lower()
    if "long" in text:
        return "long"
    if "short" in text:
        return "short"
    return None


def _first_existing_column(columns: Iterable[str], aliases: Sequence[str]) -> str | None:
    available = set(columns)
    for alias in aliases:
        if alias in available:
            return alias
    return None


def _to_datetime_utc(series: pd.Series) -> pd.Series:
    if pd.api.types.is_integer_dtype(series) or pd.api.types.is_float_dtype(series):
        numeric = pd.to_numeric(series, errors="coerce")
        median = numeric.dropna().median() if numeric.notna().any() else None
        if median is not None and median > 10_000_000_000:
            return pd.to_datetime(numeric, unit="ms", errors="coerce", utc=True)
        return pd.to_datetime(numeric, unit="s", errors="coerce", utc=True)
    return pd.to_datetime(series, errors="coerce", utc=True)


def _source_info(path: Path, project_root: Path, source_type: str, schema_status: str, row_count: int, columns: Sequence[str]) -> SourceInfo:
    return SourceInfo(
        path=_project_relative(path, project_root),
        exists=path.exists(),
        suffix=path.suffix.lower(),
        source_type=source_type,
        row_count=int(row_count),
        columns=list(columns),
        schema_status=schema_status,
        sha256=_sha256_file(path),
    )


def normalize_trades_master(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize OCR/Bitradex master or already-normalized trade frames."""

    symbol_col = _first_existing_column(raw.columns, ["symbol_norm", "symbol", "pair", "11_moeda"])
    side_col = _first_existing_column(raw.columns, ["side_norm", "side", "12_fechar_long_short"])
    pnl_col = _first_existing_column(raw.columns, ["pnl_usdt", "net_pnl", "profit_abs", "1_pnl_fechado", "reported_pnl_usdt"])
    entry_col = _first_existing_column(raw.columns, ["entry_price", "open_price", "3_preco_de_abertura"])
    exit_col = _first_existing_column(raw.columns, ["exit_price", "close_price", "4_preco_de_fechamento"])
    open_time_col = _first_existing_column(raw.columns, ["open_time_utc", "open_time", "open_date", "7_horario_de_abertura"])
    close_time_col = _first_existing_column(raw.columns, ["close_time_utc", "close_time", "close_date", "8_horario_de_fechamento"])

    out = pd.DataFrame(index=raw.index)
    out["source_row"] = raw.index.astype(int)
    out["symbol_norm"] = raw[symbol_col].map(_normalize_symbol) if symbol_col else None
    out["side_norm"] = raw[side_col].map(_normalize_side) if side_col else None
    out["pnl_usdt"] = raw[pnl_col].map(_parse_number) if pnl_col else None
    out["entry_price"] = raw[entry_col].map(_parse_number) if entry_col else None
    out["exit_price"] = raw[exit_col].map(_parse_number) if exit_col else None
    out["open_time_utc"] = _to_datetime_utc(raw[open_time_col]) if open_time_col else pd.NaT
    out["close_time_utc"] = _to_datetime_utc(raw[close_time_col]) if close_time_col else pd.NaT

    required = ["symbol_norm", "side_norm", "pnl_usdt", "entry_price", "open_time_utc"]
    normalized = out.dropna(subset=required).copy()
    normalized["duration_minutes"] = (
        (normalized["close_time_utc"] - normalized["open_time_utc"]).dt.total_seconds() / 60.0
    )
    normalized["duration_minutes"] = normalized["duration_minutes"].where(normalized["duration_minutes"].notna(), 0.0)
    normalized["is_winner"] = normalized["pnl_usdt"] > 0
    normalized["is_loser"] = normalized["pnl_usdt"] <= 0
    normalized = normalized.sort_values(["symbol_norm", "open_time_utc", "source_row"]).reset_index(drop=True)
    return normalized


def normalize_candles(raw: pd.DataFrame) -> pd.DataFrame:
    timestamp_col = _first_existing_column(raw.columns, ["timestamp", "ts", "open_time", "ts_ms", "timestamp_ms"])
    symbol_col = _first_existing_column(raw.columns, ["symbol", "pair"])
    required_price_cols = ["open", "high", "low", "close", "volume"]
    missing_prices = [column for column in required_price_cols if column not in raw.columns]
    if timestamp_col is None or symbol_col is None or missing_prices:
        return pd.DataFrame()

    out = pd.DataFrame()
    out["symbol_norm"] = raw[symbol_col].map(_normalize_symbol)
    out["candle_ts"] = _to_datetime_utc(raw[timestamp_col])
    for column in required_price_cols:
        out[f"candle_{column}"] = pd.to_numeric(raw[column], errors="coerce")
    out = out.dropna(subset=["symbol_norm", "candle_ts", "candle_open", "candle_close"]).copy()
    out = out.sort_values(["symbol_norm", "candle_ts"]).drop_duplicates(["symbol_norm", "candle_ts"], keep="last")

    grouped = out.groupby("symbol_norm", group_keys=False)
    out["lb_5m_ret_close"] = grouped["candle_close"].pct_change(5)
    out["lb_10m_ret_close"] = grouped["candle_close"].pct_change(10)
    out["lb_30m_ret_close"] = grouped["candle_close"].pct_change(30)
    out["lb_10m_range_pct"] = (out["candle_high"] - out["candle_low"]) / out["candle_close"].replace(0.0, pd.NA)
    out["lb_10m_body_pct"] = (out["candle_close"] - out["candle_open"]) / out["candle_open"].replace(0.0, pd.NA)
    out["candle_hour"] = out["candle_ts"].dt.hour.astype("int64")
    out["candle_day"] = out["candle_ts"].dt.strftime("%Y-%m-%d")
    return out.reset_index(drop=True)


def discover_canonical_candle_paths(project_root: Path, candle_roots: Sequence[Path]) -> list[Path]:
    candidates: list[Path] = []
    for root in candle_roots:
        base = root if root.is_absolute() else project_root / root
        if not base.exists():
            continue
        for filename in CANONICAL_CANDLE_FILENAMES:
            direct = base / "raw" / "binance_futures_klines" / filename
            if direct.exists():
                candidates.append(direct)
        if base.name == "binance_futures_klines":
            for filename in CANONICAL_CANDLE_FILENAMES:
                direct = base / filename
                if direct.exists():
                    candidates.append(direct)
    unique: dict[str, Path] = {}
    for path in candidates:
        unique[str(path.resolve())] = path
    return sorted(unique.values(), key=lambda item: item.name)


def load_trades_master(path: Path, project_root: Path) -> tuple[pd.DataFrame, pd.DataFrame, SourceInfo]:
    raw = _read_table(path)
    normalized = normalize_trades_master(raw)
    schema_status = "candidate_trade_schema" if not normalized.empty else "invalid_trade_schema"
    return raw, normalized, _source_info(path, project_root, "trades_master", schema_status, len(raw), raw.columns)


def load_candles(project_root: Path, candle_roots: Sequence[Path]) -> tuple[pd.DataFrame, list[SourceInfo]]:
    paths = discover_canonical_candle_paths(project_root, candle_roots)
    frames: list[pd.DataFrame] = []
    sources: list[SourceInfo] = []
    for path in paths:
        raw = _read_table(path)
        normalized = normalize_candles(raw)
        schema_status = "candidate_candle_schema" if not normalized.empty else "invalid_candle_schema"
        sources.append(_source_info(path, project_root, "candles", schema_status, len(raw), raw.columns))
        if not normalized.empty:
            frames.append(normalized)
    if not frames:
        return pd.DataFrame(), sources
    candles = pd.concat(frames, ignore_index=True)
    candles = candles.sort_values(["symbol_norm", "candle_ts"]).drop_duplicates(["symbol_norm", "candle_ts"], keep="last")
    return candles.reset_index(drop=True), sources


def align_trades_to_candles(trades: pd.DataFrame, candles: pd.DataFrame, *, tolerance_seconds: int) -> pd.DataFrame:
    if trades.empty or candles.empty:
        return pd.DataFrame()
    aligned_parts: list[pd.DataFrame] = []
    tolerance = pd.Timedelta(seconds=tolerance_seconds)
    for symbol, trade_group in trades.groupby("symbol_norm"):
        candle_group = candles[candles["symbol_norm"] == symbol]
        if candle_group.empty:
            continue
        left = trade_group.sort_values("open_time_utc").copy()
        right = candle_group.sort_values("candle_ts").copy()
        merged = pd.merge_asof(
            left,
            right,
            left_on="open_time_utc",
            right_on="candle_ts",
            direction="backward",
            tolerance=tolerance,
            suffixes=("", "_candle"),
        )
        aligned_parts.append(merged.dropna(subset=["candle_ts", "candle_close"]))
    if not aligned_parts:
        return pd.DataFrame()
    aligned = pd.concat(aligned_parts, ignore_index=True)
    aligned["alignment_lag_seconds"] = (aligned["open_time_utc"] - aligned["candle_ts"]).dt.total_seconds()
    aligned["entry_vs_candle_close_ret"] = (
        aligned["entry_price"] - aligned["candle_close"]
    ) / aligned["candle_close"].replace(0.0, pd.NA)
    aligned["duration_bucket"] = aligned["duration_minutes"].map(_duration_bucket)
    aligned["hour"] = aligned["open_time_utc"].dt.hour.astype("int64").astype(str)
    aligned["day"] = aligned["open_time_utc"].dt.strftime("%Y-%m-%d")
    aligned["regime_bucket"] = aligned.apply(_regime_bucket, axis=1)
    return aligned.reset_index(drop=True)


def _duration_bucket(value: float | int | None) -> str:
    minutes = float(value or 0.0)
    if minutes <= 30:
        return "le_30m"
    if minutes <= 120:
        return "le_2h"
    if minutes <= 360:
        return "le_6h"
    return "gt_6h"


def _regime_bucket(row: pd.Series) -> str:
    ret_10 = row.get("lb_10m_ret_close")
    ret_30 = row.get("lb_30m_ret_close")
    side = row.get("side_norm")
    if pd.isna(ret_10) or pd.isna(ret_30):
        return "unknown"
    if abs(float(ret_10)) < 0.0005 and abs(float(ret_30)) < 0.001:
        return "flat"
    if float(ret_10) > 0 and float(ret_30) > 0:
        return "bullish_long" if side == "long" else "bullish_short"
    if float(ret_10) < 0 and float(ret_30) < 0:
        return "bearish_short" if side == "short" else "bearish_long"
    if float(ret_10) > 0 > float(ret_30):
        return "reversal_up"
    if float(ret_10) < 0 < float(ret_30):
        return "pullback_down"
    return "mixed"


def _safe_float(value: float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return round(float(value), 10)


def compute_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trade_count": 0,
            "winner_count": 0,
            "loser_count": 0,
            "net_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": None,
            "win_rate": None,
            "mean_pnl": None,
        }
    pnl = pd.to_numeric(frame["pnl_usdt"], errors="coerce").fillna(0.0)
    winners = pnl[pnl > 0]
    losers = pnl[pnl <= 0]
    gross_profit = float(winners.sum())
    gross_loss = float(losers.sum())
    if gross_loss < 0:
        profit_factor: float | str | None = gross_profit / abs(gross_loss)
    elif gross_profit > 0:
        profit_factor = "inf"
    else:
        profit_factor = None
    return {
        "trade_count": int(len(frame)),
        "winner_count": int((pnl > 0).sum()),
        "loser_count": int((pnl <= 0).sum()),
        "net_pnl": round(float(pnl.sum()), 10),
        "gross_profit": round(gross_profit, 10),
        "gross_loss": round(gross_loss, 10),
        "profit_factor": round(float(profit_factor), 10) if isinstance(profit_factor, float) else profit_factor,
        "win_rate": round(float((pnl > 0).mean()), 10),
        "mean_pnl": round(float(pnl.mean()), 10),
    }


def _numeric_pf(metrics: Mapping[str, Any]) -> float:
    value = metrics.get("profit_factor")
    if value == "inf":
        return float("inf")
    if value is None:
        return 0.0
    return float(value)


def _max_day_concentration(frame: pd.DataFrame) -> float | None:
    if frame.empty or "day" not in frame.columns:
        return None
    counts = frame["day"].value_counts(dropna=False)
    if counts.empty:
        return None
    return round(float(counts.max() / len(frame)), 10)


def _candidate_record(
    *,
    candidate_id: str,
    rule_type: str,
    dimensions: Sequence[str],
    values: Sequence[str],
    subset: pd.DataFrame,
    baseline: Mapping[str, Any],
    total_winners: int,
    min_trade_count: int,
    max_day_concentration: float,
) -> dict[str, Any]:
    metrics = compute_metrics(subset)
    baseline_pf = _numeric_pf(baseline)
    candidate_pf = _numeric_pf(metrics)
    baseline_mean = float(baseline.get("mean_pnl") or 0.0)
    candidate_mean = float(metrics.get("mean_pnl") or 0.0)
    day_concentration = _max_day_concentration(subset)
    winner_retention_rate = (
        float(metrics["winner_count"] / total_winners) if total_winners else None
    )
    pf_lift = None if not math.isfinite(baseline_pf) else candidate_pf - baseline_pf
    mean_pnl_lift = candidate_mean - baseline_mean
    trade_count = int(metrics["trade_count"])
    concentration_ok = day_concentration is None or day_concentration <= max_day_concentration
    sample_ok = trade_count >= min_trade_count
    positive_ev = (
        sample_ok
        and concentration_ok
        and metrics["net_pnl"] > 0
        and candidate_pf > baseline_pf
        and candidate_mean > baseline_mean
        and (metrics["win_rate"] or 0.0) >= (baseline.get("win_rate") or 0.0)
    )
    rejection_reasons: list[str] = []
    if not sample_ok:
        rejection_reasons.append("insufficient_trade_count")
    if not concentration_ok:
        rejection_reasons.append("day_concentration_too_high")
    if metrics["net_pnl"] <= 0:
        rejection_reasons.append("non_positive_net_pnl")
    if candidate_pf <= baseline_pf:
        rejection_reasons.append("profit_factor_not_above_baseline")
    if candidate_mean <= baseline_mean:
        rejection_reasons.append("mean_pnl_not_above_baseline")
    if (metrics["win_rate"] or 0.0) < (baseline.get("win_rate") or 0.0):
        rejection_reasons.append("win_rate_below_baseline")

    return {
        "candidate_id": candidate_id,
        "rule_type": rule_type,
        "dimensions": list(dimensions),
        "values": list(values),
        "expression": " AND ".join(f"{dimension} == {value!r}" for dimension, value in zip(dimensions, values, strict=True)),
        "metrics": metrics,
        "baseline_profit_factor": baseline.get("profit_factor"),
        "baseline_mean_pnl": baseline.get("mean_pnl"),
        "profit_factor_lift": _safe_float(pf_lift),
        "mean_pnl_lift": _safe_float(mean_pnl_lift),
        "winner_retention_rate": _safe_float(winner_retention_rate),
        "max_day_concentration": _safe_float(day_concentration),
        "positive_ev_candidate": bool(positive_ev),
        "eligible_for_oos_validation": bool(positive_ev),
        "ready_for_candidate_registry": False,
        "operational_authority": False,
        "can_promote_rules": False,
        "rejection_reasons": rejection_reasons,
        "score": _safe_float((pf_lift or 0.0) * math.log1p(max(trade_count, 0)) + mean_pnl_lift),
    }


def mine_positive_ev_slices(
    aligned: pd.DataFrame,
    *,
    min_trade_count: int = DEFAULT_MIN_TRADE_COUNT,
    max_day_concentration: float = DEFAULT_MAX_DAY_CONCENTRATION,
) -> dict[str, Any]:
    baseline = compute_metrics(aligned)
    if aligned.empty:
        return {
            "baseline_metrics": baseline,
            "candidate_count": 0,
            "positive_candidate_count": 0,
            "eligible_positive_candidate_count": 0,
            "top_positive_candidates": [],
            "top_negative_slices": [],
            "rejected_candidate_count": 0,
            "min_trade_count": min_trade_count,
            "max_day_concentration": max_day_concentration,
        }

    total_winners = int((aligned["pnl_usdt"] > 0).sum())
    dimension_sets: list[tuple[str, ...]] = [
        ("symbol_norm",),
        ("side_norm",),
        ("hour",),
        ("duration_bucket",),
        ("regime_bucket",),
        ("symbol_norm", "side_norm"),
        ("symbol_norm", "hour"),
        ("side_norm", "hour"),
        ("symbol_norm", "regime_bucket"),
        ("side_norm", "regime_bucket"),
    ]

    candidates: list[dict[str, Any]] = []
    for dimensions in dimension_sets:
        if any(dimension not in aligned.columns for dimension in dimensions):
            continue
        grouped = aligned.groupby(list(dimensions), dropna=False)
        for group_values, subset in grouped:
            values_tuple = group_values if isinstance(group_values, tuple) else (group_values,)
            values = tuple(str(value) for value in values_tuple)
            candidate_id = "include__" + "__".join(f"{dimension}_{value}" for dimension, value in zip(dimensions, values, strict=True))
            candidates.append(
                _candidate_record(
                    candidate_id=candidate_id,
                    rule_type="include_slice_research_only",
                    dimensions=dimensions,
                    values=values,
                    subset=subset,
                    baseline=baseline,
                    total_winners=total_winners,
                    min_trade_count=min_trade_count,
                    max_day_concentration=max_day_concentration,
                )
            )

    positive = [candidate for candidate in candidates if candidate["positive_ev_candidate"]]
    positive.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            float(item["metrics"].get("net_pnl") or 0.0),
            int(item["metrics"].get("trade_count") or 0),
        ),
        reverse=True,
    )
    negative = sorted(
        [candidate for candidate in candidates if float(candidate["metrics"].get("net_pnl") or 0.0) < 0],
        key=lambda item: float(item["metrics"].get("net_pnl") or 0.0),
    )
    return {
        "baseline_metrics": baseline,
        "candidate_count": len(candidates),
        "positive_candidate_count": len(positive),
        "eligible_positive_candidate_count": len(positive),
        "top_positive_candidates": positive[:25],
        "top_negative_slices": negative[:15],
        "rejected_candidate_count": len(candidates) - len(positive),
        "min_trade_count": min_trade_count,
        "max_day_concentration": max_day_concentration,
    }


def _gate_matrix(*, allow_runtime_read: bool, trades_loaded: bool, candles_loaded: bool, aligned_rows: int, positive_count: int) -> list[dict[str, Any]]:
    return [
        {
            "gate_id": "research_only_contract",
            "gate_name": "Research-only contract preserved",
            "severity": "critical",
            "passed": True,
            "evidence": "research_only=True; operational_authority=False",
        },
        {
            "gate_id": "runtime_read_explicit",
            "gate_name": "Runtime/data reads are explicit",
            "severity": "critical",
            "passed": True,
            "evidence": f"allow_runtime_read={allow_runtime_read}; input_mode={'runtime_read_only' if allow_runtime_read else 'no_runtime_rows_loaded'}",
        },
        {
            "gate_id": "source_contract_available",
            "gate_name": "Trades and candles source contract is explicit",
            "severity": "high",
            "passed": (not allow_runtime_read) or (trades_loaded and candles_loaded),
            "evidence": f"trades_loaded={trades_loaded}; candles_loaded={candles_loaded}",
        },
        {
            "gate_id": "alignment_required_for_mining",
            "gate_name": "Positive-EV mining requires aligned rows",
            "severity": "high",
            "passed": (not allow_runtime_read) or aligned_rows > 0,
            "evidence": f"aligned_rows={aligned_rows}",
        },
        {
            "gate_id": "candidate_registry_blocked",
            "gate_name": "Positive candidates do not enter registry",
            "severity": "critical",
            "passed": True,
            "evidence": f"positive_candidate_count={positive_count}; ready_for_candidate_registry=False",
        },
        {
            "gate_id": "promotion_blocked",
            "gate_name": "Rule and model promotion blocked",
            "severity": "critical",
            "passed": True,
            "evidence": "can_promote_rules=False; can_promote_model=False",
        },
        {
            "gate_id": "runtime_unchanged",
            "gate_name": "Runtime and execution surfaces unchanged",
            "severity": "critical",
            "passed": True,
            "evidence": "updates_freqtrade=false; updates_risk_manager=false; sends_orders=false",
        },
    ]


def _summarize_gates(gates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failed = [str(gate["gate_id"]) for gate in gates if not gate.get("passed")]
    critical_failed = [str(gate["gate_id"]) for gate in gates if not gate.get("passed") and gate.get("severity") == "critical"]
    return {
        "gate_count": len(gates),
        "passed_gate_count": len(gates) - len(failed),
        "failed_gate_count": len(failed),
        "failed_gate_ids": failed,
        "critical_failed_gate_ids": critical_failed,
    }


def build_positive_ev_slice_mining_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    trades_master: str | Path | None = None,
    candle_roots: Sequence[str | Path] | None = None,
    min_trade_count: int = DEFAULT_MIN_TRADE_COUNT,
    max_day_concentration: float = DEFAULT_MAX_DAY_CONCENTRATION,
    alignment_tolerance_seconds: int = DEFAULT_ALIGNMENT_TOLERANCE_SECONDS,
    write: bool = False,
    no_write: bool = True,
) -> dict[str, Any]:
    root = Path(project_root)
    write_requested = bool(write and not no_write)

    raw_trade_rows = 0
    normalized_trades = pd.DataFrame()
    trades_source: SourceInfo | None = None
    candles = pd.DataFrame()
    candle_sources: list[SourceInfo] = []
    aligned = pd.DataFrame()
    mining = mine_positive_ev_slices(pd.DataFrame(), min_trade_count=min_trade_count, max_day_concentration=max_day_concentration)
    critical_warnings: list[str] = []

    if allow_runtime_read:
        master_path = Path(trades_master) if trades_master else root / "data" / "trades" / "trades_master.xlsx"
        if not master_path.is_absolute():
            master_path = root / master_path
        if master_path.exists():
            raw_trades, normalized_trades, trades_source = load_trades_master(master_path, root)
            raw_trade_rows = len(raw_trades)
        else:
            critical_warnings.append(f"trades_master_missing:{master_path}")

        roots = [Path(item) for item in (candle_roots or [Path("data")])]
        candles, candle_sources = load_candles(root, roots)
        aligned = align_trades_to_candles(
            normalized_trades,
            candles,
            tolerance_seconds=alignment_tolerance_seconds,
        )
        mining = mine_positive_ev_slices(
            aligned,
            min_trade_count=min_trade_count,
            max_day_concentration=max_day_concentration,
        )

    positive_count = int(mining.get("positive_candidate_count", 0))
    gates = _gate_matrix(
        allow_runtime_read=allow_runtime_read,
        trades_loaded=trades_source is not None,
        candles_loaded=not candles.empty,
        aligned_rows=len(aligned),
        positive_count=positive_count,
    )
    gate_summary = _summarize_gates(gates)
    ready_for_oos_validation = bool(positive_count > 0 and len(aligned) > 0)

    reason = "positive_ev_slice_mining_requires_explicit_runtime_read_and_sources"
    if allow_runtime_read and len(aligned) == 0:
        reason = "positive_ev_slice_mining_blocked_missing_aligned_rows"
    elif allow_runtime_read and positive_count == 0:
        reason = "positive_ev_slice_mining_completed_no_eligible_candidates"
    elif allow_runtime_read and positive_count > 0:
        reason = "positive_ev_slice_candidates_found_research_only"

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(project_root),
        "status": "blocked",
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "input_mode": "runtime_read_only" if allow_runtime_read else "no_runtime_rows_loaded",
        "allow_runtime_read": allow_runtime_read,
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "expected_trade_value_contract": EXPECTED_TRADE_VALUE_CONTRACT,
        "historical_rejected_candidate_rule": CANDIDATE_RULE_HISTORICAL,
        "forbidden_actions": FORBIDDEN_ACTIONS,
        "critical_warnings": critical_warnings,
        "trades_master_loaded": trades_source is not None,
        "trades_master_rows": raw_trade_rows,
        "trades_master_normalized_rows": len(normalized_trades),
        "trades_master_source": trades_source.to_dict() if trades_source else None,
        "candle_sources_loaded": bool(candle_sources and not candles.empty),
        "candle_source_count": len(candle_sources),
        "candle_rows": len(candles),
        "candle_sources": [source.to_dict() for source in candle_sources],
        "master_candle_alignment_computed": len(aligned) > 0,
        "aligned_rows": len(aligned),
        "alignment_coverage_ratio": round(float(len(aligned) / len(normalized_trades)), 10) if len(normalized_trades) else None,
        "feature_rows": len(aligned),
        "baseline_metrics": mining["baseline_metrics"],
        "candidate_count": mining["candidate_count"],
        "positive_candidate_count": mining["positive_candidate_count"],
        "eligible_positive_candidate_count": mining["eligible_positive_candidate_count"],
        "rejected_candidate_count": mining["rejected_candidate_count"],
        "top_positive_candidates": mining["top_positive_candidates"],
        "top_negative_slices": mining["top_negative_slices"],
        "min_trade_count": mining["min_trade_count"],
        "max_day_concentration": mining["max_day_concentration"],
        "slice_dimensions_mined": [
            "symbol_norm",
            "side_norm",
            "hour",
            "duration_bucket",
            "regime_bucket",
            "symbol_norm+side_norm",
            "symbol_norm+hour",
            "side_norm+hour",
            "symbol_norm+regime_bucket",
            "side_norm+regime_bucket",
        ],
        "ready_for_oos_validation": ready_for_oos_validation,
        "oos_validation_required": True,
        "oos_validated": False,
        "paper_observation_allowed": False,
        "gate_matrix": gates,
        "gate_summary": gate_summary,
        **SAFETY_FLAGS,
    }

    if write_requested:
        reports_dir = root / "data" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        output_path = reports_dir / "ocr_master_candle_positive_ev_slice_mining_v1.json"
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        report["write_performed"] = True
        report["writes_reports"] = True
        report["output_path"] = _project_relative(output_path, root)

    return report


__all__ = [
    "build_positive_ev_slice_mining_report",
    "compute_metrics",
    "mine_positive_ev_slices",
    "normalize_candles",
    "normalize_trades_master",
]
