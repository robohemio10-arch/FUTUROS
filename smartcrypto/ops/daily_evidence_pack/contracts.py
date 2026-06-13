from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PackStatus = Literal["ok", "warning", "blocked"]


@dataclass(frozen=True)
class StepDefinition:
    name: str
    script: str
    arguments: tuple[str, ...]
    supports_container_snapshot: bool = False


@dataclass(frozen=True)
class LockState:
    acquired: bool
    recovered_stale_lock: bool
    reason: str
