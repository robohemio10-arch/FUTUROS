from __future__ import annotations

import argparse
import errno
import json
import os
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NoReturn, cast


SAFE_FLAGS = {
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
}

DEFAULT_UID = 10001
DEFAULT_GID = 10001
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600

WRITABILITY_PROBE_PREFIX = ".runtime-writability-probe."
WRITABILITY_PROBE_SUFFIX = ".tmp"
WRITABILITY_PROBE_ATTEMPTS = 8
WRITABILITY_PROBE_BASE_DELAY_SECONDS = 0.05
WRITABILITY_PROBE_PAYLOAD = (
    b"smartcrypto-runtime-writability-probe\n"
)

_TRANSIENT_FILESYSTEM_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EPERM,
        errno.EBUSY,
    }
)

_TRANSIENT_WINDOWS_ERRORS = frozenset(
    {
        5,
        32,
        33,
    }
)

PHASE14_SERVICE = "phase14-feedback-sync-paper"
AUTOLEARNING_SERVICE = "paper-autolearning-scheduler"
NOTIFICATION_SERVICE = "trade-event-notifications-paper"
QLIB_REFRESH_SERVICE = "qlib-refresh-supervisor-paper"

ALLOWED_RUNTIME_PATHS = frozenset(
    {
        "/app/data/reports",
        "/app/data/runtime",
        "/app/data/trades",
        "/app/data/feedback",
        "/app/data/features",
        "/app/data/predictions",
        "/app/data/snapshots/freqtrade-paper",
    }
)


class RuntimeBootstrapError(RuntimeError):
    """Controlled fail-closed bootstrap error."""


@dataclass(frozen=True)
class RuntimePermissionProfile:
    service: str
    directories: tuple[str, ...]
    covered_files: tuple[str, ...] = ()


SERVICE_PROFILES: dict[str, RuntimePermissionProfile] = {
    PHASE14_SERVICE: RuntimePermissionProfile(
        service=PHASE14_SERVICE,
        directories=(
            "/app/data/reports",
            "/app/data/trades",
            "/app/data/snapshots/freqtrade-paper",
        ),
    ),
    AUTOLEARNING_SERVICE: RuntimePermissionProfile(
        service=AUTOLEARNING_SERVICE,
        directories=(
            "/app/data/reports",
            "/app/data/feedback",
        ),
    ),
    NOTIFICATION_SERVICE: RuntimePermissionProfile(
        service=NOTIFICATION_SERVICE,
        directories=(
            "/app/data/reports",
            "/app/data/runtime",
        ),
    ),
    QLIB_REFRESH_SERVICE: RuntimePermissionProfile(
        service=QLIB_REFRESH_SERVICE,
        directories=(
            "/app/data/runtime",
            "/app/data/reports",
            "/app/data/features",
            "/app/data/predictions",
        ),
        covered_files=(
            "/app/data/runtime/active_freqtrade_signals.json",
            "/app/data/reports/qlib_market_features_refresh_report.json",
            "/app/data/reports/qlib_market_features_refresh_report.json.tmp",
        ),
    ),
}

Chown = Callable[[Path, int, int], None]
Chmod = Callable[[Path, int], None]
ProbeFactory = Callable[[Path], BinaryIO]
Fsync = Callable[[int], None]
Sleep = Callable[[float], None]


def non_root_identifier(value: str) -> int:
    try:
        identifier = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "uid/gid must be an integer"
        ) from exc

    if identifier <= 0:
        raise argparse.ArgumentTypeError(
            "uid/gid must identify a non-root account"
        )

    return identifier


def allowed_runtime_path(value: str) -> str:
    if not value.startswith("/") or "\x00" in value:
        raise argparse.ArgumentTypeError(
            "runtime path must be absolute"
        )

    if "\\" in value:
        raise argparse.ArgumentTypeError(
            "backslashes are forbidden in runtime paths"
        )

    candidate = PurePosixPath(value)

    if any(part in {".", ".."} for part in candidate.parts):
        raise argparse.ArgumentTypeError(
            "runtime path traversal is forbidden"
        )

    normalized = str(candidate)

    if (
        normalized != value
        or normalized not in ALLOWED_RUNTIME_PATHS
    ):
        allowed = ",".join(
            sorted(ALLOWED_RUNTIME_PATHS)
        )
        raise argparse.ArgumentTypeError(
            f"path must be one of:{allowed}"
        )

    return normalized


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare exact paper-runtime bind-mount destinations, "
            "drop privileges, verify non-root writability and exec."
        )
    )
    parser.add_argument(
        "--service",
        choices=sorted(SERVICE_PROFILES),
        default=NOTIFICATION_SERVICE,
    )
    parser.add_argument(
        "--uid",
        type=non_root_identifier,
        default=DEFAULT_UID,
    )
    parser.add_argument(
        "--gid",
        type=non_root_identifier,
        default=DEFAULT_GID,
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        type=allowed_runtime_path,
        help=(
            "Exact runtime directory declared by the "
            "selected service profile."
        ),
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
    )

    args = parser.parse_args(argv)

    if args.command and args.command[0] == "--":
        args.command = args.command[1:]

    if not args.command:
        parser.error("missing command after --")

    return args


