from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import json
import math

import pandas as pd
import yaml

from smartcrypto.execution.signal_risk_gate import (
    DEFAULT_RISK_LIMITS_PATH,
    apply_risk_manager_gate,
)


@dataclass(frozen=True)
class SignalGuardConfig:
    signals_path: Path
    predictions_path: Path
    long_threshold: float
    short_threshold: float
    min_confidence: float
    valid_for_minutes: int
    max_position_usdt: float
    leverage: float
    runtime_mode: str
    risk_limits_path: Path


def load_config(path: Path = Path("config/ops_loop.yml")) -> SignalGuardConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    paths = raw.get("paths", {})
    contract = raw.get("signal_contract", {})
    risk = raw.get("risk", {})
    risk_manager = raw.get("risk_manager", {})
    return SignalGuardConfig(
        signals_path=Path(paths.get("signals", "data/freqtrade_signals.json")),
        predictions_path=Path(paths.get("qlib_predictions", "data/predictions/latest_qlib_predictions.parquet")),
        long_threshold=float(contract.get("long_threshold", 0.50)),
        short_threshold=float(contract.get("short_threshold", 0.50)),
        min_confidence=float(contract.get("min_confidence", 0.0)),
        valid_for_minutes=int(contract.get("valid_for_minutes", 15)),
        max_position_usdt=float(risk.get("max_position_usdt", 50)),
        leverage=float(risk.get("leverage", 2)),
        runtime_mode=str(raw.get("runtime_mode", "paper")),
        risk_limits_path=Path(risk_manager.get("limits_path", str(DEFAULT_RISK_LIMITS_PATH))),
    )


def read_signal_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def signal_count(payload: dict[str, Any]) -> int:
    signals = payload.get("signals")
    return len(signals) if isinstance(signals, list) else 0


def _to_pair(symbol: str, pair: Any) -> str:
    if isinstance(pair, str) and pair:
        if ":" in pair:
            return pair
        if "/" in pair:
            return f"{pair}:USDT"
    clean = str(symbol).replace("/", "").replace(":USDT", "").upper()
    if clean.endswith("USDT"):
        base = clean[:-4]
        return f"{base}/USDT:USDT"
    return str(pair or symbol)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def build_signals_from_predictions(config: SignalGuardConfig) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(minutes=config.valid_for_minutes)

    if not config.predictions_path.exists():
        return {
            "generated_at": now.isoformat(),
            "runtime_mode": config.runtime_mode,
            "model_version": "unknown",
            "source": "phase11_signal_contract_guard",
            "signals": [],
            "reason": "predictions_not_found",
        }

    frame = pd.read_parquet(config.predictions_path)
    if frame.empty:
        return {
            "generated_at": now.isoformat(),
            "runtime_mode": config.runtime_mode,
            "model_version": "unknown",
            "source": "phase11_signal_contract_guard",
            "signals": [],
            "reason": "predictions_empty",
        }

    if "date" in frame.columns:
        frame = frame.sort_values("date")
    elif "generated_at" in frame.columns:
        frame = frame.sort_values("generated_at")

    latest = frame.groupby("symbol", as_index=False, sort=False).tail(1) if "symbol" in frame.columns else frame.tail(2)

    candidate_signals: list[dict[str, Any]] = []
    for _, row in latest.iterrows():
        symbol = str(row.get("symbol", "")).upper()
        pair = _to_pair(symbol, row.get("pair", ""))
        prob_up = _safe_float(row.get("prob_up"), 0.5)
        score = _safe_float(row.get("score"), prob_up - 0.5)
        confidence = abs(prob_up - 0.5) * 2 if abs(score) <= 1 else abs(score)

        side: str | None = None
        if prob_up >= config.long_threshold:
            side = "long"
        elif prob_up <= (1.0 - config.short_threshold):
            side = "short"

        if side is None or confidence < config.min_confidence:
            continue

        # NOTE: no "risk_approved" claim is made here. RiskManager is the
        # only authority allowed to set that field, via
        # apply_risk_manager_gate() below.
        candidate_signals.append(
            {
                "pair": pair,
                "symbol": symbol,
                "side": side,
                "score": score,
                "prob_up": prob_up,
                "confidence": confidence,
                "timeframe": str(row.get("tf", "5m")),
                "generated_at": now.isoformat(),
                "valid_until": valid_until.isoformat(),
                "max_position_usdt": config.max_position_usdt,
                "leverage": config.leverage,
                "model_version": str(row.get("model_version", "qlib_lgbm_v1")),
                "reason": "phase11_repaired_from_latest_qlib_predictions",
            }
        )

    model_version = "qlib_lgbm_v1"
    if "model_version" in latest.columns and not latest["model_version"].dropna().empty:
        model_version = str(latest["model_version"].dropna().iloc[-1])

    risk_gate = apply_risk_manager_gate(candidate_signals, risk_limits_path=config.risk_limits_path)
    if risk_gate.status != "ok":
        return {
            "generated_at": now.isoformat(),
            "runtime_mode": config.runtime_mode,
            "model_version": model_version,
            "source": "phase11_signal_contract_guard",
            "signals": [],
            "reason": risk_gate.reason,
            "risk_manager_gate": risk_gate.to_dict(),
        }

    return {
        "generated_at": now.isoformat(),
        "runtime_mode": config.runtime_mode,
        "model_version": model_version,
        "source": "phase11_signal_contract_guard",
        "signals": risk_gate.approved_signals,
        "risk_manager_gate": risk_gate.to_dict(),
    }


def write_signal_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def repair_if_needed(force: bool = False) -> dict[str, Any]:
    config = load_config()
    before = read_signal_file(config.signals_path)
    before_count = signal_count(before)

    repaired = False
    reason = "signals_already_available"

    if force or before_count == 0:
        payload = build_signals_from_predictions(config)
        write_signal_file(config.signals_path, payload)
        repaired = True
        reason = "force_repair" if force else "empty_signal_file_repaired_from_predictions"
    else:
        payload = before

    report = {
        "status": "ok",
        "repaired": repaired,
        "reason": reason,
        "signals_before": before_count,
        "signals_after": signal_count(payload),
        "signals_path": str(config.signals_path),
        "predictions_path": str(config.predictions_path),
        "source": payload.get("source"),
        "model_version": payload.get("model_version"),
        "generated_at": payload.get("generated_at"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    report_path = Path("data/reports/phase11_signal_guard_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
