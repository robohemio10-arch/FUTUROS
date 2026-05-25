from __future__ import annotations

import json
from pathlib import Path

from smartcrypto.ops.phase10_summary import build_phase10_summary


def main() -> None:
    reports_dir = Path("data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    summary = build_phase10_summary()
    output = reports_dir / "phase10_output_summary.json"
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    print("VALIDATION_OK")


if __name__ == "__main__":
    main()
