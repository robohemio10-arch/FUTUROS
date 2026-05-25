from __future__ import annotations

from datetime import datetime, timezone


def is_signal_fresh(signal: dict, now: datetime | None = None) -> bool:
    valid_until = signal.get("valid_until")
    if not valid_until:
        return False

    current_time = now or datetime.now(timezone.utc)

    try:
        parsed = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
    except ValueError:
        return False

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc) > current_time
