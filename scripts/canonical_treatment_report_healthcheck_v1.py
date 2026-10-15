#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("report_timestamp_timezone_required")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-age-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    if not math.isfinite(args.max_age_seconds) or args.max_age_seconds <= 0:
        return 4

    path = Path(args.report)
    try:
        if path.is_symlink() or not path.is_file():
            return 1
    except OSError:
        return 4

    payload = _load_report(path)
    if payload is None:
        return 4

    if payload.get("status") != "ok":
        return 2
    if payload.get("schema_version") not in {
        "canonical_treatment_signal_publisher_report_v1",
        "canonical_treatment_paper_economic_monitor_v1",
    }:
        return 4

    try:
        generated = _time(payload.get("generated_at_utc"))
    except (TypeError, ValueError, OverflowError):
        return 4

    age = (datetime.now(UTC) - generated).total_seconds()
    return 0 if 0.0 <= age <= args.max_age_seconds else 3


if __name__ == "__main__":
    raise SystemExit(main())
