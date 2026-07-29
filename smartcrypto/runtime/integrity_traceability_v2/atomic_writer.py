"""Durable same-filesystem writes for shared paper/shadow artifacts."""

from __future__ import annotations

import errno
import importlib
import json
import os
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

Mkstemp = Callable[..., tuple[int, str]]
Replace = Callable[
    [
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ],
    None,
]
Fsync = Callable[[int], None]
Sleep = Callable[[float], None]

LOCK_ATTEMPTS = 100
LOCK_RETRY_SECONDS = 0.01
REPLACE_ATTEMPTS = 100
REPLACE_RETRY_SECONDS = 0.005
MAX_ERROR_TEXT = 256

_UNSUPPORTED_DIRECTORY_FSYNC = frozenset(
    value
    for value in (
        errno.EINVAL,
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
    )
    if value is not None
)
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class AtomicWriteError(RuntimeError):
    """Fail-closed write error exposing a stable reason without source data."""

    def __init__(
        self,
        reason: str,
        *,
        target: Path | None = None,
        promoted: bool = False,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.target = target
        self.promoted = promoted


class ConsistentReadError(RuntimeError):
    """Fail-closed read error after bounded transient retries."""

    def __init__(self, reason: str, *, target: Path) -> None:
        super().__init__(reason)
        self.reason = reason
        self.target = target


@dataclass(frozen=True)
class AtomicWritePolicy:
    working_directory: Path
    authorized_roots: tuple[Path, ...]
    lock_timeout_seconds: float = 5.0
    file_mode: int = 0o600
    replace_attempts: int = REPLACE_ATTEMPTS
    replace_retry_seconds: float = REPLACE_RETRY_SECONDS

    @classmethod
    def project_data(
        cls,
        *,
        working_directory: str | Path | None = None,
        include_system_temp: bool = True,
    ) -> "AtomicWritePolicy":
        root = _absolute_working_directory(working_directory)
        authorized = [(root / "data").resolve(strict=False)]
        if include_system_temp:
            authorized.append(Path(tempfile.gettempdir()).resolve(strict=False))
        return cls(
            working_directory=root,
            authorized_roots=tuple(_deduplicate_paths(authorized)),
        )

    @classmethod
    def restricted(
        cls,
        authorized_roots: Sequence[str | Path],
        *,
        working_directory: str | Path | None = None,
        lock_timeout_seconds: float = 5.0,
    ) -> "AtomicWritePolicy":
        root = _absolute_working_directory(working_directory)
        normalized = [
            _resolve_against(root, Path(item)).resolve(strict=False)
            for item in authorized_roots
        ]
        if not normalized:
            raise ValueError("authorized_roots_required")
        return cls(
            working_directory=root,
            authorized_roots=tuple(_deduplicate_paths(normalized)),
            lock_timeout_seconds=float(lock_timeout_seconds),
        )


@dataclass(frozen=True)
class AtomicWriteResult:
    status: str
    target: str
    bytes_written: int
    parent_directory_fsync_status: str
    temporary_same_directory: bool
    lock_serialized: bool
    write_performed: bool


def atomic_write_json(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    policy: AtomicWritePolicy | None = None,
    sort_keys: bool = True,
    indent: int = 2,
    default: Callable[[Any], Any] = str,
    allow_nan: bool = True,
) -> AtomicWriteResult:
    rendered = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=sort_keys,
            indent=indent,
            default=default,
            allow_nan=allow_nan,
        )
        + "\n"
    )
    return atomic_write_text(path, rendered, policy=policy)


def atomic_write_text(
    path: str | Path,
    content: str,
    *,
    policy: AtomicWritePolicy | None = None,
    encoding: str = "utf-8",
    mkstemp: Mkstemp = tempfile.mkstemp,
    replace: Replace = os.replace,
    fsync: Fsync = os.fsync,
    sleep: Sleep = time.sleep,
) -> AtomicWriteResult:
    if not isinstance(content, str):
        raise TypeError("atomic_text_content_must_be_string")
    encoded = content.encode(encoding)
    resolved_policy = policy or AtomicWritePolicy.project_data()
    target = resolve_authorized_target(path, policy=resolved_policy)
    with _serialized_target(target, policy=resolved_policy):
        return _atomic_replace_bytes_locked(
            target,
            encoded,
            policy=resolved_policy,
            mkstemp=mkstemp,
            replace=replace,
            fsync=fsync,
            sleep=sleep,
        )


