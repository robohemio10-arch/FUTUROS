from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


REQUIRED_SIGNAL_FIELDS = {
    "pair",
    "side",
    "score",
    "confidence",
    "valid_until",
    "risk_approved",
    "leverage",
}


def main() -> None:
    path = Path("data/freqtrade_signals.json")
    report_path = Path("data/reports/phase9_signal_contract_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        report = {
            "status": "error",
            "reason": "freqtrade_signals_missing",
            "path": str(path),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    payload = json.loads(path.read_text(encoding="utf-8"))
    signals = payload.get("signals", [])

    errors = []
    now = datetime.now(UTC)

    if not isinstance(signals, list) or not signals:
        errors.append("signals_empty_or_not_list")

    for index, signal in enumerate(signals if isinstance(signals, list) else []):
        missing = sorted(REQUIRED_SIGNAL_FIELDS - set(signal))
        if missing:
            errors.append(f"signal_{index}_missing_{','.join(missing)}")

        if signal.get("side") not in {"long", "short"}:
            errors.append(f"signal_{index}_invalid_side")

        try:
            valid_until = pd.to_datetime(signal.get("valid_until"), utc=True).to_pydatetime()
            if now > valid_until:
                errors.append(f"signal_{index}_expired")
        except Exception:
            errors.append(f"signal_{index}_invalid_valid_until")

        if not bool(signal.get("risk_approved", False)):
            errors.append(f"signal_{index}_risk_not_approved")

    report = {
        "status": "ok" if not errors else "error",
        "path": str(path),
        "signals": len(signals) if isinstance(signals, list) else 0,
        "errors": errors,
        "generated_at": payload.get("generated_at"),
        "model_version": payload.get("model_version"),
        "source": payload.get("source"),
        "checked_at": now.isoformat(),
    }

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
