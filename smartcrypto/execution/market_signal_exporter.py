from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.ml.market_dataset import load_market_model_config


@dataclass(frozen=True)
class SignalExportReport:
    status: str
    reason: str | None
    signals: int
    output_path: str
    created_at: str


def export_market_signals(
    config_path: str | Path = "config/market_model.yml",
    report_path: str | Path = "data/reports/phase6_signal_export_report.json",
) -> SignalExportReport:
    config = load_market_model_config(config_path)
    model_config = config["market_model"]
    prediction_config = model_config.get("prediction", {})
    predictions_path = Path(model_config["predictions_output_path"])
    output_path = Path(model_config["signal_output_path"])
    long_threshold = float(prediction_config.get("long_probability_threshold", 0.55))
    short_threshold = float(prediction_config.get("short_probability_threshold", 0.45))
    allow_short = bool(prediction_config.get("allow_short", True))
    leverage = float(prediction_config.get("default_leverage", 2.0))
    max_position_usdt = float(prediction_config.get("max_position_usdt", 50.0))
    valid_minutes = int(prediction_config.get("max_signal_age_minutes", 10))

    if not predictions_path.exists():
        report = SignalExportReport(
            status="blocked",
            reason="predictions_file_not_found",
            signals=0,
            output_path=str(output_path),
            created_at=_utc_now(),
        )
        _write_json(report_path, asdict(report))
        _write_json(output_path, _empty_signal_payload(model_config, "predictions_file_not_found"))
        return report

    predictions = pd.read_parquet(predictions_path)
    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(minutes=valid_minutes)

    signals: list[dict[str, Any]] = []
    for row in predictions.to_dict(orient="records"):
        prob_up = float(row.get("prob_up", 0.5))
        side = None
        if prob_up >= long_threshold:
            side = "long"
        elif allow_short and prob_up <= short_threshold:
            side = "short"

        if side is None:
            continue

        pair = str(row.get("pair") or _symbol_to_freqtrade_pair(str(row.get("symbol", ""))))
        signals.append(
            {
                "pair": pair,
                "symbol": str(row.get("symbol", "")),
                "side": side,
                "score": float(row.get("score", 0.0)),
                "prob_up": prob_up,
                "confidence": abs(prob_up - 0.5) * 2.0,
                "timeframe": str(row.get("tf", model_config.get("timeframe", "5m"))),
                "generated_at": now.isoformat(),
                "valid_until": valid_until.isoformat(),
                "risk_approved": True,
                "max_position_usdt": max_position_usdt,
                "leverage": leverage,
                "model_version": str(row.get("model_version", model_config.get("version", "market_direction_rf_v1"))),
                "reason": "market_direction_probability_threshold",
            }
        )

    payload = {
        "generated_at": now.isoformat(),
        "runtime_mode": "paper",
        "model_version": model_config.get("version", "market_direction_rf_v1"),
        "source": "phase6_market_prediction_model",
        "signals": signals,
    }
    _write_json(output_path, payload)

    report = SignalExportReport(
        status="ok",
        reason=None,
        signals=len(signals),
        output_path=str(output_path),
        created_at=now.isoformat(),
    )
    _write_json(report_path, asdict(report))
    return report


def _empty_signal_payload(model_config: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "generated_at": _utc_now(),
        "runtime_mode": "paper",
        "model_version": model_config.get("version", "market_direction_rf_v1"),
        "source": "phase6_market_prediction_model",
        "blocked": True,
        "reason": reason,
        "signals": [],
    }


def _symbol_to_freqtrade_pair(symbol: str) -> str:
    if symbol.endswith("USDT"):
        base = symbol[:-4]
        return f"{base}/USDT:USDT"
    return symbol


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    import json

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
