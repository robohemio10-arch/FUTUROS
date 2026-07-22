from __future__ import annotations

import errno
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.market import market_feature_schema as schema


def _write_text_parquet(
    frame: pd.DataFrame,
    path: str | Path,
    *,
    index: bool,
) -> None:
    assert index is False
    Path(path).write_text(
        str(frame.iloc[0, 0]),
        encoding="utf-8",
    )


def test_legacy_deterministic_tmp_does_not_block_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "market_features_60d.parquet"
    legacy_tmp = target.with_suffix(
        target.suffix + ".tmp"
    )
    legacy_tmp.mkdir()

    sentinel = legacy_tmp / "sentinel.txt"
    sentinel.write_text(
        "preserve",
        encoding="utf-8",
    )

    observed_paths: list[Path] = []

    def recording_writer(
        frame: pd.DataFrame,
        path: str | Path,
        *,
        index: bool,
    ) -> None:
        observed_paths.append(Path(path))
        _write_text_parquet(
            frame,
            path,
            index=index,
        )

    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        recording_writer,
    )

    schema.write_operational_market_features(
        pd.DataFrame({"value": ["new"]}),
        target,
    )

    assert target.read_text(
        encoding="utf-8"
    ) == "new"
    assert sentinel.read_text(
        encoding="utf-8"
    ) == "preserve"
    assert len(observed_paths) == 1
    assert observed_paths[0] != legacy_tmp
    assert observed_paths[0].parent == target.parent
    assert not observed_paths[0].exists()


def test_failed_serialization_preserves_previous_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "market_features_60d.parquet"
    target.write_text(
        "previous",
        encoding="utf-8",
    )

    observed_temporary: Path | None = None

    def failing_writer(
        _frame: pd.DataFrame,
        path: str | Path,
        *,
        index: bool,
    ) -> None:
        nonlocal observed_temporary

        assert index is False
        observed_temporary = Path(path)
        observed_temporary.write_text(
            "partial",
            encoding="utf-8",
        )
        raise RuntimeError(
            "controlled serialization failure"
        )

    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        failing_writer,
    )

    with pytest.raises(
        RuntimeError,
        match="controlled serialization failure",
    ):
        schema.write_operational_market_features(
            pd.DataFrame(
                {"value": ["replacement"]}
            ),
            target,
        )

    assert target.read_text(
        encoding="utf-8"
    ) == "previous"
    assert observed_temporary is not None
    assert not observed_temporary.exists()


def test_concurrent_writers_use_distinct_temporaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "market_features_60d.parquet"
    barrier = threading.Barrier(2)

    observed_paths: list[Path] = []
    observed_lock = threading.Lock()

    def concurrent_writer(
        frame: pd.DataFrame,
        path: str | Path,
        *,
        index: bool,
    ) -> None:
        assert index is False
        temporary = Path(path)

        with observed_lock:
            observed_paths.append(temporary)

        barrier.wait(timeout=10)
        temporary.write_text(
            str(frame["payload"].iloc[0]),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        concurrent_writer,
    )

    frames = (
        pd.DataFrame({"payload": ["alpha"]}),
        pd.DataFrame({"payload": ["beta"]}),
    )

    with ThreadPoolExecutor(
        max_workers=2
    ) as executor:
        futures = [
            executor.submit(
                schema.write_operational_market_features,
                frame,
                target,
            )
            for frame in frames
        ]

        for future in futures:
            future.result(timeout=15)

    assert len(observed_paths) == 2
    assert len(set(observed_paths)) == 2
    assert all(
        not path.exists()
        for path in observed_paths
    )
    assert target.read_text(
        encoding="utf-8"
    ) in {"alpha", "beta"}


def test_transient_temp_create_permission_error_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "market_features_60d.parquet"
    real_mkstemp = tempfile.mkstemp

    create_count = 0
    observed_delays: list[float] = []

    def flaky_mkstemp(
        *,
        prefix: str,
        suffix: str,
        dir: str,
    ) -> tuple[int, str]:
        nonlocal create_count

        create_count += 1

        if create_count == 1:
            raise PermissionError(
                errno.EACCES,
                "controlled transient create denial",
            )

        return real_mkstemp(
            prefix=prefix,
            suffix=suffix,
            dir=dir,
        )

    monkeypatch.setattr(
        schema.tempfile,
        "mkstemp",
        flaky_mkstemp,
    )
    monkeypatch.setattr(
        schema.time,
        "sleep",
        observed_delays.append,
    )
    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        _write_text_parquet,
    )

    schema.write_operational_market_features(
        pd.DataFrame(
            {"value": ["create-retry-success"]}
        ),
        target,
    )

    assert create_count == 2
    assert observed_delays == [
        schema.ATOMIC_TEMP_CREATE_BASE_DELAY_SECONDS
    ]
    assert target.read_text(
        encoding="utf-8"
    ) == "create-retry-success"


