from __future__ import annotations

import argparse
import errno
import json
import os
import sqlite3
import stat
import subprocess
import tempfile
import textwrap
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_VOLUME_NAME = "futuros_freqtrade_paper_db"
DEFAULT_VOLUME_DB_PATH = "/paper-db/tradesv3.paper.sqlite"
DEFAULT_OUTPUT = Path("data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite")
DEFAULT_REPORT = Path("data/reports/freqtrade_paper_db_snapshot_export.json")
DEFAULT_DOCKER_IMAGE = "python:3.12-alpine"

TEMPFILE_ATTEMPTS = 4
REPLACE_ATTEMPTS = 5
RETRY_BASE_DELAY_SECONDS = 0.05
MAX_ERROR_LENGTH = 256

_TRANSIENT_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.EBUSY})
_TRANSIENT_WINDOWS_ERRORS = frozenset({5, 32, 33})
_PROMOTION_LOCK = threading.Lock()

Mkstemp = Callable[..., tuple[int, str]]
Replace = Callable[
    [
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ],
    None,
]
Sleep = Callable[[float], None]
Fsync = Callable[[int], None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _is_transient_filesystem_error(error: OSError) -> bool:
    return (
        isinstance(error, PermissionError)
        or error.errno in _TRANSIENT_ERRNOS
        or getattr(error, "winerror", None) in _TRANSIENT_WINDOWS_ERRORS
    )


def _retry_delay(attempt: int) -> float:
    return RETRY_BASE_DELAY_SECONDS * (2**attempt)


def _sanitized_error(error: BaseException) -> str:
    fields = [type(error).__name__]
    if isinstance(error, OSError):
        if error.errno is not None:
            fields.append(f"errno={error.errno}")
        winerror = getattr(error, "winerror", None)
        if winerror is not None:
            fields.append(f"winerror={winerror}")
    return ":".join(fields)[:MAX_ERROR_LENGTH]


def _validate_source(source: Path, target: Path) -> str | None:
    if source.resolve() == target.resolve():
        return "source_and_target_must_differ"
    if source.is_symlink():
        return "source_db_symlink_forbidden"
    try:
        metadata = source.stat(follow_symlinks=False)
    except FileNotFoundError:
        return "source_db_missing"
    except OSError:
        return "source_db_unreadable"
    if not stat.S_ISREG(metadata.st_mode):
        return "source_db_not_regular_file"
    if metadata.st_size <= 0:
        return "source_db_empty"
    return None


def _create_owned_tempfile(
    target: Path,
    *,
    mkstemp: Mkstemp,
    sleep: Sleep,
) -> tuple[Path, int]:
    for attempt in range(TEMPFILE_ATTEMPTS):
        descriptor: int | None = None
        temp_path: Path | None = None
        try:
            descriptor, raw_path = mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            temp_path = Path(raw_path)
            if temp_path.parent != target.parent:
                raise OSError(errno.EPERM, "tempfile_outside_target_directory")
            os.close(descriptor)
            descriptor = None
            return temp_path, attempt
        except OSError as exc:
            close_error: OSError | None = None
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as observed_close_error:
                    close_error = observed_close_error
            cleanup_error = _cleanup_owned_tempfile(temp_path)
            if close_error is not None or cleanup_error is not None:
                raise OSError(
                    errno.EIO,
                    "tempfile_descriptor_or_cleanup_failed",
                ) from (close_error or exc)
            is_last = attempt + 1 >= TEMPFILE_ATTEMPTS
            if not _is_transient_filesystem_error(exc) or is_last:
                raise
            sleep(_retry_delay(attempt))
    raise OSError(errno.EIO, "tempfile_retry_loop_exhausted")


def _close_connection(connection: sqlite3.Connection | None) -> None:
    if connection is not None:
        connection.close()


def _backup_to_owned_tempfile(
    source: Path,
    temp_path: Path,
    *,
    fsync: Fsync,
) -> None:
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(
            f"{source.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=30,
        )
        destination_connection = sqlite3.connect(str(temp_path), timeout=30)
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        _close_connection(destination_connection)
        _close_connection(source_connection)

    with temp_path.open("r+b") as handle:
        fsync(handle.fileno())


def _promote_owned_tempfile(
    temp_path: Path,
    target: Path,
    *,
    replace: Replace,
    sleep: Sleep,
) -> int:
    with _PROMOTION_LOCK:
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                replace(temp_path, target)
                return attempt
            except OSError as exc:
                is_last = attempt + 1 >= REPLACE_ATTEMPTS
                if not _is_transient_filesystem_error(exc) or is_last:
                    raise
                sleep(_retry_delay(attempt))
    raise OSError(errno.EIO, "replace_retry_loop_exhausted")


def _cleanup_owned_tempfile(temp_path: Path | None) -> str | None:
    if temp_path is None:
        return None
    try:
        metadata = temp_path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return _sanitized_error(exc)
    if not stat.S_ISREG(metadata.st_mode):
        return "owned_tempfile_not_regular_file"
    try:
        temp_path.unlink()
    except OSError as exc:
        return _sanitized_error(exc)
    return None


def export_local_sqlite_snapshot(
    source_db: str | Path,
    output: str | Path,
    *,
    mkstemp: Mkstemp = tempfile.mkstemp,
    replace: Replace = os.replace,
    sleep: Sleep = time.sleep,
    fsync: Fsync = os.fsync,
) -> dict[str, Any]:
    source = Path(source_db)
    target = Path(output)
    source_error = _validate_source(source, target)
    if source_error is not None:
        return snapshot_report(
            status="missing_source" if source_error == "source_db_missing" else "blocked",
            reason=source_error,
            source=str(source),
            output=str(target),
        )

    try:
        ensure_parent(target)
    except OSError as exc:
        return snapshot_report(
            status="blocked",
            reason="snapshot_parent_creation_failed",
            source=str(source),
            output=str(target),
            error=_sanitized_error(exc),
        )

    temp_path: Path | None = None
    create_retry_count = 0
    replace_retry_count = 0
    operation_error: BaseException | None = None
    reason: str | None = None
    try:
        temp_path, create_retry_count = _create_owned_tempfile(
            target,
            mkstemp=mkstemp,
            sleep=sleep,
        )
        _backup_to_owned_tempfile(source, temp_path, fsync=fsync)
        replace_retry_count = _promote_owned_tempfile(
            temp_path,
            target,
            replace=replace,
            sleep=sleep,
        )
        temp_path = None
    except OSError as exc:
        operation_error = exc
        reason = (
            "snapshot_tempfile_creation_failed"
            if temp_path is None
            else "sqlite_backup_or_promotion_failed"
        )
    except sqlite3.Error as exc:
        operation_error = exc
        reason = "sqlite_backup_failed"
    except Exception as exc:
        operation_error = exc
        reason = "sqlite_snapshot_unexpected_failure"

    cleanup_error = _cleanup_owned_tempfile(temp_path)
    if cleanup_error is not None:
        return snapshot_report(
            status="blocked",
            reason="snapshot_owned_tempfile_cleanup_failed",
            source=str(source),
            output=str(target),
            error=cleanup_error,
            tempfile_create_retry_count=create_retry_count,
            replace_retry_count=replace_retry_count,
        )
    if operation_error is not None:
        return snapshot_report(
            status="blocked",
            reason=reason or "sqlite_backup_failed",
            source=str(source),
            output=str(target),
            error=_sanitized_error(operation_error),
            tempfile_create_retry_count=create_retry_count,
            replace_retry_count=replace_retry_count,
        )

    return snapshot_report(
        status="ok",
        reason=None,
        source=str(source),
        output=str(target),
        output_size_bytes=target.stat().st_size,
        tempfile_create_retry_count=create_retry_count,
        replace_retry_count=replace_retry_count,
    )


def _docker_inline_script(*, volume_db_path: str, output_name: str) -> str:
    return textwrap.dedent(
        f"""
        import errno
        import json
        import os
        import sqlite3
        import tempfile
        import time
        from pathlib import Path

        source = Path({volume_db_path!r})
        target = Path('/snapshot') / {output_name!r}
        transient = {{errno.EACCES, errno.EPERM, errno.EBUSY}}
        temp_path = None

        def delay(attempt):
            time.sleep(0.05 * (2 ** attempt))

        def transient_error(error):
            return (
                isinstance(error, PermissionError)
                or getattr(error, 'errno', None) in transient
                or getattr(error, 'winerror', None) in {{5, 32, 33}}
            )

        def sanitized(error):
            fields = [type(error).__name__]
            if getattr(error, 'errno', None) is not None:
                fields.append(f"errno={{error.errno}}")
            return ':'.join(fields)[:256]

        def cleanup_owned(path):
            if path is None:
                return None
            try:
                path.unlink()
            except FileNotFoundError:
                return None
            except OSError as error:
                return sanitized(error)
            return None

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range({TEMPFILE_ATTEMPTS}):
                descriptor = None
                try:
                    descriptor, raw_path = tempfile.mkstemp(
                        prefix=f".{{target.name}}.",
                        suffix='.tmp',
                        dir=str(target.parent),
                    )
                    temp_path = Path(raw_path)
                    os.close(descriptor)
                    descriptor = None
                    break
                except OSError as error:
                    if descriptor is not None:
                        os.close(descriptor)
                    if not transient_error(error) or attempt + 1 >= {TEMPFILE_ATTEMPTS}:
                        raise
                    delay(attempt)

            source_connection = None
            destination_connection = None
            try:
                source_connection = sqlite3.connect(
                    f"{{source.resolve().as_uri()}}?mode=ro",
                    uri=True,
                    timeout=30,
                )
                destination_connection = sqlite3.connect(str(temp_path), timeout=30)
                source_connection.backup(destination_connection)
                destination_connection.commit()
            finally:
                if destination_connection is not None:
                    destination_connection.close()
                if source_connection is not None:
                    source_connection.close()

            with temp_path.open('r+b') as handle:
                os.fsync(handle.fileno())

            for attempt in range({REPLACE_ATTEMPTS}):
                try:
                    os.replace(temp_path, target)
                    temp_path = None
                    break
                except OSError as error:
                    if not transient_error(error) or attempt + 1 >= {REPLACE_ATTEMPTS}:
                        raise
                    delay(attempt)

            print(json.dumps({{
                'status': 'ok',
                'output': str(target),
                'size_bytes': target.stat().st_size,
            }}, sort_keys=True))
        except Exception as error:
            cleanup_error = cleanup_owned(temp_path)
            print(json.dumps({{
                'status': 'blocked',
                'reason': (
                    'snapshot_owned_tempfile_cleanup_failed'
                    if cleanup_error is not None
                    else 'sqlite_backup_or_promotion_failed'
                ),
                'error': cleanup_error or sanitized(error),
            }}, sort_keys=True))
            raise SystemExit(1)
        """
    ).strip()


def build_docker_export_command(
    *,
    volume_name: str,
    output: str | Path,
    docker_image: str,
    volume_db_path: str,
) -> list[str]:
    target = Path(output)
    return [
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=volume,source={volume_name},target=/paper-db,readonly",
        "--mount",
        f"type=bind,source={target.parent.resolve()},target=/snapshot",
        docker_image,
        "python",
        "-c",
        _docker_inline_script(
            volume_db_path=volume_db_path,
            output_name=target.name,
        ),
    ]


def export_docker_volume_snapshot(
    *,
    volume_name: str = DEFAULT_VOLUME_NAME,
    output: str | Path = DEFAULT_OUTPUT,
    report_path: str | Path = DEFAULT_REPORT,
    docker_image: str = DEFAULT_DOCKER_IMAGE,
    volume_db_path: str = DEFAULT_VOLUME_DB_PATH,
) -> dict[str, Any]:
    target = Path(output)
    try:
        ensure_parent(target)
    except OSError as exc:
        payload = snapshot_report(
            status="blocked",
            reason="snapshot_parent_creation_failed",
            source=f"docker-volume:{volume_name}:{volume_db_path}",
            output=str(target),
            error=_sanitized_error(exc),
        )
        write_json(Path(report_path), payload)
        return payload

    command = build_docker_export_command(
        volume_name=volume_name,
        output=target,
        docker_image=docker_image,
        volume_db_path=volume_db_path,
    )
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            timeout=120,
        )
    except Exception as exc:
        payload = snapshot_report(
            status="blocked",
            reason="docker_snapshot_export_failed",
            source=f"docker-volume:{volume_name}:{volume_db_path}",
            output=str(target),
            error=_sanitized_error(exc),
        )
        write_json(Path(report_path), payload)
        return payload

    success = completed.returncode == 0 and target.is_file()
    payload = snapshot_report(
        status="ok" if success else "blocked",
        reason=None if success else "docker_snapshot_export_failed",
        source=f"docker-volume:{volume_name}:{volume_db_path}",
        output=str(target),
        output_size_bytes=target.stat().st_size if success else None,
        docker_returncode=completed.returncode,
        docker_stdout=completed.stdout.strip()[:4000],
        docker_stderr=completed.stderr.strip()[:4000],
    )
    write_json(Path(report_path), payload)
    return payload


