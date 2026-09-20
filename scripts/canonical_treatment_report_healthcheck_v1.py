#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

def _time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-age-seconds", type=float, default=30.0)
    args = parser.parse_args()
    path = Path(args.report)
    if not path.is_file():
        return 1
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "ok":
            return 2
        generated = _time(payload.get("generated_at_utc"))
        age = (datetime.now(UTC) - generated).total_seconds()
        return 0 if 0.0 <= age <= args.max_age_seconds else 3
    except Exception:
        return 4

if __name__ == "__main__":
    raise SystemExit(main())
