#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _load_report(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-age-seconds", type=float, default=30.0)
    args = parser.parse_args()

    path = Path(args.report)
    if not path.is_file():
        return 1

    payload = _load_report(path)
    if payload is None:
        return 4

    if payload.get("status") != "ok":
        return 2

    try:
        generated = _time(payload.get("generated_at_utc"))
    except (TypeError, ValueError, OverflowError):
        return 4

    age = (datetime.now(UTC) - generated).total_seconds()
    return 0 if 0.0 <= age <= args.max_age_seconds else 3


if __name__ == "__main__":
    raise SystemExit(main())
