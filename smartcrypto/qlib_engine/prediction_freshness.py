from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


FRESH = "fresh"
STALE = "stale"
MISSING = "missing"
INVALID = "invalid"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def inspect_qlib_prediction_freshness(
    predictions_path: str | Path,
    *,
    max_allowed_age_minutes: int = 90,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = normalize_datetime(now) or utc_now()
    path = Path(predictions_path)
    base: dict[str, Any] = {
        "source_file": str(path),
        "exists": path.exists(),
        "max_allowed_age_minutes": int(max_allowed_age_minutes),
        "prediction_generated_at": None,
        "prediction_date": None,
        "generated_at_age_minutes": None,
        "date_age_minutes": None,
        "prediction_age_minutes": None,
    }
    if not path.exists():
        return {
            **base,
            "freshness_status": MISSING,
            "stale": True,
            "reason": "qlib_predictions_missing",
        }
    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        return {
            **base,
            "freshness_status": INVALID,
            "stale": True,
            "reason": "qlib_predictions_invalid",
            "error": str(exc),
        }
    return inspect_qlib_prediction_frame_freshness(
        frame,
        source_file=path,
        max_allowed_age_minutes=max_allowed_age_minutes,
        now=current,
    )


def inspect_qlib_prediction_frame_freshness(
    frame: pd.DataFrame,
    *,
    source_file: str | Path = "<memory>",
    max_allowed_age_minutes: int = 90,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = normalize_datetime(now) or utc_now()
    base: dict[str, Any] = {
        "source_file": str(source_file),
        "exists": True,
        "max_allowed_age_minutes": int(max_allowed_age_minutes),
        "prediction_generated_at": None,
        "prediction_date": None,
        "generated_at_age_minutes": None,
        "date_age_minutes": None,
        "prediction_age_minutes": None,
        "rows": int(len(frame)) if isinstance(frame, pd.DataFrame) else 0,
    }
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {
            **base,
            "freshness_status": INVALID,
            "stale": True,
            "reason": "qlib_predictions_empty_or_invalid",
        }

    timestamps: list[tuple[str, datetime, float]] = []
    invalid_columns: list[str] = []
    if "generated_at" in frame.columns:
        generated_at = latest_timestamp(frame["generated_at"])
        if generated_at is None:
            invalid_columns.append("generated_at")
        else:
            generated_age = age_minutes(generated_at, current)
            base["prediction_generated_at"] = generated_at.isoformat()
            base["generated_at_age_minutes"] = generated_age
            timestamps.append(("generated_at", generated_at, generated_age))
    if "date" in frame.columns:
        prediction_date = latest_timestamp(frame["date"])
        if prediction_date is None:
            invalid_columns.append("date")
        else:
            date_age = age_minutes(prediction_date, current)
            base["prediction_date"] = prediction_date.isoformat()
            base["date_age_minutes"] = date_age
            timestamps.append(("date", prediction_date, date_age))

    if invalid_columns or not timestamps:
        return {
            **base,
            "freshness_status": INVALID,
            "stale": True,
            "reason": "qlib_predictions_timestamp_invalid",
            "invalid_columns": invalid_columns,
        }

    max_age = max(item[2] for item in timestamps)
    base["prediction_age_minutes"] = float(max_age)
    stale_columns = [name for name, _, age in timestamps if age > int(max_allowed_age_minutes)]
    if stale_columns:
        return {
            **base,
            "freshness_status": STALE,
            "stale": True,
            "reason": "qlib_predictions_stale",
            "stale_columns": stale_columns,
        }
    return {
        **base,
        "freshness_status": FRESH,
        "stale": False,
        "reason": None,
        "stale_columns": [],
    }


def latest_timestamp(series: pd.Series) -> datetime | None:
    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    parsed = parsed.dropna()
    if parsed.empty:
        return None
    value = parsed.max().to_pydatetime()
    return normalize_datetime(value)


def normalize_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def age_minutes(value: datetime, now: datetime) -> float:
    return float((now - value).total_seconds() / 60.0)
