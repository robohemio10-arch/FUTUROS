from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SHARED_SIGNAL_FILENAME = "active_freqtrade_signals.json"
SHARED_SIGNAL_DIRECTORY_MODE = 0o755
SHARED_SIGNAL_FILE_MODE = 0o644

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
    "changes_model": False,
}

Chmod = Callable[..., None]


class SharedFreqtradeSignalArtifactError(RuntimeError):
    """Fail-closed shared-artifact permission contract error."""


@dataclass(frozen=True)
class SharedFreqtradeSignalArtifactReport:
    status: str
    reason: str
    path: str
    exists: bool
    required: bool
    directory_mode: str | None
    file_mode: str | None
    signal_count: int
    consumer_readable: bool
    permission_changed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            **SAFE_FLAGS,
        }


def _octal_mode(value: int) -> str:
    return f"0o{stat.S_IMODE(value):03o}"


def _validate_contract_path(path: Path) -> None:
    if path.name != SHARED_SIGNAL_FILENAME:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_filename_invalid"
        )

    if path.parent.name != "runtime" or path.parent.parent.name != "data":
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_path_outside_data_runtime"
        )

    for candidate in (path.parent.parent, path.parent, path):
        try:
            if candidate.is_symlink():
                raise SharedFreqtradeSignalArtifactError(
                    "shared_signal_symlink_forbidden"
                )
        except OSError as exc:
            raise SharedFreqtradeSignalArtifactError(
                "shared_signal_path_metadata_unreadable"
            ) from exc


def _read_signal_count(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except PermissionError as exc:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_permission_denied_after_publish"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_payload_unreadable"
        ) from exc

    if not isinstance(payload, dict):
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_payload_not_object"
        )

    signals = payload.get("signals", [])
    if not isinstance(signals, list):
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_signals_not_list"
        )

    return sum(1 for item in signals if isinstance(item, dict))


def publish_shared_freqtrade_signal_artifact(
    path: str | os.PathLike[str],
    *,
    required: bool = False,
    chmod: Chmod = os.chmod,
) -> dict[str, Any]:
    """Publish one non-secret signal artifact for a read-only consumer.

    The producer retains ownership. Only the exact pinned Freqtrade signal
    artifact is made world-readable. The runtime directory becomes traversable
    and listable, while every other runtime file keeps its existing mode.
    """

    target = Path(path)
    _validate_contract_path(target)

    try:
        metadata = target.stat(follow_symlinks=False)
    except FileNotFoundError:
        if required:
            raise SharedFreqtradeSignalArtifactError(
                "shared_signal_required_file_missing"
            )
        return SharedFreqtradeSignalArtifactReport(
            status="not_present",
            reason="shared_signal_file_not_present",
            path=str(target),
            exists=False,
            required=required,
            directory_mode=None,
            file_mode=None,
            signal_count=0,
            consumer_readable=False,
            permission_changed=False,
        ).to_dict()
    except PermissionError as exc:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_permission_denied_before_publish"
        ) from exc
    except OSError as exc:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_metadata_unreadable"
        ) from exc

    if not stat.S_ISREG(metadata.st_mode):
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_not_regular_file"
        )
    if metadata.st_size <= 0:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_empty_file"
        )

    try:
        parent_metadata = target.parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_parent_metadata_unreadable"
        ) from exc

    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_parent_not_directory"
        )

    before_directory_mode = stat.S_IMODE(parent_metadata.st_mode)
    before_file_mode = stat.S_IMODE(metadata.st_mode)

    try:
        chmod(
            target.parent,
            SHARED_SIGNAL_DIRECTORY_MODE,
            follow_symlinks=False,
        )
        chmod(
            target,
            SHARED_SIGNAL_FILE_MODE,
            follow_symlinks=False,
        )
    except (NotImplementedError, TypeError):
        try:
            chmod(target.parent, SHARED_SIGNAL_DIRECTORY_MODE)
            chmod(target, SHARED_SIGNAL_FILE_MODE)
        except OSError as exc:
            raise SharedFreqtradeSignalArtifactError(
                "shared_signal_chmod_failed"
            ) from exc
    except OSError as exc:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_chmod_failed"
        ) from exc

    try:
        published_parent = target.parent.stat(follow_symlinks=False)
        published_file = target.stat(follow_symlinks=False)
    except OSError as exc:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_publish_verification_failed"
        ) from exc

    directory_mode = stat.S_IMODE(published_parent.st_mode)
    file_mode = stat.S_IMODE(published_file.st_mode)

    if directory_mode != SHARED_SIGNAL_DIRECTORY_MODE:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_directory_mode_mismatch"
        )
    if file_mode != SHARED_SIGNAL_FILE_MODE:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_file_mode_mismatch"
        )

    signal_count = _read_signal_count(target)
    consumer_readable = bool(
        directory_mode & stat.S_IXOTH
        and file_mode & stat.S_IROTH
    )
    if not consumer_readable:
        raise SharedFreqtradeSignalArtifactError(
            "shared_signal_consumer_readability_not_established"
        )

    permission_changed = (
        before_directory_mode != directory_mode
        or before_file_mode != file_mode
    )

    return SharedFreqtradeSignalArtifactReport(
        status="ok",
        reason="shared_signal_permission_contract_established",
        path=str(target),
        exists=True,
        required=required,
        directory_mode=_octal_mode(published_parent.st_mode),
        file_mode=_octal_mode(published_file.st_mode),
        signal_count=signal_count,
        consumer_readable=True,
        permission_changed=permission_changed,
    ).to_dict()