def validate_requested_paths(
    profile: RuntimePermissionProfile,
    paths: Sequence[str],
) -> None:
    validate_profile_contract(profile)
    requested = tuple(paths)

    if len(requested) != len(set(requested)):
        raise RuntimeBootstrapError(
            "duplicate_runtime_path"
        )

    if set(requested) != set(profile.directories):
        raise RuntimeBootstrapError(
            "service_runtime_path_contract_mismatch"
        )


def validate_profile_contract(
    profile: RuntimePermissionProfile,
) -> None:
    if len(profile.directories) != len(
        set(profile.directories)
    ):
        raise RuntimeBootstrapError(
            "duplicate_profile_directory"
        )

    if len(profile.covered_files) != len(
        set(profile.covered_files)
    ):
        raise RuntimeBootstrapError(
            "duplicate_profile_file"
        )

    if (
        not profile.directories
        or any(
            directory not in ALLOWED_RUNTIME_PATHS
            for directory in profile.directories
        )
    ):
        raise RuntimeBootstrapError(
            "profile_directory_not_allowlisted"
        )

    directories = tuple(
        PurePosixPath(directory)
        for directory in profile.directories
    )

    for value in profile.covered_files:
        if (
            not value.startswith("/")
            or "\x00" in value
            or "\\" in value
        ):
            raise RuntimeBootstrapError(
                "profile_file_path_invalid"
            )

        candidate = PurePosixPath(value)

        if (
            any(
                part in {".", ".."}
                for part in candidate.parts
            )
            or str(candidate) != value
        ):
            raise RuntimeBootstrapError(
                "profile_file_path_invalid"
            )

        if not any(
            candidate.is_relative_to(directory)
            for directory in directories
        ):
            raise RuntimeBootstrapError(
                "profile_file_outside_authorized_directory"
            )


