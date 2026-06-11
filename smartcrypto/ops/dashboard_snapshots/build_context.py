from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ops.dashboard_snapshots.contracts import RuntimeMode


@dataclass(frozen=True)
class DashboardBuildContext:
    project_root: Path
    output_dir: Path
    now_utc: datetime
    runtime_mode: RuntimeMode
    strict: bool = False
    allow_writes_to_output_dir: bool = False


def create_dashboard_build_context(
    project_root: str | Path,
    *,
    output_dir: str | Path = "data/reports",
    now_utc: datetime | None = None,
    runtime_mode: RuntimeMode | str = RuntimeMode.paper,
    strict: bool = False,
    allow_writes_to_output_dir: bool = False,
) -> DashboardBuildContext:
    root = Path(project_root).resolve()
    output = Path(output_dir)
    if not output.is_absolute():
        output = root / output
    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    mode = runtime_mode if isinstance(runtime_mode, RuntimeMode) else _runtime_mode(runtime_mode)
    return DashboardBuildContext(
        project_root=root,
        output_dir=output.resolve(),
        now_utc=current.astimezone(timezone.utc),
        runtime_mode=mode,
        strict=bool(strict),
        allow_writes_to_output_dir=bool(allow_writes_to_output_dir),
    )


def _runtime_mode(value: object) -> RuntimeMode:
    try:
        return RuntimeMode(str(value).strip().lower())
    except ValueError:
        return RuntimeMode.unknown
