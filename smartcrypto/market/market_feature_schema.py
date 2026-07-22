from __future__ import annotations

import errno
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd


LOOKAHEAD_PREFIXES = ("future_ret_",)
DEFAULT_LABEL_KEYS = ("symbol", "pair", "tf", "ts", "ts_ms")

ATOMIC_TEMP_SUFFIX = ".tmp"
ATOMIC_REPLACE_ATTEMPTS = 5
ATOMIC_REPLACE_BASE_DELAY_SECONDS = 0.05

_TRANSIENT_REPLACE_ERRNOS = frozenset(
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

_PROMOTION_LOCK = threading.Lock()


def lookahead_columns(frame: pd.DataFrame) -> list[str]:
    return [
        str(column)
        for column in frame.columns
        if any(
            str(column).startswith(prefix)
            for prefix in LOOKAHEAD_PREFIXES
        )
    ]


def sanitize_operational_market_features(
    frame: pd.DataFrame,
    *,
    labels_output_path: str | Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Remove lookahead labels from operational market feature artifacts."""
    removed = lookahead_columns(frame)

    sanitized = (
        frame.drop(columns=removed).copy()
        if removed
        else frame.copy()
    )

    label_path = None

    if labels_output_path is not None and removed:
        label_path = write_market_feature_labels(
            frame,
            labels_output_path,
        )

    return sanitized, operational_schema_report(
        frame=sanitized,
        lookahead_columns_removed=removed,
        labels_output_path=label_path,
    )


def _create_atomic_temp_path(target: Path) -> Path:
    """
    Create an invocation-exclusive temporary file beside the destination.

    Keeping both files in the same directory preserves same-filesystem
    atomic promotion through os.replace.
    """
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=ATOMIC_TEMP_SUFFIX,
        dir=str(target.parent),
    )

    temporary = Path(raw_path)

    try:
        os.close(descriptor)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return temporary


def _is_transient_replace_error(error: OSError) -> bool:
    """Return whether a replace failure may be caused by a transient lock."""
    windows_error = getattr(error, "winerror", None)

    return (
        isinstance(error, PermissionError)
        or error.errno in _TRANSIENT_REPLACE_ERRNOS
        or windows_error in _TRANSIENT_WINDOWS_ERRORS
    )


def _replace_atomically_with_retry(
    source: Path,
    target: Path,
) -> None:
    """
    Promote a completed temporary artifact to its final destination.

    Parquet serialization remains concurrent. Only the final promotion is
    serialized inside this process. Bounded retry handles transient locks
    from Windows, antivirus software, indexing or Docker Desktop bind mounts.
    """
    with _PROMOTION_LOCK:
        for attempt in range(ATOMIC_REPLACE_ATTEMPTS):
            try:
                os.replace(source, target)
                return
            except OSError as error:
                is_last_attempt = (
                    attempt + 1
                    >= ATOMIC_REPLACE_ATTEMPTS
                )

                if (
                    not _is_transient_replace_error(error)
                    or is_last_attempt
                ):
                    raise

                delay_seconds = (
                    ATOMIC_REPLACE_BASE_DELAY_SECONDS
                    * (2**attempt)
                )

                time.sleep(delay_seconds)

    raise RuntimeError(
        "atomic_replace_retry_loop_exhausted"
    )


def _write_parquet_atomically(
    frame: pd.DataFrame,
    target: Path,
) -> None:
    """
    Write Parquet through an invocation-owned temporary artifact.

    Existing deterministic .tmp paths and temporary files belonging to
    other executions are never reused or deleted.
    """
    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = _create_atomic_temp_path(target)
    operation_error: BaseException | None = None

    try:
        frame.to_parquet(
            temporary,
            index=False,
        )

        _replace_atomically_with_retry(
            temporary,
            target,
        )
    except BaseException as error:
        operation_error = error
        raise
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as cleanup_error:
            if operation_error is None:
                raise

            operation_error.add_note(
                "Failed to remove invocation-owned temporary "
                f"Parquet file {temporary}: {cleanup_error}"
            )


def write_operational_market_features(
    frame: pd.DataFrame,
    output_path: str | Path,
    *,
    labels_output_path: str | Path | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Atomically write features after enforcing the no-lookahead contract."""
    sanitized, report = sanitize_operational_market_features(
        frame,
        labels_output_path=labels_output_path,
    )

    _write_parquet_atomically(
        sanitized,
        Path(output_path),
    )

    return sanitized, report


def write_market_feature_labels(
    frame: pd.DataFrame,
    output_path: str | Path,
) -> str:
    """Atomically write lookahead labels to their research artifact."""
    label_columns = lookahead_columns(frame)

    if not label_columns:
        return str(output_path)

    keys = [
        column
        for column in DEFAULT_LABEL_KEYS
        if column in frame.columns
    ]

    labels = frame[
        keys + label_columns
    ].copy()

    target = Path(output_path)

    _write_parquet_atomically(
        labels,
        target,
    )

    return str(target)


def operational_schema_report(
    *,
    frame: pd.DataFrame,
    lookahead_columns_removed: list[str] | None = None,
    labels_output_path: str | None = None,
) -> dict[str, Any]:
    current_lookahead = lookahead_columns(frame)
    removed = sorted(
        lookahead_columns_removed or []
    )

    return {
        "output_schema_status": (
            "ok"
            if not current_lookahead
            else "blocked"
        ),
        "operational_feature_schema_ok": (
            not current_lookahead
        ),
        "lookahead_columns": current_lookahead,
        "lookahead_columns_count": len(
            current_lookahead
        ),
        "lookahead_columns_removed": removed,
        "lookahead_columns_removed_count": len(
            removed
        ),
        "labels_output_path": labels_output_path,
    }