def atomic_append_jsonl(
    path: str | Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    policy: AtomicWritePolicy | None = None,
    default: Callable[[Any], Any] = str,
    mkstemp: Mkstemp = tempfile.mkstemp,
    replace: Replace = os.replace,
    fsync: Fsync = os.fsync,
    sleep: Sleep = time.sleep,
) -> AtomicWriteResult:
    materialized = [dict(row) for row in rows]
    if not materialized:
        raise AtomicWriteError("jsonl_append_requires_rows")

    resolved_policy = policy or AtomicWritePolicy.project_data()
    target = resolve_authorized_target(path, policy=resolved_policy)
    with _serialized_target(target, policy=resolved_policy):
        existing = _read_valid_jsonl_bytes(target)
        rendered = "".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                default=default,
                allow_nan=False,
            )
            + "\n"
            for row in materialized
        ).encode("utf-8")
        return _atomic_replace_bytes_locked(
            target,
            existing + rendered,
            policy=resolved_policy,
            mkstemp=mkstemp,
            replace=replace,
            fsync=fsync,
            sleep=sleep,
        )


def read_json_consistent(
    path: str | Path,
    *,
    policy: AtomicWritePolicy | None = None,
    attempts: int = 20,
    retry_seconds: float = 0.005,
    sleep: Sleep = time.sleep,
) -> Any:
    resolved_policy = policy or AtomicWritePolicy.project_data()
    target = resolve_authorized_target(path, policy=resolved_policy)
    max_attempts = max(1, int(attempts))
    last_reason = "json_read_failed"

    for attempt in range(max_attempts):
        try:
            metadata = target.stat(follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode):
                raise ConsistentReadError(
                    "json_target_not_regular_file",
                    target=target,
                )
            raw = target.read_text(encoding="utf-8-sig")
            if not raw:
                last_reason = "json_target_empty"
            else:
                return json.loads(raw)
        except FileNotFoundError:
            last_reason = "json_target_missing"
        except PermissionError:
            last_reason = "json_target_temporarily_unavailable"
        except UnicodeError:
            last_reason = "json_target_invalid_encoding"
        except json.JSONDecodeError:
            last_reason = "json_target_invalid_json"
        except ConsistentReadError:
            raise
        except OSError as exc:
            raise ConsistentReadError(
                "json_target_unreadable",
                target=target,
            ) from exc

        if attempt + 1 < max_attempts:
            sleep(max(0.0, float(retry_seconds)))

    raise ConsistentReadError(last_reason, target=target)


def resolve_authorized_target(
    path: str | Path,
    *,
    policy: AtomicWritePolicy,
) -> Path:
    logical = Path(path)
    if not logical.is_absolute() and ".." in logical.parts:
        raise AtomicWriteError("path_traversal_forbidden")

    candidate = _resolve_against(policy.working_directory, logical)
    _reject_existing_symlink_components(candidate)
    target = candidate.resolve(strict=False)
    if not any(_is_within(target, root) for root in policy.authorized_roots):
        raise AtomicWriteError("target_outside_authorized_roots")
    return target


