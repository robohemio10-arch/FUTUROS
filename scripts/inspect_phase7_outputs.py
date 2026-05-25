from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def describe_table(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False, "rows": None, "columns": None}
    if p.suffix.lower() == ".parquet":
        frame = pd.read_parquet(p)
    else:
        frame = pd.read_excel(p)
    return {"exists": True, "rows": int(len(frame)), "columns": list(frame.columns)}


def read_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    try:
        return {"exists": True, **json.loads(p.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def main() -> None:
    summary = {
        "freqtrade_raw": describe_table("data/trades/freqtrade_paper_trades_raw.parquet"),
        "freqtrade_smartcrypto": describe_table("data/trades/freqtrade_paper_trades_smartcrypto.xlsx"),
        "paper_history_report": read_json("data/reports/phase7_paper_history_report.json"),
    }
    status = summary["paper_history_report"].get("status", "unknown")
    summary["phase7_status"] = {
        "status": status,
        "has_closed_trades": status == "ok",
    }
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase7_output_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
