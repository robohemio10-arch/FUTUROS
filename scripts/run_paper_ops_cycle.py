from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

from smartcrypto.ops.phase10_summary import build_phase10_summary


def main() -> None:
    reports_dir = Path("data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    summary = build_phase10_summary()
    summary["created_at"] = datetime.now(timezone.utc).isoformat()
    summary["note"] = "Host PowerShell scripts orchestrate Docker services; this script summarizes the current cycle state."

    output = reports_dir / "phase10_cycle_report.json"
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