def reject_symlink_components(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            if candidate.is_symlink():
                raise RuntimeBootstrapError(
                    "symlink_path_forbidden"
                )
        except OSError as exc:
            raise RuntimeBootstrapError(
                "path_metadata_unreadable"
            ) from exc


def platform_chown(
    path: Path,
    uid: int,
    gid: int,
) -> None:
    try:
        os.chown(  # type: ignore[attr-defined]
            path,
            uid,
            gid,
            follow_symlinks=False,
        )
    except AttributeError as exc:
        raise RuntimeBootstrapError(
            "posix_chown_unavailable"
        ) from exc


def platform_chmod(
    path: Path,
    mode: int,
) -> None:
    try:
        os.chmod(
            path,
            mode,
            follow_symlinks=False,
        )
    except (NotImplementedError, TypeError):
        os.chmod(path, mode)


def apply_ownership(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
    chown: Chown,
    chmod: Chmod,
) -> None:
    try:
        chown(path, uid, gid)
        chmod(path, mode)
    except RuntimeBootstrapError:
        raise
    except OSError as exc:
        raise RuntimeBootstrapError(
            "runtime_path_permission_failed"
        ) from exc


def ensure_runtime_path(
    path: Path,
    *,
    uid: int,
    gid: int,
    chown: Chown = platform_chown,
    chmod: Chmod = platform_chmod,
) -> dict[str, int]:
    reject_symlink_components(path)

    if not path.parent.is_dir():
        raise RuntimeBootstrapError(
            "authorized_parent_directory_missing"
        )

    try:
        path.mkdir(exist_ok=True)
    except OSError as exc:
        raise RuntimeBootstrapError(
            "runtime_directory_creation_failed"
        ) from exc

    reject_symlink_components(path)

    if not path.is_dir():
        raise RuntimeBootstrapError(
            "runtime_target_not_directory"
        )

    directory_count = 0
    file_count = 0

    try:
        for (
            root_value,
            directory_names,
            file_names,
        ) in os.walk(
            path,
            topdown=True,
            followlinks=False,
        ):
            root = Path(root_value)
            reject_symlink_components(root)

            apply_ownership(
                root,
                uid=uid,
                gid=gid,
                mode=DIRECTORY_MODE,
                chown=chown,
                chmod=chmod,
            )
            directory_count += 1

            for name in directory_names:
                directory = root / name

                if directory.is_symlink():
                    raise RuntimeBootstrapError(
                        "runtime_child_symlink_forbidden"
                    )

                mode = directory.stat(
                    follow_symlinks=False
                ).st_mode

                if not stat.S_ISDIR(mode):
                    raise RuntimeBootstrapError(
                        "runtime_child_not_directory"
                    )

            for name in file_names:
                file_path = root / name

                if file_path.is_symlink():
                    raise RuntimeBootstrapError(
                        "runtime_child_symlink_forbidden"
                    )

                mode = file_path.stat(
                    follow_symlinks=False
                ).st_mode

                if not stat.S_ISREG(mode):
                    raise RuntimeBootstrapError(
                        "runtime_child_not_regular_file"
                    )

                apply_ownership(
                    file_path,
                    uid=uid,
                    gid=gid,
                    mode=FILE_MODE,
                    chown=chown,
                    chmod=chmod,
                )
                file_count += 1
    except RuntimeBootstrapError:
        raise
    except OSError as exc:
        raise RuntimeBootstrapError(
            "runtime_path_traversal_failed"
        ) from exc

    return {
        "directory_count": directory_count,
        "file_count": file_count,
    }


def prepare_runtime_permissions(
    profile: RuntimePermissionProfile,
    *,
    uid: int,
    gid: int,
    chown: Chown = platform_chown,
    chmod: Chmod = platform_chmod,
) -> dict[str, int]:
    if os.geteuid() != 0:  # type: ignore[attr-defined]
        raise RuntimeBootstrapError(
            "root_required_for_permission_bootstrap"
        )

    directory_count = 0
    file_count = 0

    for directory in profile.directories:
        summary = ensure_runtime_path(
            Path(directory),
            uid=uid,
            gid=gid,
            chown=chown,
            chmod=chmod,
        )
        directory_count += summary["directory_count"]
        file_count += summary["file_count"]

    return {
        "directory_count": directory_count,
        "file_count": file_count,
    }


def drop_privileges(
    *,
    uid: int,
    gid: int,
) -> None:
    if os.geteuid() != 0:  # type: ignore[attr-defined]
        raise RuntimeBootstrapError(
            "root_required_before_privilege_drop"
        )

    try:
        os.setgroups([])  # type: ignore[attr-defined]
        os.setgid(gid)  # type: ignore[attr-defined]
        os.setuid(uid)  # type: ignore[attr-defined]
        os.umask(0o077)
    except OSError as exc:
        raise RuntimeBootstrapError(
            "privilege_drop_failed"
        ) from exc

    if (
        os.geteuid() != uid  # type: ignore[attr-defined]
        or os.getegid() != gid  # type: ignore[attr-defined]
    ):
        raise RuntimeBootstrapError(
            "privilege_drop_verification_failed"
        )

    os.environ["HOME"] = "/app"
    os.environ["USER"] = "smartcrypto"
    os.environ["LOGNAME"] = "smartcrypto"


def is_transient_filesystem_error(
    error: OSError,
) -> bool:
    windows_error = getattr(error, "winerror", None)

    return (
        isinstance(error, PermissionError)
        or error.errno in _TRANSIENT_FILESYSTEM_ERRNOS
        or windows_error in _TRANSIENT_WINDOWS_ERRORS
    )


def retry_delay_seconds(attempt: int) -> float:
    return (
        WRITABILITY_PROBE_BASE_DELAY_SECONDS
        * (2**attempt)
    )


def open_runtime_probe(
    directory: Path,
) -> BinaryIO:
    return cast(
        BinaryIO,
        tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=WRITABILITY_PROBE_PREFIX,
            suffix=WRITABILITY_PROBE_SUFFIX,
            dir=str(directory),
            delete=False,
        ),
    )


def cleanup_runtime_probe(
    handle: BinaryIO | None,
    probe_path: Path | None,
) -> OSError | None:
    errors: list[OSError] = []

    if handle is not None and not handle.closed:
        try:
            handle.close()
        except OSError as error:
            errors.append(error)

    if probe_path is not None:
        try:
            probe_path.unlink(missing_ok=True)
        except OSError as error:
            errors.append(error)

    if not errors:
        return None

    primary = errors[0]

    for secondary in errors[1:]:
        primary.add_note(
            f"Additional probe cleanup error: {secondary}"
        )

    return primary