def test_transient_temp_create_busy_error_uses_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "market_features_60d.parquet"
    real_mkstemp = tempfile.mkstemp

    create_count = 0
    observed_delays: list[float] = []

    def flaky_mkstemp(
        *,
        prefix: str,
        suffix: str,
        dir: str,
    ) -> tuple[int, str]:
        nonlocal create_count

        create_count += 1

        if create_count <= 2:
            raise OSError(
                errno.EBUSY,
                "controlled busy bind mount",
            )

        return real_mkstemp(
            prefix=prefix,
            suffix=suffix,
            dir=dir,
        )

    monkeypatch.setattr(
        schema.tempfile,
        "mkstemp",
        flaky_mkstemp,
    )
    monkeypatch.setattr(
        schema.time,
        "sleep",
        observed_delays.append,
    )
    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        _write_text_parquet,
    )

    schema.write_operational_market_features(
        pd.DataFrame(
            {"value": ["busy-retry-success"]}
        ),
        target,
    )

    assert create_count == 3
    assert observed_delays == [
        schema.ATOMIC_TEMP_CREATE_BASE_DELAY_SECONDS,
        (
            schema.ATOMIC_TEMP_CREATE_BASE_DELAY_SECONDS
            * 2
        ),
    ]


def test_permanent_temp_create_failure_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "market_features_60d.parquet"
    target.write_text(
        "previous",
        encoding="utf-8",
    )

    create_count = 0
    observed_delays: list[float] = []

    def permanent_failure(
        *,
        prefix: str,
        suffix: str,
        dir: str,
    ) -> tuple[int, str]:
        del prefix, suffix, dir

        nonlocal create_count
        create_count += 1

        raise OSError(
            errno.ENOSPC,
            "controlled permanent create failure",
        )

    monkeypatch.setattr(
        schema.tempfile,
        "mkstemp",
        permanent_failure,
    )
    monkeypatch.setattr(
        schema.time,
        "sleep",
        observed_delays.append,
    )

    with pytest.raises(
        OSError,
        match="controlled permanent create failure",
    ):
        schema.write_operational_market_features(
            pd.DataFrame(
                {"value": ["replacement"]}
            ),
            target,
        )

    assert create_count == 1
    assert observed_delays == []
    assert target.read_text(
        encoding="utf-8"
    ) == "previous"


def test_transient_temp_create_retry_exhaustion_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "market_features_60d.parquet"
    target.write_text(
        "previous",
        encoding="utf-8",
    )

    create_count = 0
    observed_delays: list[float] = []

    def always_denied(
        *,
        prefix: str,
        suffix: str,
        dir: str,
    ) -> tuple[int, str]:
        del prefix, suffix, dir

        nonlocal create_count
        create_count += 1

        raise PermissionError(
            errno.EACCES,
            "controlled persistent create denial",
        )

    monkeypatch.setattr(
        schema.tempfile,
        "mkstemp",
        always_denied,
    )
    monkeypatch.setattr(
        schema.time,
        "sleep",
        observed_delays.append,
    )

    with pytest.raises(
        PermissionError,
        match="controlled persistent create denial",
    ):
        schema.write_operational_market_features(
            pd.DataFrame(
                {"value": ["replacement"]}
            ),
            target,
        )

    assert (
        create_count
        == schema.ATOMIC_TEMP_CREATE_ATTEMPTS
    )
    assert len(observed_delays) == (
        schema.ATOMIC_TEMP_CREATE_ATTEMPTS - 1
    )
    assert target.read_text(
        encoding="utf-8"
    ) == "previous"
    assert list(
        tmp_path.glob(
            ".market_features_60d.parquet.*.tmp"
        )
    ) == []


def test_transient_replace_failure_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "market_features_60d.parquet"
    real_replace = os.replace

    replace_count = 0
    observed_delays: list[float] = []

    def flaky_replace(
        source: (
            str
            | bytes
            | os.PathLike[str]
            | os.PathLike[bytes]
        ),
        destination: (
            str
            | bytes
            | os.PathLike[str]
            | os.PathLike[bytes]
        ),
    ) -> None:
        nonlocal replace_count

        replace_count += 1

        if replace_count == 1:
            raise PermissionError(
                errno.EACCES,
                "controlled transient lock",
            )

        real_replace(
            source,
            destination,
        )

    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        _write_text_parquet,
    )
    monkeypatch.setattr(
        schema.os,
        "replace",
        flaky_replace,
    )
    monkeypatch.setattr(
        schema.time,
        "sleep",
        observed_delays.append,
    )

    schema.write_operational_market_features(
        pd.DataFrame(
            {"value": ["retry-success"]}
        ),
        target,
    )

    assert replace_count == 2
    assert observed_delays == [
        schema.ATOMIC_REPLACE_BASE_DELAY_SECONDS
    ]
    assert target.read_text(
        encoding="utf-8"
    ) == "retry-success"


