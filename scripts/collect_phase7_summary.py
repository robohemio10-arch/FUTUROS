from __future__ import annotations

import json
from pathlib import Path


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
        "preflight": read_json("data/reports/phase7_preflight_report.json"),
        "history": read_json("data/reports/phase7_paper_history_report.json"),
        "outputs": read_json("data/reports/phase7_output_summary.json"),
    }
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase7_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
