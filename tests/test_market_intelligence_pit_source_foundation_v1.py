from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from smartcrypto.research.aibot_parity import market_intelligence_pit_source_foundation as pit

START = datetime(2026, 1, 21, 12, tzinfo=timezone.utc)
SPOT = "spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-01-21.zip"
PERP = "futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-01-21.zip"
FUNDING = "futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-01.zip"


def _row(*, spot: bool, price: str = "100", opened: datetime = START) -> list[str]:
    scale = 1_000_000 if spot else 1000
    first = int(opened.timestamp()) * scale
    return [
        str(first),
        "100",
        "100",
        "100",
        price,
        "1",
        str(first + 60 * scale - 1),
        "1",
        "1",
        "1",
        "1",
        "0",
    ]


def _basis() -> pit.PITObservation:
    return pit.basis_observations(
        "BTCUSDT",
        [_row(spot=True)],
        [_row(spot=False, price="101")],
        SPOT,
        PERP,
        "a" * 64,
        "b" * 64,
        {int(START.timestamp() * 1000)},
    )[0]


def _funding() -> pit.PITObservation:
    return pit.funding_observations(
        "BTCUSDT",
        [
            ["calc_time", "funding_interval_hours", "last_funding_rate"],
            [str(int(START.timestamp() * 1000)), "8", "-0.0002"],
        ],
        FUNDING,
        "c" * 64,
    )[0]


def _dataset(time: datetime) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "open_time_utc": time,
                "feature_cutoff_utc": time - timedelta(minutes=1),
            }
        ]
    )


def test_basis_real_synchronized_prices_and_timestamp_units() -> None:
    record = _basis()
    assert record.value == pytest.approx(100.0)
    assert record.event_time_utc == START + timedelta(minutes=1)
    assert record.available_at_utc == START + timedelta(minutes=2)
    assert record.availability_is_modeled is True
    assert len(record.provenance) == 2
    assert record.provenance[0].archive_sha256 == "a" * 64
    assert pit.PITObservation.model_validate(record.model_dump(mode="json")) == record


def test_unsynchronized_missing_leg_cannot_create_basis() -> None:
    assert (
        pit.basis_observations(
            "BTCUSDT",
            [_row(spot=True)],
            [_row(spot=False, opened=START + timedelta(minutes=1))],
            SPOT,
            PERP,
            "a" * 64,
            "b" * 64,
            {int(START.timestamp() * 1000)},
        )
        == []
    )


def test_unclosed_candle_is_rejected() -> None:
    bad = _row(spot=True)
    bad[6] = bad[0]
    with pytest.raises(pit.PITSourceError, match="interval_not_closed"):
        pit.basis_observations(
            "BTCUSDT",
            [bad],
            [_row(spot=False)],
            SPOT,
            PERP,
            "a" * 64,
            "b" * 64,
            {int(START.timestamp() * 1000)},
        )


def test_settled_funding_is_real_not_predicted_and_has_expiry() -> None:
    record = _funding()
    assert record.value == -0.0002
    assert record.available_at_utc == START + timedelta(minutes=5)
    assert record.max_age_seconds == 8 * 3600 + 300
    _, coverage = pit.align_observations(_dataset(START + timedelta(hours=9)), [record])
    assert coverage["FUNDING"]["by_symbol"]["BTCUSDT"]["stale_count"] == 1


@pytest.mark.parametrize("family", ["BASIS", "FUNDING"])
def test_future_availability_excluded_and_boundary_included(family: str) -> None:
    record = _basis() if family == "BASIS" else _funding()
    frame, coverage = pit.align_observations(
        _dataset(record.available_at_utc - timedelta(microseconds=1)), [record]
    )
    assert coverage[family]["available_count"] == 0
    assert pd.isna(frame.iloc[0][pit.FEATURES[family]])
    frame, coverage = pit.align_observations(_dataset(record.available_at_utc), [record])
    assert coverage[family]["available_count"] == 1
    assert frame.iloc[0][pit.FEATURES[family]] == record.value
    assert coverage[family]["future_join_count"] == 0


def test_previous_settlement_not_future_rate_is_used() -> None:
    previous = _funding()
    fields = previous.model_dump(exclude={"observation_id"})
    fields["provenance"] = previous.provenance
    fields.update(
        event_time_utc=START + timedelta(hours=8),
        available_at_utc=START + timedelta(hours=8, minutes=5),
        value=0.009,
    )
    future = pit.observation(**fields)
    frame, _ = pit.align_observations(
        _dataset(START + timedelta(hours=8, minutes=2)), [previous, future]
    )
    assert frame.iloc[0][pit.FEATURES["FUNDING"]] == previous.value


def test_duplicate_identity_and_hash_tampering_fail_closed() -> None:
    record = _basis()
    with pytest.raises(pit.PITSourceError, match="identity_collision"):
        pit.align_observations(_dataset(record.available_at_utc), [record, record])
    payload = record.model_dump(mode="json")
    payload["value"] = 123.0
    with pytest.raises(ValidationError, match="hash_mismatch"):
        pit.PITObservation.model_validate(payload)
    payload["realized_pnl"] = 1.0
    with pytest.raises(ValidationError):
        pit.PITObservation.model_validate(payload)


