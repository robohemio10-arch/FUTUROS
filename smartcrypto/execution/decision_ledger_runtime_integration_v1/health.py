"""Health classification for injected P0.4C sinks."""

from __future__ import annotations

from typing import Any, Protocol


class HealthReadable(Protocol):
    def health(self) -> dict[str, object]: ...


def classify_sink_health(sink: HealthReadable) -> dict[str, Any]:
    payload = dict(sink.health())
    status = str(payload.get("status") or "unknown")
    ready_for_runtime = False
    return {
        "status": status,
        "ready_for_runtime": ready_for_runtime,
        "paper_restart_authorized": False,
        "runtime_integration_allowed": False,
        "observed": payload,
    }