def _atomic_replace_bytes_locked(
    target: Path,
    content: bytes,
    *,
    policy: AtomicWritePolicy,
    mkstemp: Mkstemp,
    replace: Replace,
    fsync: Fsync,
    sleep: Sleep,
) -> AtomicWriteResult:
    _prepare_target_parent(target, policy=policy)
    descriptor: int | None = None
    temporary: Path | None = None
    promoted = False
    try:
        descriptor, raw_path = mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(target.parent),
        )
        temporary = Path(raw_path).resolve(strict=False)
        if temporary.parent != target.parent.resolve(strict=False):
            raise AtomicWriteError(
                "temporary_outside_target_directory",
                target=target,
            )
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            written = handle.write(content)
            if written != len(content):
                raise AtomicWriteError("short_write_detected", target=target)
            handle.flush()
            fsync(handle.fileno())
        _apply_file_mode(temporary, policy.file_mode)
        _replace_with_retry(
            temporary,
            target,
            replace=replace,
            policy=policy,
            sleep=sleep,
        )
        temporary = None
        promoted = True
        parent_status = _fsync_parent_directory(target.parent, fsync=fsync)
        return AtomicWriteResult(
            status="ok",
            target=str(target),
            bytes_written=len(content),
            parent_directory_fsync_status=parent_status,
            temporary_same_directory=True,
            lock_serialized=True,
            write_performed=True,
        )
    except AtomicWriteError:
        raise
    except OSError as exc:
        reason = (
            "parent_directory_fsync_failed"
            if promoted
            else "atomic_file_write_failed"
        )
        raise AtomicWriteError(reason, target=target, promoted=promoted) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                if temporary is not None:
                    _cleanup_owned_temporary(temporary)
                raise AtomicWriteError(
                    "temporary_descriptor_close_failed",
                    target=target,
                    promoted=promoted,
                ) from exc
        cleanup_error = _cleanup_owned_temporary(temporary)
        if cleanup_error is not None:
            raise AtomicWriteError(
                "owned_temporary_cleanup_failed",
                target=target,
                promoted=promoted,
            ) from cleanup_error