def test_earlier_availability_and_invalid_source_rejected() -> None:
    payload = _basis().model_dump(mode="json")
    payload["available_at_utc"] = START.isoformat()
    with pytest.raises(ValidationError, match="availability_delay_mismatch"):
        pit.PITObservation.model_validate(payload)
    with pytest.raises(ValidationError, match="unauthorized_public_source"):
        pit.Provenance(
            url="https://private.invalid/x.zip", archive_sha256="a" * 64, row_sha256="b" * 64
        )


def _zip() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("observations.csv", "a,b\n1,2\n")
    return stream.getvalue()


def test_checksum_verified_cache_is_idempotent_and_offline(tmp_path: Path) -> None:
    raw = _zip()
    calls = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return (
            (pit.sha256(raw) + "  " + Path(SPOT).name).encode()
            if url.endswith(".CHECKSUM")
            else raw
        )

    first = pit.archive_rows(SPOT, tmp_path, allow_download=True, fetch=fetch)
    assert first == ([["a", "b"], ["1", "2"]], pit.sha256(raw))
    assert len(calls) == 2
    assert pit.archive_rows(SPOT, tmp_path, allow_download=False, fetch=fetch) == first
    assert len(calls) == 2
    (tmp_path / SPOT).write_bytes(raw + b"drift")
    with pytest.raises(pit.PITSourceError, match="checksum_mismatch"):
        pit.archive_rows(SPOT, tmp_path, allow_download=False, fetch=fetch)


def test_invalid_download_checksum_never_materializes(tmp_path: Path) -> None:
    def fetch(url: str) -> bytes:
        return ("0" * 64 + "  " + Path(SPOT).name).encode() if url.endswith(".CHECKSUM") else _zip()

    with pytest.raises(pit.PITSourceError, match="checksum_mismatch"):
        pit.archive_rows(SPOT, tmp_path, allow_download=True, fetch=fetch)
    assert list(tmp_path.iterdir()) == []


def test_no_write_missing_cache_never_downloads(tmp_path: Path) -> None:
    def forbidden(_: str) -> bytes:
        raise AssertionError("network called in no-write")

    with pytest.raises(pit.PITSourceError, match="SOURCE_CACHE_MISSING"):
        pit.archive_rows(SPOT, tmp_path, allow_download=False, fetch=forbidden)
    assert list(tmp_path.iterdir()) == []


def test_external_atomic_write_readback_and_conflict(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    with pytest.raises(pit.PITSourceError, match="git_worktree"):
        pit.external_root(project / "outputs", project)
    with pytest.raises(pit.PITSourceError, match="absolute"):
        pit.external_root(Path("relative"), project)
    output = pit.external_root(tmp_path / "external", project)
    target = output / "snapshot.json"
    pit.write_once(target, b"exact\n")
    pit.write_once(target, b"exact\n")
    assert target.read_bytes() == b"exact\n"
    assert list(output.iterdir()) == [target]
    with pytest.raises(pit.PITSourceError, match="materialization_conflict"):
        pit.write_once(target, b"different\n")


def test_builder_sparse_windows_cache_reuse_and_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _dataset(START + timedelta(minutes=3))
    monkeypatch.setattr(
        pit,
        "_load_bundle",
        lambda **_: (dataset, (), [{"split_id": "frozen"}], {"dataset_hash": "frozen"}),
    )
    requests: list[str] = []

    def archive(relative: str, _: Path, *, allow_download: bool) -> tuple[list[list[str]], str]:
        requests.append(relative)
        if "fundingRate" in relative:
            return [
                ["calc_time", "funding_interval_hours", "last_funding_rate"],
                [str(int(START.timestamp() * 1000)), "8", "0.0001"],
            ], "a" * 64
        return [_row(spot=relative.startswith("spot/"))], "b" * 64

    monkeypatch.setattr(pit, "archive_rows", archive)
    output = tmp_path / "external"
    report = pit.build_source_foundation(
        project_root=tmp_path / "project", output_root=output, write=True
    )
    assert report["archive_count"] == 3
    assert report["coverage"]["BASIS"]["available_count"] == 1
    assert report["coverage"]["FLOW"]["source_status"] == "SOURCE_UNAVAILABLE"
    raw = Path(report["observations_path"]).read_bytes()
    assert pit.sha256(raw) == report["observations_sha256"]
    assert not any("aggTrades" in value for value in requests)
    offline = pit.build_source_foundation(
        project_root=tmp_path / "project", output_root=output, write=False
    )
    assert offline["write_performed"] is False
    assert offline["observations_sha256"] == report["observations_sha256"]
    manifest = json.loads((output / "market_intelligence_pit_source_manifest_v1.json").read_bytes())
    assert manifest["historical_reception_proven"] is False