def verify_runtime_directory_writability(
    directory: Path,
    *,
    open_probe: ProbeFactory | None = None,
    fsync: Fsync | None = None,
    sleep: Sleep | None = None,
) -> dict[str, int]:
    reject_symlink_components(directory)

    if not directory.is_dir():
        raise RuntimeBootstrapError(
            "runtime_writability_target_not_directory"
        )

    probe_factory = open_probe or open_runtime_probe
    fsync_call = fsync or os.fsync
    sleep_call = sleep or time.sleep

    for attempt in range(WRITABILITY_PROBE_ATTEMPTS):
        handle: BinaryIO | None = None
        probe_path: Path | None = None

        try:
            handle = probe_factory(directory)
            raw_name = getattr(handle, "name", None)

            if not isinstance(
                raw_name,
                (str, os.PathLike),
            ):
                raise RuntimeBootstrapError(
                    "runtime_writability_probe_path_invalid"
                )

            probe_path = Path(raw_name)

            if probe_path.parent != directory:
                raise RuntimeBootstrapError(
                    "runtime_writability_probe_outside_directory"
                )

            reject_symlink_components(probe_path)

            written = handle.write(
                WRITABILITY_PROBE_PAYLOAD
            )

            if written != len(
                WRITABILITY_PROBE_PAYLOAD
            ):
                raise RuntimeBootstrapError(
                    "runtime_writability_probe_short_write"
                )

            handle.flush()
            fsync_call(handle.fileno())
            handle.close()
            handle = None

            probe_path.unlink()
            probe_path = None

            return {
                "attempt_count": attempt + 1,
                "retry_count": attempt,
            }
        except (OSError, RuntimeBootstrapError) as error:
            cleanup_error = cleanup_runtime_probe(
                handle,
                probe_path,
            )

            if cleanup_error is not None:
                raise RuntimeBootstrapError(
                    "runtime_writability_probe_cleanup_failed"
                ) from cleanup_error

            if isinstance(
                error,
                RuntimeBootstrapError,
            ):
                raise

            is_last_attempt = (
                attempt + 1
                >= WRITABILITY_PROBE_ATTEMPTS
            )

            if (
                not is_transient_filesystem_error(
                    error
                )
                or is_last_attempt
            ):
                raise RuntimeBootstrapError(
                    "runtime_writability_probe_failed"
                ) from error

            sleep_call(
                retry_delay_seconds(attempt)
            )

    raise RuntimeBootstrapError(
        "runtime_writability_probe_retry_loop_exhausted"
    )


def verify_runtime_writability(
    profile: RuntimePermissionProfile,
) -> dict[str, int]:
    validate_profile_contract(profile)

    probe_attempt_count = 0
    probe_retry_count = 0

    for directory in profile.directories:
        result = verify_runtime_directory_writability(
            Path(directory)
        )
        probe_attempt_count += result["attempt_count"]
        probe_retry_count += result["retry_count"]

    return {
        "directory_count": len(profile.directories),
        "probe_attempt_count": probe_attempt_count,
        "probe_retry_count": probe_retry_count,
    }


def emit_event(
    event: str,
    *,
    service: str,
    **fields: int | bool | str,
) -> None:
    payload = {
        "event": event,
        "service": service,
        **fields,
        **SAFE_FLAGS,
    }

    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def exec_application(
    command: Sequence[str],
) -> NoReturn:
    if not command or not command[0]:
        raise RuntimeBootstrapError(
            "application_command_missing"
        )

    try:
        os.execvp(
            command[0],
            list(command),
        )
    except OSError as exc:
        raise RuntimeBootstrapError(
            "application_exec_failed"
        ) from exc

    raise RuntimeBootstrapError(
        "application_exec_returned_unexpectedly"
    )


def main(
    argv: list[str] | None = None,
) -> int:
    args = parse_args(
        list(
            argv
            if argv is not None
            else sys.argv[1:]
        )
    )
    profile = SERVICE_PROFILES[args.service]

    try:
        validate_requested_paths(
            profile,
            args.path,
        )

        summary = prepare_runtime_permissions(
            profile,
            uid=args.uid,
            gid=args.gid,
        )

        emit_event(
            "runtime_permissions_prepared",
            service=profile.service,
            **summary,
        )

        drop_privileges(
            uid=args.uid,
            gid=args.gid,
        )

        emit_event(
            "runtime_privileges_dropped",
            service=profile.service,
            effective_uid=args.uid,
            effective_gid=args.gid,
        )

        writability_summary = (
            verify_runtime_writability(
                profile
            )
        )

        emit_event(
            "runtime_writability_verified",
            service=profile.service,
            **writability_summary,
        )

        exec_application(args.command)
    except RuntimeBootstrapError as exc:
        emit_event(
            "runtime_permissions_bootstrap_blocked",
            service=profile.service,
            reason=str(exc),
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