def _prepare_target_parent(target: Path, *, policy: AtomicWritePolicy) -> None:
    _reject_existing_symlink_components(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AtomicWriteError("target_parent_creation_failed", target=target) from exc
    _reject_existing_symlink_components(target)

    try:
        parent_metadata = target.parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise AtomicWriteError("target_parent_unreadable", target=target) from exc
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise AtomicWriteError("target_parent_not_directory", target=target)
    if not any(_is_within(target, root) for root in policy.authorized_roots):
        raise AtomicWriteError("target_outside_authorized_roots", target=target)

    try:
        metadata = target.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AtomicWriteError("target_unreadable", target=target) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise AtomicWriteError("target_not_regular_file", target=target)


def _replace_with_retry(
    source: Path,
    target: Path,
    *,
    replace: Replace,
    policy: AtomicWritePolicy,
    sleep: Sleep,
) -> None:
    attempts = max(1, int(policy.replace_attempts))
    for attempt in range(attempts):
        try:
            replace(source, target)
            return
        except OSError as exc:
            if not _replace_contention(exc) or attempt + 1 >= attempts:
                raise
            sleep(max(0.0, float(policy.replace_retry_seconds)))


def _replace_contention(error: OSError) -> bool:
    return os.name == "nt" and (
        error.errno in {errno.EACCES, errno.EPERM}
        or getattr(error, "winerror", None) in {5, 32, 33}
    )


def _read_valid_jsonl_bytes(target: Path) -> bytes:
    try:
        content = target.read_bytes()
    except FileNotFoundError:
        return b""
    except OSError as exc:
        raise AtomicWriteError("existing_jsonl_unreadable", target=target) from exc
    if not content:
        return b""
    if not content.endswith(b"\n"):
        raise AtomicWriteError("existing_jsonl_missing_terminal_newline", target=target)
    try:
        lines = content.decode("utf-8").splitlines()
        for line in lines:
            if line.strip():
                json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AtomicWriteError("existing_jsonl_invalid", target=target) from exc
    return content


@contextmanager
def _serialized_target(
    target: Path,
    *,
    policy: AtomicWritePolicy,
) -> Iterator[None]:
    _prepare_target_parent(target, policy=policy)
    thread_lock = _thread_lock_for(target)
    with thread_lock:
        lock = _InterProcessFileLock(
            target.parent / f".{target.name}.atomic.lock",
            timeout_seconds=policy.lock_timeout_seconds,
        )
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


class _InterProcessFileLock:
    def __init__(self, path: Path, *, timeout_seconds: float) -> None:
        self.path = path
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.descriptor: int | None = None

    def acquire(self) -> None:
        if _path_is_symlink(self.path):
            raise AtomicWriteError("lock_path_symlink_forbidden", target=self.path)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise AtomicWriteError("lock_open_failed", target=self.path) from exc
        self.descriptor = descriptor
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise AtomicWriteError("lock_not_regular_file", target=self.path)
            if metadata.st_size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            deadline = time.monotonic() + self.timeout_seconds
            for attempt in range(LOCK_ATTEMPTS):
                try:
                    _lock_descriptor(descriptor)
                    return
                except OSError as exc:
                    if not _lock_contention(exc):
                        raise AtomicWriteError(
                            "lock_acquire_failed",
                            target=self.path,
                        ) from exc
                    if time.monotonic() >= deadline or attempt + 1 >= LOCK_ATTEMPTS:
                        raise AtomicWriteError(
                            "lock_timeout",
                            target=self.path,
                        ) from exc
                    time.sleep(LOCK_RETRY_SECONDS)
        except (OSError, AtomicWriteError):
            self._close()
            raise

    def release(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        try:
            _unlock_descriptor(descriptor)
        except OSError as exc:
            self._close()
            raise AtomicWriteError("lock_release_failed", target=self.path) from exc
        self._close()

    def _close(self) -> None:
        descriptor = self.descriptor
        self.descriptor = None
        if descriptor is None:
            return
        try:
            os.close(descriptor)
        except OSError as exc:
            raise AtomicWriteError("lock_close_failed", target=self.path) from exc


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        msvcrt: Any = importlib.import_module("msvcrt")
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return

    fcntl: Any = importlib.import_module("fcntl")
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        msvcrt: Any = importlib.import_module("msvcrt")
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return

    fcntl: Any = importlib.import_module("fcntl")
    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _lock_contention(error: OSError) -> bool:
    return (
        isinstance(error, BlockingIOError)
        or error.errno in {errno.EACCES, errno.EAGAIN}
        or getattr(error, "winerror", None) in {33, 36}
    )


def _fsync_parent_directory(parent: Path, *, fsync: Fsync) -> str:
    descriptor: int | None = None
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(parent, flags)
        fsync(descriptor)
    except OSError as exc:
        if _directory_fsync_unsupported(exc):
            return "unsupported"
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return "ok"


def _directory_fsync_unsupported(error: OSError) -> bool:
    if error.errno in _UNSUPPORTED_DIRECTORY_FSYNC:
        return True
    return os.name == "nt" and (
        error.errno in {errno.EACCES, errno.EPERM, errno.EISDIR}
        or getattr(error, "winerror", None) in {5, 32, 33}
    )


def _cleanup_owned_temporary(path: Path | None) -> OSError | None:
    if path is None:
        return None
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return exc
    if not stat.S_ISREG(metadata.st_mode):
        return OSError(errno.EPERM, "owned_temporary_not_regular_file")
    try:
        path.unlink()
    except OSError as exc:
        return exc
    return None


def _reject_existing_symlink_components(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            if _path_is_symlink(candidate):
                raise AtomicWriteError(
                    "symlink_path_component_forbidden",
                    target=path,
                )
        except OSError as exc:
            raise AtomicWriteError(
                "path_component_unreadable",
                target=path,
            ) from exc


def _path_is_symlink(path: Path) -> bool:
    return path.is_symlink()


def _thread_lock_for(target: Path) -> threading.RLock:
    key = os.path.normcase(str(target))
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


def _absolute_working_directory(value: str | Path | None) -> Path:
    candidate = Path.cwd() if value is None else Path(value)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve(strict=False)


def _resolve_against(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _deduplicate_paths(paths: Sequence[Path]) -> list[Path]:
    observed: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = os.path.normcase(str(path))
        if key not in observed:
            observed.add(key)
            result.append(path)
    return result


def _apply_file_mode(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError as exc:
        if os.name != "nt":
            raise AtomicWriteError("temporary_chmod_failed", target=path) from exc