def snapshot_report(
    *,
    status: str,
    reason: str | None,
    source: str,
    output: str,
    output_size_bytes: int | None = None,
    error: str | None = None,
    docker_returncode: int | None = None,
    docker_stdout: str | None = None,
    docker_stderr: str | None = None,
    tempfile_create_retry_count: int = 0,
    replace_retry_count: int = 0,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "source": source,
        "output": output,
        "output_size_bytes": output_size_bytes,
        "error": error,
        "docker_returncode": docker_returncode,
        "docker_stdout": docker_stdout,
        "docker_stderr": docker_stderr,
        "tempfile_strategy": "exclusive_same_directory",
        "tempfile_create_retry_count": tempfile_create_retry_count,
        "replace_retry_count": replace_retry_count,
        "source_db_read_only": True,
        "target_preserved_on_failure": True,
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "write_performed": status == "ok",
        "created_at": utc_now(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a read-only snapshot of the Freqtrade paper SQLite named volume."
    )
    parser.add_argument("--volume-name", default=DEFAULT_VOLUME_NAME)
    parser.add_argument("--volume-db-path", default=DEFAULT_VOLUME_DB_PATH)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument(
        "--local-source-db",
        default=None,
        help="Test/diagnostic mode: backup a local SQLite file without Docker.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.local_source_db:
        payload = export_local_sqlite_snapshot(args.local_source_db, args.output)
        write_json(Path(args.report), payload)
    else:
        payload = export_docker_volume_snapshot(
            volume_name=args.volume_name,
            output=args.output,
            report_path=args.report,
            docker_image=args.docker_image,
            volume_db_path=args.volume_db_path,
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
