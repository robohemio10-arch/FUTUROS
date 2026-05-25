from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


PAIRS = {
    "BTC/USDT:USDT": "BTCUSDT",
    "ETH/USDT:USDT": "ETHUSDT",
}


def build_signal(pair: str, side: str, ttl_minutes: int, confidence: float, leverage: float, max_position_usdt: float) -> dict:
    now = datetime.now(UTC)
    symbol = PAIRS[pair]
    score = confidence if side == "long" else -confidence
    prob_up = 0.5 + confidence / 2 if side == "long" else 0.5 - confidence / 2

    return {
        "pair": pair,
        "symbol": symbol,
        "side": side,
        "score": score,
        "prob_up": max(0.01, min(0.99, prob_up)),
        "confidence": confidence,
        "timeframe": "5m",
        "generated_at": now.isoformat(),
        "valid_until": (now + timedelta(minutes=ttl_minutes)).isoformat(),
        "risk_approved": True,
        "max_position_usdt": max_position_usdt,
        "leverage": leverage,
        "model_version": "phase9_test_signal_v1",
        "reason": "phase9_execution_validation_test",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--side", choices=["long", "short"], default="long")
    parser.add_argument("--pair", choices=["all", "BTC/USDT:USDT", "ETH/USDT:USDT"], default="all")
    parser.add_argument("--ttl-minutes", type=int, default=30)
    parser.add_argument("--confidence", type=float, default=0.99)
    parser.add_argument("--leverage", type=float, default=2.0)
    parser.add_argument("--max-position-usdt", type=float, default=50.0)
    parser.add_argument("--output", default="data/freqtrade_signals.json")
    args = parser.parse_args()

    selected_pairs = list(PAIRS) if args.pair == "all" else [args.pair]
    now = datetime.now(UTC)

    payload = {
        "generated_at": now.isoformat(),
        "runtime_mode": "paper",
        "model_version": "phase9_test_signal_v1",
        "source": "phase9_signal_execution_validation",
        "signals": [
            build_signal(
                pair=pair,
                side=args.side,
                ttl_minutes=args.ttl_minutes,
                confidence=args.confidence,
                leverage=args.leverage,
                max_position_usdt=args.max_position_usdt,
            )
            for pair in selected_pairs
        ],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "status": "ok",
        "signals": len(payload["signals"]),
        "side": args.side,
        "pairs": selected_pairs,
        "output_path": str(output),
        "valid_until": payload["signals"][0]["valid_until"] if payload["signals"] else None,
        "created_at": now.isoformat(),
    }

    report_path = Path("data/reports/phase9_test_signal_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