def test_permanent_replace_failure_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "market_features_60d.parquet"
    target.write_text(
        "previous",
        encoding="utf-8",
    )

    replace_count = 0
    observed_delays: list[float] = []
    observed_temporary: Path | None = None

    def recording_writer(
        frame: pd.DataFrame,
        path: str | Path,
        *,
        index: bool,
    ) -> None:
        nonlocal observed_temporary

        observed_temporary = Path(path)
        _write_text_parquet(
            frame,
            path,
            index=index,
        )

    def permanent_failure(
        _source: (
            str
            | bytes
            | os.PathLike[str]
            | os.PathLike[bytes]
        ),
        _destination: (
            str
            | bytes
            | os.PathLike[str]
            | os.PathLike[bytes]
        ),
    ) -> None:
        nonlocal replace_count

        replace_count += 1
        raise OSError(
            errno.ENOSPC,
            "controlled permanent failure",
        )

    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        recording_writer,
    )
    monkeypatch.setattr(
        schema.os,
        "replace",
        permanent_failure,
    )
    monkeypatch.setattr(
        schema.time,
        "sleep",
        observed_delays.append,
    )

    with pytest.raises(
        OSError,
        match="controlled permanent failure",
    ):
        schema.write_operational_market_features(
            pd.DataFrame(
                {"value": ["replacement"]}
            ),
            target,
        )

    assert replace_count == 1
    assert observed_delays == []
    assert target.read_text(
        encoding="utf-8"
    ) == "previous"
    assert observed_temporary is not None
    assert not observed_temporary.exists()


def test_promotion_uses_same_destination_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "market_features_60d.parquet"
    real_replace = os.replace
    replace_calls: list[
        tuple[Path, Path]
    ] = []

    def recording_replace(
        source: (
            str
            | bytes
            | os.PathLike[str]
            | os.PathLike[bytes]
        ),
        destination: (
            str
            | bytes
            | os.PathLike[str]
            | os.PathLike[bytes]
        ),
    ) -> None:
        source_path = Path(source)
        destination_path = Path(destination)

        replace_calls.append(
            (
                source_path,
                destination_path,
            )
        )
        real_replace(
            source_path,
            destination_path,
        )

    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        _write_text_parquet,
    )
    monkeypatch.setattr(
        schema.os,
        "replace",
        recording_replace,
    )

    schema.write_operational_market_features(
        pd.DataFrame(
            {"value": ["atomic"]}
        ),
        target,
    )

    assert len(replace_calls) == 1

    source, destination = replace_calls[0]

    assert (
        source.parent
        == destination.parent
        == target.parent
    )
    assert destination == target
    assert not source.exists()


def test_label_writer_uses_atomic_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "labels.parquet"
    observed_columns: list[str] = []

    def labels_writer(
        frame: pd.DataFrame,
        path: str | Path,
        *,
        index: bool,
    ) -> None:
        assert index is False
        observed_columns.extend(
            str(column)
            for column in frame.columns
        )
        Path(path).write_text(
            "labels",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        labels_writer,
    )

    result = schema.write_market_feature_labels(
        pd.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "feature_rsi": [55.0],
                "future_ret_5m": [0.01],
            }
        ),
        target,
    )

    assert result == str(target)
    assert observed_columns == [
        "symbol",
        "future_ret_5m",
    ]
    assert target.read_text(
        encoding="utf-8"
    ) == "labels"


def test_no_lookahead_contract_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "market_features_60d.parquet"
    observed_columns: list[str] = []

    def feature_writer(
        frame: pd.DataFrame,
        path: str | Path,
        *,
        index: bool,
    ) -> None:
        assert index is False
        observed_columns.extend(
            str(column)
            for column in frame.columns
        )
        Path(path).write_text(
            "features",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        feature_writer,
    )

    sanitized, report = (
        schema.write_operational_market_features(
            pd.DataFrame(
                {
                    "symbol": ["ETHUSDT"],
                    "feature_rsi": [45.0],
                    "future_ret_5m": [-0.01],
                }
            ),
            target,
        )
    )

    assert sanitized.columns.tolist() == [
        "symbol",
        "feature_rsi",
    ]
    assert observed_columns == [
        "symbol",
        "feature_rsi",
    ]
    assert (
        report["operational_feature_schema_ok"]
        is True
    )
    assert report[
        "lookahead_columns_removed"
    ] == ["future_ret_5m"]
