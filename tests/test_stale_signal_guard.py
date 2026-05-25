from datetime import datetime, timedelta, timezone

from smartcrypto.risk.stale_signal_guard import is_signal_fresh


def test_signal_fresh() -> None:
    now = datetime.now(timezone.utc)
    assert is_signal_fresh({"valid_until": (now + timedelta(minutes=1)).isoformat()}, now=now)


def test_signal_stale() -> None:
    now = datetime.now(timezone.utc)
    assert not is_signal_fresh({"valid_until": (now - timedelta(minutes=1)).isoformat()}, now=now)
