from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping


SnapshotSourceStatus = Literal[
    "OK",
    "MISSING",
    "INVALID_EMPTY",
    "INVALID_JSON",
    "INVALID_SCHEMA",
    "BLOCKED",
    "IO_ERROR",
]
SNAPSHOT_CACHE_TTL_SECONDS = 2
SnapshotByteReader = Callable[[str, int, int], bytes]


@dataclass(frozen=True)
class SnapshotJsonReadResult:
    status: SnapshotSourceStatus
    reason: str
    path: str
    data: Mapping[str, Any] | None = None
    cache_enabled: bool = False
    mtime_ns: int | None = None
    size_bytes: int | None = None


def _read_snapshot_bytes(path: str, mtime_ns: int, size_bytes: int) -> bytes:
    del mtime_ns, size_bytes
    return Path(path).read_bytes()


def build_snapshot_byte_reader(streamlit_module: Any | None) -> tuple[SnapshotByteReader, bool]:
    if streamlit_module is None:
        return _read_snapshot_bytes, False
    cache_data = getattr(streamlit_module, "cache_data", None)
    if not callable(cache_data):
        return _read_snapshot_bytes, False
    try:
        cached = cache_data(ttl=SNAPSHOT_CACHE_TTL_SECONDS, show_spinner=False)(_read_snapshot_bytes)
    except (AttributeError, RuntimeError, TypeError):
        return _read_snapshot_bytes, False
    return cached, True


def _optional_streamlit() -> Any | None:
    try:
        import streamlit
    except ImportError:
        return None
    return streamlit


_SNAPSHOT_BYTE_READER, STREAMLIT_CACHE_ENABLED = build_snapshot_byte_reader(_optional_streamlit())


def load_snapshot_json(
    path: str | Path,
    *,
    project_root: str | Path = ".",
) -> SnapshotJsonReadResult:
    root = Path(project_root).resolve()
    target = resolve_snapshot_path(root, path)
    if target is None:
        return SnapshotJsonReadResult(
            status="BLOCKED",
            reason="path_outside_project_root",
            path=str(Path(path)),
            cache_enabled=STREAMLIT_CACHE_ENABLED,
        )
    if not target.is_file():
        return SnapshotJsonReadResult(
            status="MISSING",
            reason="file_not_found",
            path=str(target),
            cache_enabled=STREAMLIT_CACHE_ENABLED,
        )
    try:
        signature = target.stat()
        content = _SNAPSHOT_BYTE_READER(
            str(target),
            signature.st_mtime_ns,
            signature.st_size,
        )
    except OSError as exc:
        return SnapshotJsonReadResult(
            status="IO_ERROR",
            reason=f"snapshot_read_failed:{type(exc).__name__}",
            path=str(target),
            cache_enabled=STREAMLIT_CACHE_ENABLED,
        )
    if not content.strip():
        return SnapshotJsonReadResult(
            status="INVALID_EMPTY",
            reason="snapshot_file_empty",
            path=str(target),
            cache_enabled=STREAMLIT_CACHE_ENABLED,
            mtime_ns=signature.st_mtime_ns,
            size_bytes=signature.st_size,
        )
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return SnapshotJsonReadResult(
            status="INVALID_JSON",
            reason=f"snapshot_json_invalid:{type(exc).__name__}",
            path=str(target),
            cache_enabled=STREAMLIT_CACHE_ENABLED,
            mtime_ns=signature.st_mtime_ns,
            size_bytes=signature.st_size,
        )
    if not isinstance(payload, Mapping):
        return SnapshotJsonReadResult(
            status="INVALID_SCHEMA",
            reason="snapshot_root_not_object",
            path=str(target),
            cache_enabled=STREAMLIT_CACHE_ENABLED,
            mtime_ns=signature.st_mtime_ns,
            size_bytes=signature.st_size,
        )
    return SnapshotJsonReadResult(
        status="OK",
        reason="snapshot_json_loaded",
        path=str(target),
        data=dict(payload),
        cache_enabled=STREAMLIT_CACHE_ENABLED,
        mtime_ns=signature.st_mtime_ns,
        size_bytes=signature.st_size,
    )


def resolve_snapshot_path(root: Path, path: str | Path) -> Path | None:
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved
