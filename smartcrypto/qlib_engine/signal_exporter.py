from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.qlib_engine.common import QlibEngineConfig, write_json


def _freqtrade_pair(pair: str, symbol: str) -> str:
    pair = str(pair)
    if ":USDT" in pair:
        return pair
    if "/" in pair:
        return f"{pair}:USDT"
    symbol = str(symbol).upper()
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT:USDT"
    return pair


def export_qlib_freqtrade_signals(
    *,
    predictions_path: str | Path,
    output_path: str | Path,
    report_path: str | Path,
    config: QlibEngineConfig,
) -> dict[str, Any]:
    source = Path(predictions_path)
    if not source.exists():
        report = {"status": "blocked", "reason": "predictions_missing", "predictions_path": str(source)}
        write_json(report_path, report)
        return report

    predictions = pd.read_parquet(source)
    if predictions.empty:
        report = {"status": "blocked", "reason": "predictions_empty"}
        write_json(report_path, report)
        return report

    generated_at = datetime.now(timezone.utc)
    valid_until = generated_at + timedelta(minutes=config.signal_ttl_minutes)
    signals: list[dict[str, Any]] = []

    for row in predictions.to_dict("records"):
        prob_up = float(row["prob_up"])
        score = float(row["score"])
        confidence = abs(score)

        if prob_up >= config.prediction_threshold:
            side = "long"
        elif prob_up <= 1 - config.prediction_threshold:
            side = "short"
        else:
            continue

        pair = _freqtrade_pair(str(row.get("pair", "")), str(row.get("symbol", "")))
        signals.append(
            {
                "pair": pair,
                "symbol": str(row["symbol"]),
                "side": side,
                "score": score,
                "prob_up": prob_up,
                "confidence": confidence,
                "timeframe": str(row.get("tf", config.timeframe)),
                "generated_at": generated_at.isoformat(),
                "valid_until": valid_until.isoformat(),
                "risk_approved": True,
                "max_position_usdt": float(config.max_position_usdt),
                "leverage": float(config.leverage),
                "model_version": str(row.get("model_version", config.model_version)),
                "source": "phase8_qlib_prediction_engine",
                "reason": "qlib_probability_threshold",
            }
        )

    payload = {
        "generated_at": generated_at.isoformat(),
        "runtime_mode": "paper",
        "model_version": config.model_version,
        "source": "phase8_qlib_prediction_engine",
        "signals": signals,
    }
    write_json(output_path, payload)

    report = {
        "status": "ok",
        "reason": None,
        "signals": len(signals),
        "output_path": str(output_path),
        "created_at": generated_at.isoformat(),
    }
    write_json(report_path, report)
    return report
