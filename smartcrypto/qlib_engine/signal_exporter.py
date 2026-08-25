from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.execution.decision_ledger_paper_observability_wiring_v1 import (
    finalize_after_risk_manager,
    prepare_before_risk_manager,
)
from smartcrypto.execution.paper_profitability_policy_v1 import decide_direction
from smartcrypto.execution.signal_risk_gate import (
    DEFAULT_RISK_LIMITS_PATH,
    apply_risk_manager_gate,
)
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
    risk_limits_path: str | Path = DEFAULT_RISK_LIMITS_PATH,
) -> dict[str, Any]:
    source = Path(predictions_path)
    report: dict[str, Any]

    if not source.exists():
        report = {
            "status": "blocked",
            "reason": "predictions_missing",
            "predictions_path": str(source),
        }
        write_json(report_path, report)
        return report

    predictions = pd.read_parquet(source)
    if predictions.empty:
        report = {
            "status": "blocked",
            "reason": "predictions_empty",
        }
        write_json(report_path, report)
        return report

    generated_at = datetime.now(timezone.utc)
    valid_until = generated_at + timedelta(minutes=config.signal_ttl_minutes)
    candidate_signals: list[dict[str, Any]] = []

    for row in predictions.to_dict("records"):
        direction = decide_direction(
            row.get("prob_up"),
            long_probability=config.prediction_threshold,
            short_probability=1.0 - config.prediction_threshold,
        )
        if direction.status != "ok" or direction.proposed_side == "no_trade":
            continue
        side = direction.proposed_side

        pair = _freqtrade_pair(str(row.get("pair", "")), str(row.get("symbol", "")))

        candidate_signals.append(
            {
                "pair": pair,
                "symbol": str(row["symbol"]),
                "side": side,
                "proposed_side": side,
                "score": direction.score,
                "prob_up": direction.prob_up,
                "calibrated_probability": direction.prob_up,
                "confidence": direction.confidence,
                "market_regime": str(row.get("market_regime") or "unknown"),
                "market_regime_status": str(
                    row.get("market_regime_status") or "unknown"
                ),
                "regime_block": False,
                "cooldown_block": False,
                "timeframe": str(row.get("tf", config.timeframe)),
                "generated_at": generated_at.isoformat(),
                "valid_until": valid_until.isoformat(),
                "max_position_usdt": float(config.max_position_usdt),
                "leverage": float(config.leverage),
                "model_version": str(row.get("model_version", config.model_version)),
                "source": "phase8_qlib_prediction_engine",
                "reason": direction.reason,
            }
        )

    observability_preparation = prepare_before_risk_manager(
        candidate_signals,
        producer_id="phase8-qlib-signal-exporter",
    )

    # candidate_signals acima não carrega nenhuma alegação de risk_approved.
    # A única autoridade autorizada a definir esse campo é o RiskManager,
    # via apply_risk_manager_gate().
    risk_gate = apply_risk_manager_gate(
        observability_preparation.signals,
        risk_limits_path=risk_limits_path,
    )
    observability = finalize_after_risk_manager(
        observability_preparation,
        risk_gate=risk_gate,
    )

    if risk_gate.status != "ok":
        report = {
            "status": "blocked",
            "reason": risk_gate.reason,
            "signals_candidate": len(candidate_signals),
            "risk_manager_gate": risk_gate.to_dict(),
            "decision_ledger_observability": observability.report.model_dump(mode="json"),
        }
        write_json(report_path, report)
        return report

    if observability.report.publication_blocked:
        report = {
            "status": "blocked",
            "reason": observability.report.reason,
            "signals_candidate": len(candidate_signals),
            "risk_manager_gate": risk_gate.to_dict(),
            "decision_ledger_observability": observability.report.model_dump(mode="json"),
        }
        write_json(report_path, report)
        return report

    signals = observability.active_signals
    payload: dict[str, Any] = {
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
        "risk_manager_gate": risk_gate.to_dict(),
        "decision_ledger_observability": observability.report.model_dump(mode="json"),
    }
    write_json(report_path, report)
    return report
